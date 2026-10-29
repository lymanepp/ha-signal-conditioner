"""Tests for the Signal Conditioner sensor entity."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_NAME,
    CONF_SOURCE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryHider

from custom_components.signal_conditioner.const import (
    CONF_CALIBRATION,
    CONF_DATA_POINTS,
    CONF_DEGREE,
    CONF_PRECISION,
    CONF_ROUNDING,
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
        minor_version=0,
        domain=DOMAIN,
        title="Corrected temperature",
        data={
            CONF_NAME: "Corrected temperature",
            CONF_SOURCE: "sensor.raw_temperature",
            CONF_CALIBRATION: {
                CONF_DATA_POINTS: [[1.0, 2.0], [2.0, 3.0]],
                CONF_DEGREE: 1,
            },
            CONF_ROUNDING: {CONF_PRECISION: 2},
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

    hass.states.async_set("sensor.raw_temperature", "7", {})
    await hass.async_block_till_done()

    state = hass.states.get("sensor.corrected_temperature")
    assert state is not None
    assert float(state.state) == 8.0
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"


async def test_unavailable_source_retains_last_valid_value(hass: HomeAssistant) -> None:
    """follow_source: false keeps the original Calibration behavior."""
    hass.states.async_set("sensor.raw", "3")

    entry = ConfigEntry(
        version=1,
        minor_version=0,
        domain=DOMAIN,
        title="Stable value",
        data={
            CONF_NAME: "Stable value",
            CONF_SOURCE: "sensor.raw",
            CONF_CALIBRATION: {
                CONF_DATA_POINTS: [[0.0, 0.0], [1.0, 1.0]],
                CONF_DEGREE: 1,
            },
            CONF_ROUNDING: {CONF_PRECISION: 2},
            "availability": {"follow_source": False},
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


async def test_hide_source_is_reversed_on_unload(hass: HomeAssistant) -> None:
    """Undo only source hiding owned by Signal Conditioner."""

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", "test", "raw_hidden", suggested_object_id="raw_hidden"
    )
    hass.states.async_set("sensor.raw_hidden", "3")

    entry = ConfigEntry(
        version=1,
        minor_version=0,
        domain=DOMAIN,
        title="Hidden source",
        data={
            CONF_NAME: "Hidden source",
            CONF_SOURCE: "sensor.raw_hidden",
            "hide_source": True,
        },
        source="user",
        unique_id="sensor.raw_hidden",
        discovery_keys={},
        options={},
        subentries_data=[],
    )
    hass.config_entries._entries[entry.entry_id] = entry

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (
        registry.async_get("sensor.raw_hidden").hidden_by
        is RegistryEntryHider.INTEGRATION
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("sensor.raw_hidden").hidden_by is None


async def test_late_created_source_is_hidden_when_requested(
    hass: HomeAssistant,
) -> None:
    """Resolve late startup dependencies rather than leaving a source visible."""
    entry = ConfigEntry(
        version=1,
        minor_version=0,
        domain=DOMAIN,
        title="Conditioned late sensor",
        data={
            CONF_NAME: "Conditioned late sensor",
            CONF_SOURCE: "sensor.late_raw",
            "hide_source": True,
        },
        source="user",
        unique_id="conditioned_late_sensor",
        discovery_keys={},
        options={},
        subentries_data=[],
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get("sensor.late_raw") is None
    registry.async_get_or_create(
        "sensor", "test", "late_raw", suggested_object_id="late_raw"
    )
    hass.states.async_set("sensor.late_raw", "10")
    await hass.async_block_till_done()
    assert (
        registry.async_get("sensor.late_raw").hidden_by
        is RegistryEntryHider.INTEGRATION
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("sensor.late_raw").hidden_by is None


async def test_unknown_source_does_not_restore_previous_value(
    hass: HomeAssistant,
) -> None:
    """An existing unknown source must be honored when follow_source is on."""
    hass.states.async_set("sensor.unknown_raw", STATE_UNKNOWN)
    entry = ConfigEntry(
        version=1,
        minor_version=0,
        domain=DOMAIN,
        title="Unknown raw",
        data={CONF_NAME: "Unknown raw", CONF_SOURCE: "sensor.unknown_raw"},
        source="user",
        unique_id="unknown_raw_conditioned",
        discovery_keys={},
        options={},
        subentries_data=[],
    )
    hass.config_entries._entries[entry.entry_id] = entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    conditioned = [
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id != "sensor.unknown_raw"
    ]
    assert len(conditioned) == 1
    assert conditioned[0].state == STATE_UNKNOWN
