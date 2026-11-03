"""Problems must be visible: a gone source, readings that are all rejected."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_NAME, CONF_SOURCE, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.signal_conditioner.const import (
    CONF_AVAILABILITY,
    CONF_FOLLOW_SOURCE,
    CONF_VALUE_LIMITS,
    DOMAIN,
)
from custom_components.signal_conditioner.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.signal_conditioner.rejections import rejection_issue_id

SOURCE = "sensor.raw"
TARGET = "sensor.conditioned"


async def _setup(hass: HomeAssistant, **data: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Conditioned",
        data={CONF_NAME: "Conditioned", CONF_SOURCE: SOURCE, **data},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant) -> str:
    state = hass.states.get(TARGET)
    assert state is not None
    return state.state


def _restore(hass: HomeAssistant) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(TARGET, "3.0"),
                {"native_value": 3.0, "native_unit_of_measurement": None},
            )
        ],
    )


async def test_restored_value_ends_when_source_is_confirmed_missing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _restore(hass)
    await _setup(hass)
    assert _state(hass) == "3.0"
    await _tick(hass, freezer, 31)  # the Repairs grace period
    assert _state(hass) == STATE_UNAVAILABLE

    hass.states.async_set(SOURCE, "4")
    await hass.async_block_till_done()
    assert _state(hass) == "4.0"


async def test_restored_value_kept_without_follow_source(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _restore(hass)
    await _setup(hass, **{CONF_AVAILABILITY: {CONF_FOLLOW_SOURCE: False}})
    await _tick(hass, freezer, 31)
    assert _state(hass) == "3.0"


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, rejection_issue_id(entry.entry_id)
    )


async def _send(hass: HomeAssistant, value: str) -> None:
    hass.states.async_set(SOURCE, value)
    await hass.async_block_till_done()


async def test_lasting_rejection_is_reported_and_clears(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set(SOURCE, "5")
    entry = await _setup(hass, **{CONF_VALUE_LIMITS: {"maximum": 10}})

    for minute in range(10):
        await _send(hass, str(100 + minute))
        assert _issue(hass, entry) is None  # a glitch is not reported
        await _tick(hass, freezer, 60)
    await _send(hass, "200")
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_placeholders == {
        "conditioner": "Conditioned",
        "source": SOURCE,
        "minutes": "10",
        "value": "200",
        "reason": "above maximum",
    }
    assert _state(hass) == "5.0"

    await _send(hass, "7")
    assert _issue(hass, entry) is None


async def test_few_rejections_over_a_long_time_are_not_reported(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """One bad reading, then silence: the streak needs several readings."""
    hass.states.async_set(SOURCE, "5")
    entry = await _setup(hass, **{CONF_VALUE_LIMITS: {"maximum": 10}})
    await _send(hass, "100")
    await _tick(hass, freezer, 3600)
    await _send(hass, "101")
    assert _issue(hass, entry) is None


async def test_deleting_the_entry_clears_the_rejection_issue(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set(SOURCE, "5")
    entry = await _setup(hass, **{CONF_VALUE_LIMITS: {"maximum": 10}})
    for value in ("100", "101", "102"):
        await _tick(hass, freezer, 300)
        await _send(hass, value)
    assert _issue(hass, entry) is not None
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None


async def test_diagnostics_show_reading_counters(hass: HomeAssistant) -> None:
    hass.states.async_set(SOURCE, "5")
    entry = await _setup(hass, **{CONF_VALUE_LIMITS: {"maximum": 10}})
    await _send(hass, "abc")

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    readings = diagnostics["sensor"]["readings"]
    assert readings["accepted"] == 1
    assert readings["rejected"] == 1
    assert readings["rejecting_now"] is True
    assert readings["last_rejection"]["value"] == "abc"
    assert readings["last_rejection"]["reason"] == "source value is not numeric"
    assert diagnostics["entry"]["data"][CONF_SOURCE] == SOURCE
    assert diagnostics["source_state"]["state"] == "abc"
