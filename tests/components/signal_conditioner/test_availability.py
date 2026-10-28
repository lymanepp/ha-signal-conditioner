"""Tests for publication timing, restore, and availability of the sensor."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_NAME,
    CONF_SOURCE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.signal_conditioner.const import (
    CONF_AVAILABILITY,
    CONF_DURATION,
    CONF_FOLLOW_SOURCE,
    CONF_STALE_AFTER,
    CONF_WINDOW,
    DOMAIN,
)

SOURCE = "sensor.raw"
TARGET = "sensor.conditioned"


async def _setup(hass: HomeAssistant, **data: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Conditioned",
        unique_id="conditioned",
        data={CONF_NAME: "Conditioned", CONF_SOURCE: SOURCE, **data},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _state(hass: HomeAssistant) -> str:
    state = hass.states.get(TARGET)
    assert state is not None
    return state.state


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_window_publishes_current_reading_at_setup(
    hass: HomeAssistant,
) -> None:
    """A window does not leave the sensor unavailable until its first tick."""
    hass.states.async_set(SOURCE, "21.5")
    await _setup(hass, **{CONF_WINDOW: {CONF_DURATION: 60}})
    assert _state(hass) == "21.5"


async def test_window_mean_is_time_weighted_and_carries_over(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set(SOURCE, "10")
    await _setup(hass, **{CONF_WINDOW: {CONF_DURATION: 60}})
    await _tick(hass, freezer, 15)
    hass.states.async_set(SOURCE, "20")
    await hass.async_block_till_done()
    assert _state(hass) == "10.0"  # held until the interval ends

    await _tick(hass, freezer, 45)
    assert float(_state(hass)) == 17.5  # 10 for 15 s, 20 for 45 s

    await _tick(hass, freezer, 60)
    assert float(_state(hass)) == 20.0  # quiet interval: value in force


async def test_follows_unavailable_and_unknown_source_by_default(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(SOURCE, "5")
    await _setup(hass, **{CONF_WINDOW: {CONF_DURATION: 60}})
    assert _state(hass) == "5.0"

    hass.states.async_set(SOURCE, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_UNAVAILABLE

    # The first reading after a gap is published at once, window or not.
    hass.states.async_set(SOURCE, "6")
    await hass.async_block_till_done()
    assert _state(hass) == "6.0"

    hass.states.async_set(SOURCE, STATE_UNKNOWN)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_UNKNOWN

    hass.states.async_set(SOURCE, "7")
    await hass.async_block_till_done()
    assert _state(hass) == "7.0"

    hass.states.async_remove(SOURCE)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_UNAVAILABLE


async def test_unavailable_gap_is_not_averaged_into_next_interval(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set(SOURCE, "100")
    await _setup(hass, **{CONF_WINDOW: {CONF_DURATION: 60}})
    hass.states.async_set(SOURCE, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30)
    hass.states.async_set(SOURCE, "0")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30)
    assert float(_state(hass)) == 0.0


async def test_ignoring_source_availability_keeps_last_value(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(SOURCE, "5")
    await _setup(hass, **{CONF_AVAILABILITY: {CONF_FOLLOW_SOURCE: False}})
    for gap in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        hass.states.async_set(SOURCE, gap)
        await hass.async_block_till_done()
        assert _state(hass) == "5.0"


async def test_restores_last_value_while_source_is_missing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(TARGET, "71.25", {ATTR_UNIT_OF_MEASUREMENT: "°F"}),
                {"native_value": 71.25, "native_unit_of_measurement": "°F"},
            )
        ],
    )
    await _setup(hass, **{CONF_WINDOW: {CONF_DURATION: 60}})
    state = hass.states.get(TARGET)
    assert state is not None
    assert state.state == "71.25"
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°F"

    # The restored value is averaged with the first live reading.
    await _tick(hass, freezer, 30)
    hass.states.async_set(SOURCE, "72.25")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30)
    assert float(_state(hass)) == 71.75


async def test_does_not_restore_over_an_unavailable_source(
    hass: HomeAssistant,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(TARGET, "1.0"),
                {"native_value": 1.0, "native_unit_of_measurement": None},
            )
        ],
    )
    hass.states.async_set(SOURCE, STATE_UNAVAILABLE)
    await _setup(hass)
    assert _state(hass) == STATE_UNAVAILABLE


async def test_restore_is_skipped_when_source_has_a_value(
    hass: HomeAssistant,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(TARGET, "1.0"),
                {"native_value": 1.0, "native_unit_of_measurement": None},
            )
        ],
    )
    hass.states.async_set(SOURCE, "2")
    await _setup(hass)
    assert _state(hass) == "2.0"


async def test_non_numeric_restored_value_is_ignored(hass: HomeAssistant) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(TARGET, "x"),
                {"native_value": "x", "native_unit_of_measurement": None},
            )
        ],
    )
    await _setup(hass)
    assert _state(hass) == STATE_UNAVAILABLE


async def test_stale_source_goes_unavailable_and_recovers_on_report(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set(SOURCE, "5")
    await _setup(hass, **{CONF_AVAILABILITY: {CONF_STALE_AFTER: 300}})

    # Unchanged re-reports keep it alive.
    await _tick(hass, freezer, 200)
    hass.states.async_set(SOURCE, "5")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 200)
    assert _state(hass) == "5.0"

    await _tick(hass, freezer, 150)
    assert _state(hass) == STATE_UNAVAILABLE

    # An unchanged re-report after going stale brings it back.
    hass.states.async_set(SOURCE, "5")
    await hass.async_block_till_done()
    assert _state(hass) == "5.0"


async def test_stale_after_applies_to_a_restored_value(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(TARGET, "3.0"),
                {"native_value": 3.0, "native_unit_of_measurement": None},
            )
        ],
    )
    await _setup(hass, **{CONF_AVAILABILITY: {CONF_STALE_AFTER: 60}})
    assert _state(hass) == "3.0"
    await _tick(hass, freezer, 61)
    assert _state(hass) == STATE_UNAVAILABLE


async def test_rejected_readings_do_not_count_as_fresh(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set(SOURCE, "5")
    await _setup(
        hass,
        value_limits={"maximum": 10},
        **{CONF_AVAILABILITY: {CONF_STALE_AFTER: 60}},
    )
    await _tick(hass, freezer, 30)
    hass.states.async_set(SOURCE, "99")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 31)
    assert _state(hass) == STATE_UNAVAILABLE


async def test_attribute_source_reports_keep_it_alive(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set(SOURCE, "on", {"level": 4})
    await _setup(hass, attribute="level", **{CONF_AVAILABILITY: {CONF_STALE_AFTER: 60}})
    assert _state(hass) == "4.0"
    hass.states.async_set(SOURCE, "on", {"other": 1})
    await hass.async_block_till_done()
    assert _state(hass) == "4.0"


async def test_unknown_source_report_is_ignored_for_staleness(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(
        hass,
        **{CONF_AVAILABILITY: {CONF_FOLLOW_SOURCE: False, CONF_STALE_AFTER: 60}},
    )
    hass.states.async_set(SOURCE, STATE_UNKNOWN)
    await hass.async_block_till_done()
    hass.states.async_set(SOURCE, STATE_UNKNOWN)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_UNAVAILABLE
