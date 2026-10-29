"""Repairs regressions for missing, disabled, and recovering source references."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import (
    CONF_NAME,
    CONF_SOURCE,
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.signal_conditioner.const import DOMAIN
from custom_components.signal_conditioner.source_health import source_issue_id

SOURCE = "sensor.reference_raw"


async def _setup(hass: HomeAssistant, source: str = SOURCE) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Conditioned reference",
        unique_id="conditioned_reference",
        data={CONF_NAME: "Conditioned reference", CONF_SOURCE: source},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.Issue | None:
    return ir.async_get(hass).issues.get((DOMAIN, source_issue_id(entry.entry_id)))


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _finish_startup(hass: HomeAssistant) -> None:
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()


async def test_missing_source_waits_for_startup_and_grace(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    await _tick(hass, freezer, 60)
    assert _issue(hass, entry) is None  # No issue before Home Assistant starts.

    await _finish_startup(hass)
    await _tick(hass, freezer, 29)
    assert _issue(hass, entry) is None
    await _tick(hass, freezer, 2)
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "missing_source"
    assert issue.is_fixable is False
    assert issue.is_persistent is False
    assert issue.translation_placeholders["source"] == SOURCE


async def test_transient_missing_source_never_creates_issue(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set(SOURCE, "20")
    entry = await _setup(hass)
    await _finish_startup(hass)
    hass.states.async_remove(SOURCE)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 15)
    hass.states.async_set(SOURCE, "21")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 31)
    assert _issue(hass, entry) is None


async def test_source_appearing_clears_repairs_immediately(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    await _finish_startup(hass)
    await _tick(hass, freezer, 31)
    assert _issue(hass, entry) is not None
    hass.states.async_set(SOURCE, "12")
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None


async def test_unknown_and_unavailable_are_not_repairs(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set(SOURCE, STATE_UNKNOWN)
    entry = await _setup(hass)
    await _finish_startup(hass)
    await _tick(hass, freezer, 31)
    assert _issue(hass, entry) is None
    hass.states.async_set(SOURCE, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 31)
    assert _issue(hass, entry) is None


async def test_registered_but_unloaded_source_is_not_missing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    er.async_get(hass).async_get_or_create(
        "sensor", "test", "registered_raw", suggested_object_id="reference_raw"
    )
    entry = await _setup(hass)
    await _finish_startup(hass)
    await _tick(hass, freezer, 31)
    assert _issue(hass, entry) is None


async def test_disabled_source_creates_issue_and_enable_clears_it(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", "test", "disabled_raw", suggested_object_id="reference_raw"
    )
    registry.async_update_entity(SOURCE, disabled_by=RegistryEntryDisabler.USER)
    entry = await _setup(hass)
    await _finish_startup(hass)
    await _tick(hass, freezer, 31)
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "disabled_source"
    registry.async_update_entity(SOURCE, disabled_by=None)
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None


async def test_missing_issue_removed_on_entry_unload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    await _finish_startup(hass)
    await _tick(hass, freezer, 31)
    assert _issue(hass, entry) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None
    await _tick(hass, freezer, 60)
    assert _issue(hass, entry) is None


async def test_missing_issue_removed_when_entry_is_deleted(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    await _finish_startup(hass)
    await _tick(hass, freezer, 31)
    assert _issue(hass, entry) is not None
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None


async def test_reconfigure_clears_old_source_issue(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    await _finish_startup(hass)
    await _tick(hass, freezer, 31)
    assert _issue(hass, entry) is not None

    hass.states.async_set("sensor.healthy_raw", "42")
    hass.config_entries.async_update_entry(
        entry,
        data={CONF_NAME: "Conditioned reference", CONF_SOURCE: "sensor.healthy_raw"},
    )
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None


async def test_unload_before_grace_cancels_pending_issue(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    await _finish_startup(hass)
    await _tick(hass, freezer, 15)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await _tick(hass, freezer, 30)
    assert _issue(hass, entry) is None


async def test_live_source_overrides_disabled_registry_record(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", "test", "old_raw", suggested_object_id="reference_raw"
    )
    registry.async_update_entity(SOURCE, disabled_by=RegistryEntryDisabler.USER)
    hass.states.async_set(SOURCE, "17")
    entry = await _setup(hass)
    await _finish_startup(hass)
    await _tick(hass, freezer, 31)
    assert _issue(hass, entry) is None
