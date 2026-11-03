"""Limits and calibration stay in the unit they were written in."""

from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_DEVICE_CLASS,
    CONF_ICON,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIT_OF_MEASUREMENT,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.signal_conditioner.const import (
    CONF_CALIBRATION,
    CONF_DATA_POINTS,
    CONF_DATA_POINTS_TEXT,
    CONF_DEGREE,
    CONF_ENABLE_CALIBRATION,
    CONF_ENABLE_LIMITS,
    CONF_ENABLE_ROUNDING,
    CONF_ENABLE_WINDOW,
    CONF_HIDE_SOURCE,
    CONF_INPUT_UNIT,
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_STATE_CLASS,
    CONF_VALUE_LIMITS,
    DOMAIN,
)
from custom_components.signal_conditioner.pipeline import (
    ConditioningPipeline,
    Disposition,
    PipelineConfig,
)
from custom_components.signal_conditioner.units import unit_issue_id

SOURCE = "sensor.raw"
FAHRENHEIT = {ATTR_UNIT_OF_MEASUREMENT: "°F", ATTR_DEVICE_CLASS: "temperature"}
CELSIUS = {ATTR_UNIT_OF_MEASUREMENT: "°C", ATTR_DEVICE_CLASS: "temperature"}
# The attic sensor from a real setup: limits and a -1 °F offset, in °F.
ATTIC = {
    CONF_VALUE_LIMITS: {CONF_MINIMUM: 20, CONF_MAXIMUM: 180},
    CONF_CALIBRATION: {CONF_DATA_POINTS: [[77.1, 76.1], [87.1, 86.1]]},
}


async def _setup(hass: HomeAssistant, **data: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Attic",
        data={CONF_NAME: "Attic", CONF_SOURCE: SOURCE, **data},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _set(hass: HomeAssistant, value: str, attributes: dict) -> None:
    hass.states.async_set(SOURCE, value, attributes)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant) -> tuple[str, str | None]:
    state = hass.states.get("sensor.attic")
    assert state is not None
    return state.state, state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, unit_issue_id(entry.entry_id))


@pytest.mark.parametrize(
    "stages",
    [ATTIC, {CONF_CALIBRATION: ATTIC[CONF_CALIBRATION]}],
    ids=["limits_and_calibration", "calibration_only"],
)
async def test_source_unit_change_is_converted(
    hass: HomeAssistant, stages: dict
) -> None:
    """°F limits and calibration still apply when the source switches to °C."""
    hass.states.async_set(SOURCE, "77.1", FAHRENHEIT)
    entry = await _setup(hass, **stages)
    assert _state(hass) == ("76.1", "°F")
    assert entry.options[CONF_INPUT_UNIT] == "°F"  # learned

    await _set(hass, "25.06", CELSIUS)  # 77.108 °F
    value, unit = _state(hass)
    assert unit == "°C"
    assert float(value) == pytest.approx((77.108 - 1 - 32) / 1.8)

    await _set(hass, "-10", CELSIUS)  # 14 °F
    if CONF_VALUE_LIMITS in stages:
        # Below the 20 °F minimum: rejected, as it would be in °F.
        assert _state(hass)[0] == pytest.approx(value)
    else:
        assert float(_state(hass)[0]) == pytest.approx((14 - 1 - 32) / 1.8)


async def test_unconvertible_unit_is_rejected_and_reported(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(SOURCE, "50", {ATTR_UNIT_OF_MEASUREMENT: "%"})
    entry = await _setup(
        hass, **{CONF_VALUE_LIMITS: {CONF_MAXIMUM: 100}, CONF_INPUT_UNIT: "%"}
    )
    assert _state(hass) == ("50.0", "%")

    await _set(hass, "0.5", {ATTR_UNIT_OF_MEASUREMENT: "ratio"})
    assert _state(hass)[0] == STATE_UNKNOWN
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_placeholders == {
        "conditioner": "Attic",
        "source": SOURCE,
        "source_unit": "ratio",
        "input_unit": "%",
    }

    await _set(hass, "60", {ATTR_UNIT_OF_MEASUREMENT: "%"})
    assert _state(hass) == ("60.0", "%")
    assert _issue(hass, entry) is None


async def test_unit_change_without_device_class_is_not_guessed(
    hass: HomeAssistant,
) -> None:
    """°C/°F are also temperature-difference units: no device class, no guess."""
    hass.states.async_set(SOURCE, "77.1", {ATTR_UNIT_OF_MEASUREMENT: "°F"})
    entry = await _setup(hass, **ATTIC)
    await _set(hass, "25", {ATTR_UNIT_OF_MEASUREMENT: "°C"})
    assert _state(hass)[0] == STATE_UNKNOWN
    assert _issue(hass, entry) is not None


async def test_deleting_the_entry_clears_the_issue(hass: HomeAssistant) -> None:
    hass.states.async_set(SOURCE, "1", {ATTR_UNIT_OF_MEASUREMENT: "A"})
    entry = await _setup(hass, **{**ATTIC, CONF_INPUT_UNIT: "V"})
    assert _issue(hass, entry) is not None
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None


async def test_explicit_output_unit_is_not_converted_back(
    hass: HomeAssistant,
) -> None:
    """A calibration to % takes volts; mV input is converted in, not out."""
    hass.states.async_set(
        SOURCE, "2000", {ATTR_UNIT_OF_MEASUREMENT: "mV", ATTR_DEVICE_CLASS: "voltage"}
    )
    await _setup(
        hass,
        **{
            CONF_UNIT_OF_MEASUREMENT: "%",
            CONF_INPUT_UNIT: "V",
            CONF_CALIBRATION: {CONF_DATA_POINTS: [[0, 0], [4, 100]]},
        },
    )
    assert _state(hass) == ("50.0", "%")


async def test_explicit_input_unit_wins_over_learning(hass: HomeAssistant) -> None:
    hass.states.async_set(SOURCE, "25.06", CELSIUS)
    entry = await _setup(hass, **{**ATTIC, CONF_INPUT_UNIT: "°F"})
    assert float(_state(hass)[0]) == pytest.approx((77.108 - 1 - 32) / 1.8)
    assert CONF_INPUT_UNIT not in entry.options


async def test_unit_is_not_tracked_without_limits_or_calibration(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(SOURCE, "77", FAHRENHEIT)
    entry = await _setup(hass)
    await _set(hass, "25", CELSIUS)
    assert _state(hass) == ("25.0", "°C")
    assert CONF_INPUT_UNIT not in entry.options


async def test_learned_unit_survives_yaml_import(hass: HomeAssistant) -> None:
    """The import replaces data, so the learned unit lives in options."""
    hass.states.async_set(SOURCE, "77.1", FAHRENHEIT)
    yaml = {DOMAIN: {"attic": {CONF_SOURCE: SOURCE, **ATTIC}}}
    assert await async_setup_component(hass, DOMAIN, yaml)
    await hass.async_block_till_done()
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert entry.source == SOURCE_IMPORT
    assert entry.options[CONF_INPUT_UNIT] == "°F"

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _set(hass, "25.06", CELSIUS)
    assert float(_state(hass)[0]) == pytest.approx((77.108 - 1 - 32) / 1.8)


def _source_input(**overrides: object) -> dict[str, Any]:
    return {
        CONF_NAME: "Attic",
        CONF_SOURCE: SOURCE,
        CONF_HIDE_SOURCE: False,
        CONF_UNIT_OF_MEASUREMENT: "",
        CONF_DEVICE_CLASS: "",
        CONF_STATE_CLASS: "",
        CONF_ICON: "",
        CONF_ENABLE_LIMITS: False,
        CONF_ENABLE_CALIBRATION: True,
        CONF_ENABLE_WINDOW: False,
        CONF_ENABLE_ROUNDING: False,
        **overrides,
    }


async def _calibrate(hass: HomeAssistant, flow_id: str) -> dict[str, Any]:
    return await hass.config_entries.flow.async_configure(
        flow_id, {CONF_DATA_POINTS_TEXT: "77.1, 76.1\n87.1, 86.1", CONF_DEGREE: 1}
    )


async def test_config_flow_records_and_shows_the_input_unit(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(SOURCE, "77", FAHRENHEIT)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _source_input()
    )
    assert result["description_placeholders"]["unit"] == "°F"
    result = await _calibrate(hass, result["flow_id"])
    assert result["data"][CONF_INPUT_UNIT] == "°F"


@pytest.mark.parametrize(
    ("new_attributes", "expected"),
    [(CELSIUS, "°F"), ({ATTR_UNIT_OF_MEASUREMENT: "%"}, "%")],
    ids=["convertible_keeps_old_unit", "unconvertible_takes_new_unit"],
)
async def test_reconfigure_input_unit(
    hass: HomeAssistant, new_attributes: dict, expected: str
) -> None:
    hass.states.async_set(SOURCE, "25", new_attributes)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Attic",
        data={
            CONF_NAME: "Attic",
            CONF_SOURCE: SOURCE,
            CONF_CALIBRATION: ATTIC[CONF_CALIBRATION],
        },
        options={CONF_INPUT_UNIT: "°F"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    source_input = _source_input()
    del source_input[CONF_SOURCE]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], source_input
    )
    assert result["description_placeholders"]["unit"] == expected
    await _calibrate(hass, result["flow_id"])
    assert entry.data[CONF_INPUT_UNIT] == expected


def test_pipeline_converts_around_limits_and_calibration() -> None:
    """Limits see the converted reading; the result is converted back."""
    pipeline = ConditioningPipeline(PipelineConfig(maximum=100))
    assert pipeline.condition("30", lambda v: v * 3, lambda v: v / 3).value == 30
    # 40 is within the limit, but 120 (converted) is not.
    assert pipeline.condition("40").value == 40
    result = pipeline.condition("40", lambda v: v * 3, lambda v: v / 3)
    assert result.disposition is Disposition.REJECT
