"""Tests for the Normify config flow."""

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

from custom_components.normify.const import (
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
    CONF_REJECT_VALUES,
    CONF_REJECT_VALUES_TEXT,
    CONF_STATE_CLASS,
    CONF_WINDOW_DURATION,
    CONF_WINDOW_OUTPUT,
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
            CONF_ENABLE_LIMITS: limits,
            CONF_ENABLE_CALIBRATION: calibration,
            CONF_ENABLE_WINDOW: window,
            CONF_ENABLE_ROUNDING: rounding,
        },
    )


async def test_only_enabled_steps_are_visited(hass: HomeAssistant) -> None:
    """The one window page owns collection and publication timing."""
    result = await _source_step(
        hass, limits=True, calibration=True, window=True, rounding=True
    )
    assert result["step_id"] == "value_limits"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MINIMUM: 0, CONF_MAXIMUM: 100, CONF_REJECT_VALUES_TEXT: "-999"},
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
    assert result["step_id"] == "review"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_WINDOW_DURATION] == 30
    assert result["data"][CONF_WINDOW_OUTPUT] == WINDOW_OUTPUT_MEAN
    assert result["data"][CONF_DATA_POINTS] == [[38.68, 32.0], [79.89, 75.0]]
    assert result["data"][CONF_REJECT_VALUES] == [-999.0]


async def test_source_only_pipeline_skips_window(hass: HomeAssistant) -> None:
    """Omitting the window publishes accepted readings immediately."""
    result = await _source_step(hass)
    assert result["step_id"] == "review"


async def test_metadata_overrides_are_saved_and_shown_in_review(
    hass: HomeAssistant,
) -> None:
    """Create flow persists every explicit metadata override."""
    hass.states.async_set(
        "sensor.attribute_container",
        "1",
        {"friendly_name": "Attribute Container", "temperature": 72.5},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Conditioned attribute",
            CONF_SOURCE: "sensor.attribute_container",
            "attribute": "temperature",
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

    assert result["step_id"] == "review"
    assert "unit °F" in result["description_placeholders"]["pipeline"]
    assert "device class temperature" in result["description_placeholders"]["pipeline"]
    assert "state class measurement" in result["description_placeholders"]["pipeline"]
    assert "icon mdi:thermometer" in result["description_placeholders"]["pipeline"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_OF_MEASUREMENT] == "°F"
    assert result["data"][CONF_DEVICE_CLASS] == "temperature"
    assert result["data"][CONF_STATE_CLASS] == "measurement"
    assert result["data"][CONF_ICON] == "mdi:thermometer"


async def test_blank_metadata_overrides_are_elided(hass: HomeAssistant) -> None:
    """Blank optional metadata fields are never persisted."""
    hass.states.async_set("sensor.raw", "5")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Plain sensor",
            CONF_SOURCE: "sensor.raw",
            "attribute": "",
            CONF_HIDE_SOURCE: False,
            CONF_UNIT_OF_MEASUREMENT: "   ",
            CONF_DEVICE_CLASS: "",
            CONF_STATE_CLASS: "",
            CONF_ICON: "",
            CONF_ENABLE_LIMITS: False,
            CONF_ENABLE_CALIBRATION: False,
            CONF_ENABLE_WINDOW: False,
            CONF_ENABLE_ROUNDING: False,
        },
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    for key in (
        CONF_UNIT_OF_MEASUREMENT,
        CONF_DEVICE_CLASS,
        CONF_STATE_CLASS,
        CONF_ICON,
    ):
        assert key not in result["data"]


async def test_reconfigure_can_change_and_clear_metadata_overrides(
    hass: HomeAssistant,
) -> None:
    """Reconfigure exposes persisted metadata and removes cleared overrides."""
    hass.states.async_set("sensor.raw", "5")
    entry = ConfigEntry(
        version=1,
        minor_version=0,
        domain=DOMAIN,
        title="Metadata sensor",
        data={
            CONF_NAME: "Metadata sensor",
            CONF_SOURCE: "sensor.raw",
            CONF_HIDE_SOURCE: False,
            CONF_UNIT_OF_MEASUREMENT: "°F",
            CONF_DEVICE_CLASS: "temperature",
            CONF_STATE_CLASS: "measurement",
            CONF_ICON: "mdi:thermometer",
        },
        source="user",
        unique_id="metadata_sensor",
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
    assert result["step_id"] == "reconfigure"

    schema = result["data_schema"].schema
    suggested = {
        marker.schema: marker.description.get("suggested_value")
        for marker in schema
        if hasattr(marker, "description") and marker.description
    }
    assert suggested[CONF_UNIT_OF_MEASUREMENT] == "°F"
    assert suggested[CONF_DEVICE_CLASS] == "temperature"
    assert suggested[CONF_STATE_CLASS] == "measurement"
    assert suggested[CONF_ICON] == "mdi:thermometer"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Metadata sensor",
            CONF_SOURCE: "sensor.raw",
            "attribute": "",
            CONF_HIDE_SOURCE: False,
            CONF_UNIT_OF_MEASUREMENT: "°C",
            CONF_DEVICE_CLASS: "",
            CONF_STATE_CLASS: "",
            CONF_ICON: "",
            CONF_ENABLE_LIMITS: False,
            CONF_ENABLE_CALIBRATION: False,
            CONF_ENABLE_WINDOW: False,
            CONF_ENABLE_ROUNDING: False,
        },
    )
    assert result["step_id"] == "review"
    assert "unit °C" in result["description_placeholders"]["pipeline"]
    assert "device class" not in result["description_placeholders"]["pipeline"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_UNIT_OF_MEASUREMENT] == "°C"
    for key in (CONF_DEVICE_CLASS, CONF_STATE_CLASS, CONF_ICON):
        assert key not in entry.data
