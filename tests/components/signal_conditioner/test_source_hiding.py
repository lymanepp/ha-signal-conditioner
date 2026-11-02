"""Tests for hide_source ownership across restarts, reloads, and changes."""

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntryDisabler
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_ICON,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import (
    EVENT_ENTITY_REGISTRY_UPDATED,
    RegistryEntryHider,
)
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.signal_conditioner.const import (
    CONF_ENABLE_CALIBRATION,
    CONF_ENABLE_LIMITS,
    CONF_ENABLE_ROUNDING,
    CONF_ENABLE_WINDOW,
    CONF_HIDE_SOURCE,
    CONF_STATE_CLASS,
    DOMAIN,
)

SOURCE = "sensor.raw"


def _register(
    hass: HomeAssistant,
    entity_id: str = SOURCE,
    hidden_by: RegistryEntryHider | None = None,
) -> er.EntityRegistry:
    registry = er.async_get(hass)
    object_id = entity_id.split(".", 1)[1]
    registry.async_get_or_create(
        "sensor", "test", object_id, suggested_object_id=object_id
    )
    if hidden_by is not None:
        registry.async_update_entity(entity_id, hidden_by=hidden_by)
    hass.states.async_set(entity_id, "5")
    return registry


def _hidden_by(hass: HomeAssistant, entity_id: str = SOURCE) -> object:
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    return registry_entry.hidden_by


async def _add(
    hass: HomeAssistant, unique_id: str = "a", *, hide: bool = True
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=unique_id,
        unique_id=unique_id,
        data={CONF_NAME: unique_id, CONF_SOURCE: SOURCE, CONF_HIDE_SOURCE: hide},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_source_hidden_before_restart_is_released_on_delete(
    hass: HomeAssistant,
) -> None:
    """After a restart the registry, not memory, says who hid the source."""
    # The previous run hid the source; nothing about that is in memory now.
    _register(hass, hidden_by=RegistryEntryHider.INTEGRATION)
    entry = await _add(hass)
    assert _hidden_by(hass) is RegistryEntryHider.INTEGRATION

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _hidden_by(hass) is None


async def test_reload_does_not_unhide_and_rehide(hass: HomeAssistant) -> None:
    _register(hass)
    entry = await _add(hass)
    assert _hidden_by(hass) is RegistryEntryHider.INTEGRATION

    updates: list[Event] = []
    hass.bus.async_listen(EVENT_ENTITY_REGISTRY_UPDATED, updates.append)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _hidden_by(hass) is RegistryEntryHider.INTEGRATION
    assert [e for e in updates if e.data.get("entity_id") == SOURCE] == []


async def test_disable_unhides_and_enable_hides_again(hass: HomeAssistant) -> None:
    _register(hass)
    entry = await _add(hass)

    assert await hass.config_entries.async_set_disabled_by(
        entry.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert _hidden_by(hass) is None

    assert await hass.config_entries.async_set_disabled_by(entry.entry_id, None)
    await hass.async_block_till_done()
    assert _hidden_by(hass) is RegistryEntryHider.INTEGRATION


async def test_reconfigure_turning_hide_off_unhides_after_restart(
    hass: HomeAssistant,
) -> None:
    _register(hass, hidden_by=RegistryEntryHider.INTEGRATION)
    entry = await _add(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "a",
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
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert _hidden_by(hass) is None


async def test_yaml_source_change_unhides_old_and_hides_new(
    hass: HomeAssistant,
) -> None:
    _register(hass, hidden_by=RegistryEntryHider.INTEGRATION)
    _register(hass, "sensor.new_raw")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Garage",
        unique_id="garage",
        source=SOURCE_IMPORT,
        data={CONF_NAME: "Garage", CONF_SOURCE: SOURCE, CONF_HIDE_SOURCE: True},
    )
    entry.add_to_hass(hass)

    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: {
                "garage": {
                    CONF_NAME: "Garage",
                    CONF_SOURCE: "sensor.new_raw",
                    CONF_HIDE_SOURCE: True,
                }
            }
        },
    )
    await hass.async_block_till_done()

    assert _hidden_by(hass) is None
    assert _hidden_by(hass, "sensor.new_raw") is RegistryEntryHider.INTEGRATION


async def test_shared_source_stays_hidden_until_last_hider_is_deleted(
    hass: HomeAssistant,
) -> None:
    _register(hass)
    first = await _add(hass, "first")
    second = await _add(hass, "second")

    await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()
    assert _hidden_by(hass) is RegistryEntryHider.INTEGRATION

    await hass.config_entries.async_remove(second.entry_id)
    await hass.async_block_till_done()
    assert _hidden_by(hass) is None


async def test_source_hidden_by_user_is_never_unhidden(hass: HomeAssistant) -> None:
    _register(hass, hidden_by=RegistryEntryHider.USER)
    entry = await _add(hass)
    assert _hidden_by(hass) is RegistryEntryHider.USER

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _hidden_by(hass) is RegistryEntryHider.USER
