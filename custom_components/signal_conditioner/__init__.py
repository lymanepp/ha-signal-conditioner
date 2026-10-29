"""The Signal Conditioner integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_DEVICE_CLASS,
    CONF_ICON,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
    SERVICE_RELOAD,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, reload as reload_helper
from homeassistant.helpers.issue_registry import async_delete_issue
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType

try:
    # Home Assistant replaced voluptuous with probatio, an API-compatible
    # drop-in (2026.10 depends on probatio; 2026.8 and earlier, including the
    # 2026.7.2 minimum in hacs.json, only on voluptuous).
    import probatio as vol
except ImportError:
    import voluptuous as vol  # type: ignore[no-redef]

from .config import pipeline_config_from_data
from .const import (
    CONF_AVAILABILITY,
    CONF_CALIBRATION,
    CONF_DATA_POINTS,
    CONF_DEGREE,
    CONF_DURATION,
    CONF_FOLLOW_SOURCE,
    CONF_HIDE_SOURCE,
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_MODE,
    CONF_OUTPUT,
    CONF_PRECISION,
    CONF_ROUNDING,
    CONF_STALE_AFTER,
    CONF_STATE_CLASS,
    CONF_VALUE_LIMITS,
    CONF_WINDOW,
    DEFAULT_DEGREE,
    DEFAULT_FOLLOW_SOURCE,
    DEFAULT_PRECISION,
    DOMAIN,
    LIMIT_MODE_CLAMP,
    LIMIT_MODE_REJECT,
    MAX_DEGREE,
    PLATFORMS,
    WINDOW_OUTPUT_LATEST,
    WINDOW_OUTPUT_MEAN,
)
from .pipeline import MAX_WINDOW_SECONDS, PipelineConfigurationError
from .source_health import source_issue_id

_LOGGER = logging.getLogger(__name__)

EVENT_YAML_RELOADED = f"event_{DOMAIN}_reloaded"
_YAML_RUNTIME = "yaml_runtime"


@dataclass(slots=True)
class YamlRuntime:
    """Protect YAML reconciliation and track startup imports."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    startup_tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    initialized: bool = False


def _yaml_runtime(hass: HomeAssistant) -> YamlRuntime:
    return hass.data.setdefault(DOMAIN, {}).setdefault(_YAML_RUNTIME, YamlRuntime())


def _validate_signal_conditioner(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one concise conditioning configuration."""
    if value.get(CONF_ATTRIBUTE) and value.get(CONF_HIDE_SOURCE):
        raise vol.Invalid("attribute and hide_source cannot be used together")
    try:
        pipeline_config_from_data(value)
    except PipelineConfigurationError as err:
        raise vol.Invalid(str(err)) from err
    return value


_positive_window = vol.All(
    vol.Coerce(float), vol.Range(min=0.001, max=MAX_WINDOW_SECONDS)
)

VALUE_LIMITS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MINIMUM): vol.Coerce(float),
        vol.Optional(CONF_MAXIMUM): vol.Coerce(float),
        vol.Optional(CONF_MODE, default=LIMIT_MODE_REJECT): vol.In(
            [LIMIT_MODE_REJECT, LIMIT_MODE_CLAMP]
        ),
    }
)
CALIBRATION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DATA_POINTS): [
            vol.ExactSequence([vol.Coerce(float), vol.Coerce(float)])
        ],
        vol.Optional(CONF_DEGREE, default=DEFAULT_DEGREE): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_DEGREE)
        ),
    }
)
WINDOW_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DURATION): _positive_window,
        vol.Optional(CONF_OUTPUT, default=WINDOW_OUTPUT_MEAN): vol.In(
            [WINDOW_OUTPUT_MEAN, WINDOW_OUTPUT_LATEST]
        ),
    }
)
AVAILABILITY_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_FOLLOW_SOURCE, default=DEFAULT_FOLLOW_SOURCE): cv.boolean,
        vol.Optional(CONF_STALE_AFTER): _positive_window,
    }
)
ROUNDING_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PRECISION, default=DEFAULT_PRECISION): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=12)
        )
    }
)

SIGNAL_CONDITIONER_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(CONF_SOURCE): cv.entity_id,
            vol.Optional(CONF_ATTRIBUTE): cv.string,
            vol.Optional(CONF_HIDE_SOURCE, default=False): cv.boolean,
            vol.Optional(CONF_NAME): cv.string,
            vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
            vol.Optional(CONF_DEVICE_CLASS): vol.In(
                [device_class.value for device_class in SensorDeviceClass]
            ),
            vol.Optional(CONF_STATE_CLASS): vol.In(
                [state_class.value for state_class in SensorStateClass]
            ),
            vol.Optional(CONF_ICON): cv.icon,
            vol.Optional(CONF_VALUE_LIMITS): VALUE_LIMITS_SCHEMA,
            vol.Optional(CONF_CALIBRATION): CALIBRATION_SCHEMA,
            vol.Optional(CONF_WINDOW): WINDOW_SCHEMA,
            vol.Optional(CONF_ROUNDING): ROUNDING_SCHEMA,
            vol.Optional(CONF_AVAILABILITY): AVAILABILITY_SCHEMA,
        }
    ),
    _validate_signal_conditioner,
)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({cv.slug: SIGNAL_CONDITIONER_SCHEMA})}, extra=vol.ALLOW_EXTRA
)


def _yaml_entry_data(yaml_config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Prepare all YAML definitions before any entry is changed."""
    prepared: dict[str, dict[str, Any]] = {}
    for object_id, raw_config in yaml_config.items():
        data = dict(raw_config)
        data[CONF_UNIQUE_ID] = object_id
        data.setdefault(CONF_NAME, object_id.replace("_", " ").title())
        prepared[object_id] = data
    return prepared


async def _async_sync_yaml_entries(
    hass: HomeAssistant, yaml_config: Mapping[str, Any]
) -> None:
    """Reconcile YAML-owned entries without touching UI-owned entries."""
    prepared = _yaml_entry_data(yaml_config)

    # Remove old entries first, so renaming a YAML key does not temporarily
    # create two entities (or two owners of the same hidden source).
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.source == SOURCE_IMPORT and entry.unique_id not in prepared:
            _LOGGER.info(
                "Removing Signal Conditioner '%s': its YAML key was removed",
                entry.unique_id,
            )
            await hass.config_entries.async_remove(entry.entry_id)

    for entry_data in prepared.values():
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=entry_data
        )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import YAML entries and register a live, admin-only YAML reload action."""
    runtime = _yaml_runtime(hass)
    if runtime.initialized:
        return True
    runtime.initialized = True

    # Do not await config-entry imports in the integration's initial setup:
    # a created entry may need to finish setting up this same integration.
    # The reload action awaits these startup operations before reconciling.
    runtime.startup_tasks.append(
        hass.async_create_task(_async_sync_yaml_entries(hass, config.get(DOMAIN, {})))
    )

    async def async_reload_yaml(call: ServiceCall) -> None:
        """Reload and validate YAML before changing any existing entries."""
        async with runtime.lock:
            reloaded_config = await reload_helper.async_integration_yaml_config(
                hass, DOMAIN, raise_on_failure=True
            )
            # Validate everything before adding, changing, or removing entries.
            yaml_config = CONFIG_SCHEMA({DOMAIN: reloaded_config.get(DOMAIN, {})})[
                DOMAIN
            ]

            startup_tasks, runtime.startup_tasks = runtime.startup_tasks, []
            if startup_tasks:
                await asyncio.gather(*startup_tasks)
            await _async_sync_yaml_entries(hass, yaml_config)

        hass.bus.async_fire(EVENT_YAML_RELOADED, context=call.context)

    async_register_admin_service(
        hass, DOMAIN, SERVICE_RELOAD, async_reload_yaml, schema={}
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Signal Conditioner from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Signal Conditioner config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove any lingering Repairs issue for an entry that was deleted."""
    async_delete_issue(hass, DOMAIN, source_issue_id(entry.entry_id))


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload Signal Conditioner when its config entry changes."""
    await hass.config_entries.async_reload(entry.entry_id)
