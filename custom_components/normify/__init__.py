"""The Normify integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.sensor.const import (
    CONF_STATE_CLASS,
    DEVICE_CLASSES_SCHEMA,
    STATE_CLASSES_SCHEMA,
)
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .calibration import InvalidDataPointsError, PolynomialCalibration
from .const import (
    CONF_DATA_POINTS,
    CONF_DEGREE,
    CONF_HIDE_SOURCE,
    CONF_PRECISION,
    DEFAULT_DEGREE,
    DEFAULT_PRECISION,
    DOMAIN,
    MAX_DEGREE,
    PLATFORMS,
)


def _validate_calibration(value: dict[str, Any]) -> dict[str, Any]:
    """Validate that calibration points can define the requested polynomial."""
    if value.get(CONF_ATTRIBUTE) and value.get(CONF_HIDE_SOURCE):
        raise vol.Invalid("attribute and hide_source cannot be used together")

    try:
        PolynomialCalibration.fit(
            value[CONF_DATA_POINTS],
            degree=value[CONF_DEGREE],
            precision=value[CONF_PRECISION],
        )
    except InvalidDataPointsError as err:
        raise vol.Invalid(str(err)) from err

    return value


NORMIFY_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(CONF_SOURCE): cv.entity_id,
            vol.Optional(CONF_ATTRIBUTE): cv.string,
            vol.Optional(CONF_HIDE_SOURCE, default=False): cv.boolean,
            vol.Optional(CONF_NAME): cv.string,
            vol.Optional(CONF_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
            vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
            vol.Optional(CONF_STATE_CLASS): STATE_CLASSES_SCHEMA,
            vol.Required(CONF_DATA_POINTS): [
                vol.ExactSequence([vol.Coerce(float), vol.Coerce(float)])
            ],
            vol.Optional(CONF_DEGREE, default=DEFAULT_DEGREE): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_DEGREE)
            ),
            vol.Optional(CONF_PRECISION, default=DEFAULT_PRECISION): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=12)
            ),
        }
    ),
    _validate_calibration,
)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({cv.slug: NORMIFY_SCHEMA})}, extra=vol.ALLOW_EXTRA
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import legacy-style YAML definitions into config entries."""
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
