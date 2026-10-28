"""Tests for the Signal Conditioner config flow."""

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_ICON,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.signal_conditioner.const import (
    CONF_DATA_POINTS,
    CONF_DATA_POINTS_TEXT,
    CONF_DEGREE,
    CONF_DURATION,
    CONF_ENABLE_CALIBRATION,
    CONF_ENABLE_LIMITS,
    CONF_ENABLE_ROUNDING,
    CONF_ENABLE_WINDOW,
    CONF_HIDE_SOURCE,
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_OUTPUT,
    CONF_PRECISION,
    CONF_STATE_CLASS,
    CONF_CALIBRATION,
    CONF_ROUNDING,
    CONF_VALUE_LIMITS,
    CONF_WINDOW,
    DOMAIN,
    WINDOW_OUTPUT_MEAN,
)


async def _source_step(
    hass: HomeAssistant,
    *,
    limits: bool = False,
    calibration: bool = False,
    window: bool = False,
    rounding: bool = False,
) -> dict:
    hass.states.async_set(
        "sensor.garage_humidity_raw",
        "50",
        {"friendly_name": "Garage Humidity Raw", "unit_of_measurement": "%"},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "",
            CONF_SOURCE: "sensor.garage_humidity_raw",
            "attribute": "",
            CONF_HIDE_SOURCE: False,
            CONF_UNIT_OF_MEASUREMENT: "",
            CONF_DEVICE_CLASS: "",
            CONF_STATE_CLASS: "",
            CONF_ICON: "",
            CONF_ENABLE_LIMITS: limits,
            CONF_ENABLE_CALIBRATION: calibration,
            CONF_ENABLE_WINDOW: window,
            CONF_ENABLE_ROUNDING: rounding,
        },
    )


async def test_only_enabled_steps_are_visited(hass: HomeAssistant) -> None:
    result = await _source_step(
        hass, limits=True, calibration=True, window=True, rounding=True
    )
    assert result["step_id"] == "value_limits"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MINIMUM: 0, CONF_MAXIMUM: 100}
    )
    assert result["step_id"] == "calibration"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DATA_POINTS_TEXT: "38.68, 32\n79.89, 75", CONF_DEGREE: 1},
    )
    assert result["step_id"] == "window"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DURATION: {"seconds": 30}, CONF_OUTPUT: WINDOW_OUTPUT_MEAN},
    )
    assert result["step_id"] == "rounding"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PRECISION: 1}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_WINDOW][CONF_DURATION] == 30
    assert result["data"][CONF_WINDOW][CONF_OUTPUT] == WINDOW_OUTPUT_MEAN
    assert result["data"][CONF_CALIBRATION][CONF_DATA_POINTS] == [
        [38.68, 32.0],
        [79.89, 75.0],
    ]
    assert result["data"][CONF_VALUE_LIMITS] == {
        CONF_MINIMUM: 0.0,
        CONF_MAXIMUM: 100.0,
    }
    assert result["data"][CONF_ROUNDING] == {CONF_PRECISION: 1}


async def test_source_only_pipeline_finishes_immediately(hass: HomeAssistant) -> None:
    result = await _source_step(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage Humidity Raw"


async def test_metadata_overrides_are_saved(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.raw", "1")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Conditioned",
            CONF_SOURCE: "sensor.raw",
            "attribute": "",
            CONF_HIDE_SOURCE: False,
            CONF_UNIT_OF_MEASUREMENT: "°F",
            CONF_DEVICE_CLASS: "temperature",
            CONF_STATE_CLASS: "measurement",
            CONF_ICON: "mdi:thermometer",
            CONF_ENABLE_LIMITS: False,
            CONF_ENABLE_CALIBRATION: False,
            CONF_ENABLE_WINDOW: False,
            CONF_ENABLE_ROUNDING: False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_OF_MEASUREMENT] == "°F"
    assert result["data"][CONF_DEVICE_CLASS] == "temperature"


async def test_blank_metadata_overrides_are_elided(hass: HomeAssistant) -> None:
    result = await _source_step(hass)
    for key in (
        CONF_UNIT_OF_MEASUREMENT,
        CONF_DEVICE_CLASS,
        CONF_STATE_CLASS,
        CONF_ICON,
    ):
        assert key not in result["data"]


async def test_same_name_different_sources_do_not_collide(hass: HomeAssistant) -> None:
    for entity_id in ("sensor.first_raw", "sensor.second_raw"):
        hass.states.async_set(entity_id, "1", {"friendly_name": "Shared"})
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Shared",
                CONF_SOURCE: entity_id,
                "attribute": "",
                CONF_HIDE_SOURCE: False,
                CONF_UNIT_OF_MEASUREMENT: "",
                CONF_DEVICE_CLASS: "",
                CONF_STATE_CLASS: "",
                CONF_ICON: "",
                CONF_ENABLE_LIMITS: False,
                CONF_ENABLE_CALIBRATION: False,
                CONF_ENABLE_WINDOW: False,
                CONF_ENABLE_ROUNDING: False,
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["result"].unique_id == entity_id


async def test_attribute_and_hide_source_are_incompatible(hass: HomeAssistant) -> None:
    """The source entity cannot be hidden when conditioning one attribute."""
    hass.states.async_set("sensor.raw", "5", {"temperature": 72})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Attribute",
            CONF_SOURCE: "sensor.raw",
            "attribute": "temperature",
            CONF_HIDE_SOURCE: True,
            CONF_UNIT_OF_MEASUREMENT: "",
            CONF_DEVICE_CLASS: "",
            CONF_STATE_CLASS: "",
            CONF_ICON: "",
            CONF_ENABLE_LIMITS: False,
            CONF_ENABLE_CALIBRATION: False,
            CONF_ENABLE_WINDOW: False,
            CONF_ENABLE_ROUNDING: False,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_configuration"}


async def test_reconfigure_keeps_source_identity(hass: HomeAssistant) -> None:
    """Reconfiguration changes behavior, not the entry's source identity."""
    hass.states.async_set("sensor.original", "5")
    entry = ConfigEntry(
        version=1,
        minor_version=0,
        domain=DOMAIN,
        title="Original",
        data={
            CONF_NAME: "Original",
            CONF_SOURCE: "sensor.original",
            CONF_HIDE_SOURCE: False,
        },
        source="user",
        unique_id="sensor.original",
        discovery_keys={},
        options={},
        subentries_data=[],
    )
    hass.config_entries._entries[entry.entry_id] = entry

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    schema_keys = {marker.schema for marker in result["data_schema"].schema}
    assert CONF_SOURCE not in schema_keys
    assert "attribute" not in schema_keys

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Updated",
            CONF_HIDE_SOURCE: False,
            CONF_UNIT_OF_MEASUREMENT: "",
            CONF_DEVICE_CLASS: "",
            CONF_STATE_CLASS: "",
            CONF_ICON: "",
            CONF_ENABLE_LIMITS: False,
            CONF_ENABLE_CALIBRATION: False,
            CONF_ENABLE_WINDOW: False,
            CONF_ENABLE_ROUNDING: False,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_SOURCE] == "sensor.original"
    assert entry.unique_id == "sensor.original"
