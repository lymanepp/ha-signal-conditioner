"""The Normify integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
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
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .config import flatten_configuration, pipeline_config_from_data
from .const import (
    CONF_CALIBRATION,
    CONF_DATA_POINTS,
    CONF_DEGREE,
    CONF_DURATION,
    CONF_HIDE_SOURCE,
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_OUTPUT,
    CONF_PRECISION,
    CONF_REJECT_VALUES,
    CONF_ROUNDING,
    CONF_STATE_CLASS,
    CONF_VALUE_LIMITS,
    CONF_WINDOW,
    DEFAULT_DEGREE,
    DEFAULT_PRECISION,
    DOMAIN,
    MAX_DEGREE,
    MAX_WINDOW_SECONDS,
    PLATFORMS,
    WINDOW_OUTPUT_LATEST,
    WINDOW_OUTPUT_MEAN,
)
from .pipeline import PipelineConfigurationError


def _validate_normify(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and flatten one concise conditioning configuration."""
    if value.get(CONF_ATTRIBUTE) and value.get(CONF_HIDE_SOURCE):
        raise vol.Invalid("attribute and hide_source cannot be used together")
    flat = flatten_configuration(value)
    try:
        pipeline_config_from_data(flat)
    except PipelineConfigurationError as err:
        raise vol.Invalid(str(err)) from err
    return flat


_positive_window = vol.All(
    vol.Coerce(float), vol.Range(min=0.001, max=MAX_WINDOW_SECONDS)
)

VALUE_LIMITS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MINIMUM): vol.Coerce(float),
        vol.Optional(CONF_MAXIMUM): vol.Coerce(float),
        vol.Optional(CONF_REJECT_VALUES): [vol.Coerce(float)],
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
ROUNDING_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PRECISION, default=DEFAULT_PRECISION): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=12)
        )
    }
)

NORMIFY_SCHEMA = vol.All(
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
        }
    ),
    _validate_normify,
)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({cv.slug: NORMIFY_SCHEMA})}, extra=vol.ALLOW_EXTRA
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import YAML definitions into config entries."""
    for object_id, raw_config in config.get(DOMAIN, {}).items():
        entry_data = dict(raw_config)
        entry_data[CONF_UNIQUE_ID] = object_id
        entry_data.setdefault(CONF_NAME, object_id.replace("_", " ").title())
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=entry_data,
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Normify from a config entry."""
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Normify config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload Normify when its config entry changes."""
    await hass.config_entries.async_reload(entry.entry_id)
