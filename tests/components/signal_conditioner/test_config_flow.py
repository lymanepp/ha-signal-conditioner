"""Tests for the Signal Conditioner config flow."""

from unittest.mock import patch

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
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.signal_conditioner.const import (
    CONF_AVAILABILITY,
    CONF_CALIBRATION,
    CONF_DATA_POINTS,
    CONF_DATA_POINTS_TEXT,
    CONF_DEGREE,
    CONF_DURATION,
    CONF_ENABLE_CALIBRATION,
    CONF_ENABLE_LIMITS,
    CONF_ENABLE_ROUNDING,
    CONF_ENABLE_WINDOW,
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
    CONF_USE_ATTRIBUTE,
    CONF_VALUE_LIMITS,
    CONF_WINDOW,
    DOMAIN,
    LIMIT_MODE_CLAMP,
    LIMIT_MODE_REJECT,
    WINDOW_OUTPUT_MEAN,
)
from custom_components.signal_conditioner.pipeline import PipelineConfigurationError


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
        CONF_MODE: LIMIT_MODE_REJECT,
    }
    assert result["data"][CONF_ROUNDING] == {CONF_PRECISION: 1}


async def test_source_only_pipeline_finishes_immediately(hass: HomeAssistant) -> None:
    result = await _source_step(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage Humidity Raw (conditioned)"


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
        assert result["result"].unique_id is None


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
            CONF_USE_ATTRIBUTE: True,
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
    assert result["errors"] == {"base": "hide_source_with_attribute"}


async def test_reconfigure_keeps_source_identity(hass: HomeAssistant) -> None:
    """Reconfiguring without touching the source keeps it."""
    hass.states.async_set("sensor.original", "5")
    entry = ConfigEntry(
        version=1,
        minor_version=1,
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


def _plain_source(**extra: object) -> dict:
    return {
        CONF_NAME: "Plain",
        CONF_SOURCE: "sensor.raw",
        CONF_HIDE_SOURCE: False,
        CONF_UNIT_OF_MEASUREMENT: "",
        CONF_DEVICE_CLASS: "",
        CONF_STATE_CLASS: "",
        CONF_ICON: "",
        CONF_ENABLE_LIMITS: False,
        CONF_ENABLE_CALIBRATION: False,
        CONF_ENABLE_WINDOW: False,
        CONF_ENABLE_ROUNDING: False,
        **extra,
    }


async def test_availability_defaults_to_following_the_source(
    hass: HomeAssistant,
) -> None:
    result = await _source_step(hass)
    assert result["data"][CONF_AVAILABILITY] == {CONF_FOLLOW_SOURCE: True}


async def test_availability_options_are_saved(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.raw", "1")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        _plain_source(
            **{
                CONF_FOLLOW_SOURCE: False,
                CONF_STALE_AFTER: {"minutes": 15},
            }
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_AVAILABILITY] == {
        CONF_FOLLOW_SOURCE: False,
        CONF_STALE_AFTER: 900,
    }


async def test_zero_stale_after_means_disabled(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.raw", "1")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _plain_source(**{CONF_STALE_AFTER: {"seconds": 0}})
    )
    assert CONF_STALE_AFTER not in result["data"][CONF_AVAILABILITY]


async def test_out_of_range_stale_after_is_rejected(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.raw", "1")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _plain_source(**{CONF_STALE_AFTER: {"hours": 25}})
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "stale_after_out_of_range"}


async def test_clamp_limit_mode_is_saved(hass: HomeAssistant) -> None:
    result = await _source_step(hass, limits=True)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MINIMUM: 0, CONF_MODE: LIMIT_MODE_CLAMP}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_VALUE_LIMITS] == {
        CONF_MINIMUM: 0.0,
        CONF_MODE: LIMIT_MODE_CLAMP,
    }


async def test_reconfigure_offers_saved_availability(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.original", "5")
    entry = ConfigEntry(
        version=1,
        minor_version=0,
        domain=DOMAIN,
        title="Original",
        data={
            CONF_NAME: "Original",
            CONF_SOURCE: "sensor.original",
            CONF_AVAILABILITY: {CONF_FOLLOW_SOURCE: False, CONF_STALE_AFTER: 600},
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
    markers = {marker.schema: marker for marker in result["data_schema"].schema}
    assert markers[CONF_FOLLOW_SOURCE].default() is False
    assert markers[CONF_STALE_AFTER].description == {
        "suggested_value": {"seconds": 600.0}
    }


async def test_reconfigure_reloads_a_loaded_entry_once(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Reconfigure reloads exactly once, without the update-listener warning."""
    hass.states.async_set("sensor.raw", "5")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Plain",
        unique_id="sensor.raw",
        data={CONF_NAME: "Plain", CONF_SOURCE: "sensor.raw"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await entry.start_reconfigure_flow(hass)
    with patch.object(
        hass.config_entries,
        "async_reload",
        wraps=hass.config_entries.async_reload,
    ) as reload:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                key: value
                for key, value in _plain_source(
                    **{CONF_NAME: "Renamed", CONF_UNIT_OF_MEASUREMENT: "V"}
                ).items()
                if key not in (CONF_SOURCE, "attribute")
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert reload.call_count == 1
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert "update listener" not in caplog.text
    state = hass.states.get("sensor.plain")
    assert state is not None
    assert state.attributes["unit_of_measurement"] == "V"


async def test_value_limits_need_a_minimum_or_maximum(hass: HomeAssistant) -> None:
    result = await _source_step(hass, limits=True)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODE: LIMIT_MODE_CLAMP}
    )
    assert result["step_id"] == "value_limits"
    assert result["errors"] == {"base": "limits_empty"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MINIMUM: 10, CONF_MAXIMUM: 0}
    )
    assert result["errors"] == {"base": "minimum_above_maximum"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAXIMUM: 100}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_calibration_error_names_the_problem(hass: HomeAssistant) -> None:
    result = await _source_step(hass, calibration=True)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DATA_POINTS_TEXT: "1, 2", CONF_DEGREE: 1}
    )
    assert result["errors"] == {"base": "invalid_calibration"}
    assert "at least 2 points" in result["description_placeholders"]["error"]


async def test_calibration_accepts_decimal_commas(hass: HomeAssistant) -> None:
    result = await _source_step(hass, calibration=True)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DATA_POINTS_TEXT: "38,68; 32\n79,89 75", CONF_DEGREE: 1},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CALIBRATION][CONF_DATA_POINTS] == [
        [38.68, 32.0],
        [79.89, 75.0],
    ]


async def test_invalid_combination_returns_to_first_form(hass: HomeAssistant) -> None:
    """A final validation failure shows an error instead of crashing the flow."""
    hass.states.async_set("sensor.raw", "1")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.signal_conditioner.config_flow.pipeline_config_from_data",
        side_effect=PipelineConfigurationError("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _plain_source()
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_configuration"}


async def test_value_limits_accept_decimals(hass: HomeAssistant) -> None:
    result = await _source_step(hass, limits=True)
    selector = result["data_schema"].schema[CONF_MINIMUM]
    assert selector.config["step"] == "any"


async def test_sub_second_stale_after_is_rejected(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.raw", "1")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        _plain_source(**{CONF_STALE_AFTER: {"seconds": 0.5}}),
    )
    assert result["errors"] == {"base": "stale_after_out_of_range"}
