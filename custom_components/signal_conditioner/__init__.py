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
    MAJOR_VERSION,
    MINOR_VERSION,
    SERVICE_RELOAD,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    reload as reload_helper,
)
from homeassistant.helpers.device import async_entity_id_to_device_id
from homeassistant.helpers.helper_integration import async_handle_source_entity_changes
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
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
from .source_hiding import async_release_entry_source

_LOGGER = logging.getLogger(__name__)

EVENT_YAML_RELOADED = f"event_{DOMAIN}_reloaded"
_YAML_RUNTIME = "yaml_runtime"

# Before 2026.8, async_handle_source_entity_changes defaults to adding the
# helper's config entry to the source device; HA's own helpers opt out. From
# 2026.8 the option has no effect and passing it logs a deprecation.
_SOURCE_CHANGES_KWARGS: dict[str, Any] = (
    {"add_helper_config_entry_to_device": False}
    if (MAJOR_VERSION, MINOR_VERSION) < (2026, 8)
    else {}
)


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

# Strict schema for the whole YAML block, used to validate each key.
YAML_CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({cv.slug: SIGNAL_CONDITIONER_SCHEMA})}, extra=vol.ALLOW_EXTRA
)

# What Home Assistant validates before async_setup. Deliberately lenient: a
# failure here would block the whole integration, UI-created entries included.
# Each YAML key is validated on its own in _validate_yaml instead.
CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): object}, extra=vol.ALLOW_EXTRA)

_INVALID_YAML_ISSUE_ID = "invalid_yaml"


@dataclass(slots=True)
class ValidatedYaml:
    """Per-key YAML validation result."""

    valid: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Key -> error. None as the key means the whole block is unusable.
    invalid: dict[str | None, str] = field(default_factory=dict)


def _validate_yaml(raw: object) -> ValidatedYaml:
    """Validate each YAML key on its own, so one mistake cannot hide the rest."""
    result = ValidatedYaml()
    if raw is None:
        return result
    if not isinstance(raw, Mapping):
        result.invalid[None] = f"expected a mapping of keys, got {type(raw).__name__}"
        return result
    for key, value in raw.items():
        try:
            object_id = cv.slug(key)
            result.valid[object_id] = SIGNAL_CONDITIONER_SCHEMA(value)
        except vol.Invalid as err:
            result.invalid[str(key)] = str(err)
    return result


@callback
def _async_report_invalid_yaml(hass: HomeAssistant, yaml: ValidatedYaml) -> None:
    """Log invalid YAML keys and show them in Repairs; clear when all valid."""
    if not yaml.invalid:
        async_delete_issue(hass, DOMAIN, _INVALID_YAML_ISSUE_ID)
        return
    for key, error in yaml.invalid.items():
        _LOGGER.error(
            "Invalid Signal Conditioner YAML %s: %s. Its existing sensor, if "
            "any, keeps its previous configuration",
            f"key '{key}'" if key is not None else "configuration",
            error,
        )
    async_create_issue(
        hass,
        DOMAIN,
        _INVALID_YAML_ISSUE_ID,
        is_fixable=False,
        is_persistent=False,
        severity=IssueSeverity.ERROR,
        translation_key=_INVALID_YAML_ISSUE_ID,
        translation_placeholders={
            "keys": ", ".join(
                key if key is not None else DOMAIN for key in yaml.invalid
            )
        },
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


async def _async_sync_yaml_entries(hass: HomeAssistant, yaml: ValidatedYaml) -> None:
    """Reconcile YAML-owned entries without touching UI-owned entries.

    Entries of invalid keys are left as they are: an invalid key is not a
    removed key, and removing it would delete the sensor and its history.
    """
    if None in yaml.invalid:
        return  # The whole block is unusable: change nothing.
    prepared = _yaml_entry_data(yaml.valid)

    # Remove old entries first, so renaming a YAML key does not temporarily
    # create two entities (or two owners of the same hidden source).
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (
            entry.source == SOURCE_IMPORT
            and entry.unique_id not in prepared
            and entry.unique_id not in yaml.invalid
        ):
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
    yaml = _validate_yaml(config.get(DOMAIN))
    _async_report_invalid_yaml(hass, yaml)
    runtime.startup_tasks.append(
        hass.async_create_task(_async_sync_yaml_entries(hass, yaml))
    )

    async def async_reload_yaml(call: ServiceCall) -> None:
        """Reload and validate YAML before changing any existing entries."""
        async with runtime.lock:
            reloaded_config = await reload_helper.async_integration_yaml_config(
                hass, DOMAIN, raise_on_failure=True
            )
            # Validate everything before adding, changing, or removing
            # entries: a reload is all-or-nothing, and reports what failed.
            yaml = _validate_yaml(reloaded_config.get(DOMAIN))
            _async_report_invalid_yaml(hass, yaml)
            if yaml.invalid:
                raise vol.Invalid(
                    "Invalid Signal Conditioner YAML, nothing was changed: "
                    + "; ".join(
                        f"{key if key is not None else DOMAIN}: {error}"
                        for key, error in yaml.invalid.items()
                    )
                )

            startup_tasks, runtime.startup_tasks = runtime.startup_tasks, []
            if startup_tasks:
                await asyncio.gather(*startup_tasks)
            await _async_sync_yaml_entries(hass, yaml)

        hass.bus.async_fire(EVENT_YAML_RELOADED, context=call.context)

    async_register_admin_service(
        hass, DOMAIN, SERVICE_RELOAD, async_reload_yaml, schema=vol.Schema({})
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Signal Conditioner from a config entry."""
    if entry.source != SOURCE_IMPORT:
        # Follow source renames and device moves. YAML entries can't: the
        # next import would put the old entity ID back.
        def set_source_entity_id(source_entity_id: str) -> None:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_SOURCE: source_entity_id}
            )
            hass.config_entries.async_schedule_reload(entry.entry_id)

        entry.async_on_unload(
            async_handle_source_entity_changes(
                hass,
                helper_config_entry_id=entry.entry_id,
                set_source_entity_id_or_uuid=set_source_entity_id,
                source_device_id=async_entity_id_to_device_id(
                    hass, entry.data[CONF_SOURCE]
                ),
                source_entity_id_or_uuid=entry.data[CONF_SOURCE],
                **_SOURCE_CHANGES_KWARGS,
            )
        )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an entry to the current config entry version."""
    if entry.version > 1:
        return False  # Downgraded from a future version.

    if entry.minor_version < 1:
        # 1.1: UI entries no longer use the source entity ID as unique ID, so
        # one source can have several conditioners. Keep the entity, its
        # entity ID, and its history by moving it to the entry-ID based
        # unique ID that the sensor derives when the entry has none.
        if entry.source != SOURCE_IMPORT and entry.unique_id is not None:
            old_unique_id = f"{DOMAIN}.{entry.unique_id}"
            new_unique_id = f"{DOMAIN}.{entry.entry_id}"

            @callback
            def migrate_unique_id(
                entity_entry: er.RegistryEntry,
            ) -> dict[str, Any] | None:
                if entity_entry.unique_id == old_unique_id:
                    return {"new_unique_id": new_unique_id}
                return None

            await er.async_migrate_entries(hass, entry.entry_id, migrate_unique_id)
            hass.config_entries.async_update_entry(entry, unique_id=None)
        hass.config_entries.async_update_entry(entry, minor_version=1)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Signal Conditioner config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # A plain unload (reload, shutdown) keeps the source hidden; a disabled
    # conditioner should not keep its source out of sight.
    if unloaded and entry.disabled_by is not None:
        async_release_entry_source(hass, entry)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up the Repairs issue and source hiding of a deleted entry."""
    async_delete_issue(hass, DOMAIN, source_issue_id(entry.entry_id))
    async_release_entry_source(hass, entry)
