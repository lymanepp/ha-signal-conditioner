"""Tests for the Normify sensor entity."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_NAME,
    CONF_SOURCE,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant

from custom_components.normify.const import (
    ATTR_SOURCE_VALUE,
    CONF_DATA_POINTS,
    CONF_DEGREE,
    CONF_HIDE_SOURCE,
    CONF_PRECISION,
    DOMAIN,
)


async def test_sensor_updates_and_inherits_metadata(hass: HomeAssistant) -> None:
    """Calibrate source values and inherit source metadata."""
    hass.states.async_set(
        "sensor.raw_temperature",
        "4",
        {
            ATTR_UNIT_OF_MEASUREMENT: "°C",
            ATTR_DEVICE_CLASS: "temperature",
            "state_class": "measurement",
        },
    )

    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Corrected temperature",
        data={
            CONF_NAME: "Corrected temperature",
            CONF_SOURCE: "sensor.raw_temperature",
            CONF_DATA_POINTS: [[1.0, 2.0], [2.0, 3.0]],
            CONF_DEGREE: 1,
            CONF_PRECISION: 2,
            CONF_HIDE_SOURCE: False,
        },
        source="user",
        unique_id="corrected_temperature",
        discovery_keys={},
        options={},
        subentries_data=[],
    )
    hass.config_entries._entries[entry.entry_id] = entry

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.corrected_temperature")
    assert state is not None
    assert float(state.state) == 5.0
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"
    assert state.attributes[ATTR_DEVICE_CLASS] == "temperature"
    assert state.attributes[ATTR_SOURCE_VALUE] == 4.0

    hass.states.async_set("sensor.raw_temperature", "7", {})
    await hass.async_block_till_done()

    state = hass.states.get("sensor.corrected_temperature")
    assert state is not None
    assert float(state.state) == 8.0
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"


async def test_unavailable_source_retains_last_valid_value(hass: HomeAssistant) -> None:
    """Preserve Calibration's established unavailable-state behavior."""
    hass.states.async_set("sensor.raw", "3")

    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Stable value",
        data={
            CONF_NAME: "Stable value",
            CONF_SOURCE: "sensor.raw",
            CONF_DATA_POINTS: [[0.0, 0.0], [1.0, 1.0]],
            CONF_DEGREE: 1,
            CONF_PRECISION: 2,
            CONF_HIDE_SOURCE: False,
        },
        source="user",
        unique_id="stable_value",
        discovery_keys={},
        options={},
        subentries_data=[],
    )
    hass.config_entries._entries[entry.entry_id] = entry

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.stable_value").state == "3.0"

    hass.states.async_set("sensor.raw", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.stable_value").state == "3.0"
