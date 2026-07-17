"""Tests for the Normify config flow."""

from homeassistant import config_entries
from homeassistant.const import CONF_NAME, CONF_SOURCE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.normify.const import (
    CONF_DATA_POINTS,
    CONF_DATA_POINTS_TEXT,
    CONF_DEGREE,
    CONF_HIDE_SOURCE,
    CONF_PRECISION,
    DOMAIN,
)


async def test_user_flow(hass: HomeAssistant) -> None:
    """Create a Normify helper through the UI."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Garage humidity",
            CONF_SOURCE: "sensor.garage_humidity_raw",
            "attribute": "",
            CONF_DATA_POINTS_TEXT: "38.68, 32\n79.89, 75",
            CONF_DEGREE: 1,
            CONF_PRECISION: 2,
            "unit_of_measurement": "%",
            "device_class": "humidity",
            "state_class": "measurement",
            CONF_HIDE_SOURCE: False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage humidity"
    assert result["data"][CONF_DATA_POINTS] == [[38.68, 32.0], [79.89, 75.0]]


async def test_invalid_calibration(hass: HomeAssistant) -> None:
    """Reject a degree that cannot be supported by the points."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Bad calibration",
            CONF_SOURCE: "sensor.raw",
            "attribute": "",
            CONF_DATA_POINTS_TEXT: "0, 0\n1, 1",
            CONF_DEGREE: 2,
            CONF_PRECISION: 2,
            "unit_of_measurement": "",
            "device_class": "",
            "state_class": "",
            CONF_HIDE_SOURCE: False,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_calibration"}
