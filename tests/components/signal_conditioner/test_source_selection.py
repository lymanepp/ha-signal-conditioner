"""Choosing the source: hide once, any domain, attributes, and changes."""

from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_DEVICE_CLASS,
    CONF_ICON,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryHider
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.signal_conditioner.const import (
    CONF_ENABLE_CALIBRATION,
    CONF_ENABLE_LIMITS,
    CONF_ENABLE_ROUNDING,
    CONF_ENABLE_WINDOW,
    CONF_HIDE_SOURCE,
    CONF_STATE_CLASS,
    CONF_USE_ATTRIBUTE,
    DOMAIN,
)

SOURCE = "sensor.raw"


def _register(hass: HomeAssistant, entity_id: str = SOURCE) -> er.EntityRegistry:
    registry = er.async_get(hass)
    domain, object_id = entity_id.split(".", 1)
    registry.async_get_or_create(
        domain, "test", object_id, suggested_object_id=object_id
    )
    hass.states.async_set(entity_id, "5")
    return registry


def _hidden_by(hass: HomeAssistant, entity_id: str = SOURCE) -> object:
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    return entry.hidden_by


async def _reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


def _form(**overrides: object) -> dict[str, Any]:
    return {
        CONF_NAME: "Conditioned",
        CONF_SOURCE: SOURCE,
        CONF_HIDE_SOURCE: False,
        CONF_UNIT_OF_MEASUREMENT: "",
        CONF_DEVICE_CLASS: "",
        CONF_STATE_CLASS: "",
        CONF_ICON: "",
        CONF_ENABLE_LIMITS: False,
        CONF_ENABLE_CALIBRATION: False,
        CONF_ENABLE_WINDOW: False,
        CONF_ENABLE_ROUNDING: False,
        **overrides,
    }


async def _ui_entry(hass: HomeAssistant, **data: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Conditioned",
        data={CONF_NAME: "Conditioned", CONF_SOURCE: SOURCE, **data},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_user_unhide_stands_for_ui_conditioner(hass: HomeAssistant) -> None:
    """Like Home Assistant's helpers: hide once, then the user decides."""
    registry = _register(hass)
    entry = await _ui_entry(hass, **{CONF_HIDE_SOURCE: True})
    assert _hidden_by(hass) is RegistryEntryHider.INTEGRATION

    registry.async_update_entity(SOURCE, hidden_by=None)
    await _reload(hass, entry)
    assert _hidden_by(hass) is None

    # Turning the option off and on is an explicit request to hide again.
    for hide in (False, True):
        result = await entry.start_reconfigure_flow(hass)
        await hass.config_entries.flow.async_configure(
            result["flow_id"], _form(**{CONF_HIDE_SOURCE: hide})
        )
        await hass.async_block_till_done()
    assert _hidden_by(hass) is RegistryEntryHider.INTEGRATION


async def test_existing_hidden_source_is_adopted_on_upgrade(
    hass: HomeAssistant,
) -> None:
    """A source hidden before this change counts as hidden once."""
    registry = _register(hass)
    registry.async_update_entity(SOURCE, hidden_by=RegistryEntryHider.INTEGRATION)
    entry = await _ui_entry(hass, **{CONF_HIDE_SOURCE: True})
    registry.async_update_entity(SOURCE, hidden_by=None)
    await _reload(hass, entry)
    assert _hidden_by(hass) is None


async def test_yaml_conditioner_keeps_its_source_hidden(hass: HomeAssistant) -> None:
    """YAML is declarative: hide_source: true means hidden."""
    registry = _register(hass)
    assert await async_setup_component(
        hass, DOMAIN, {DOMAIN: {"yaml": {CONF_SOURCE: SOURCE, CONF_HIDE_SOURCE: True}}}
    )
    await hass.async_block_till_done()
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert entry.source == SOURCE_IMPORT

    registry.async_update_entity(SOURCE, hidden_by=None)
    await _reload(hass, entry)
    assert _hidden_by(hass) is RegistryEntryHider.INTEGRATION


async def test_attribute_of_any_domain_is_chosen_from_the_source(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(
        "climate.hall", "heat", {"current_temperature": 21.5, "hvac_action": "idle"}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        _form(**{CONF_SOURCE: "climate.hall", CONF_USE_ATTRIBUTE: True}),
    )
    assert result["step_id"] == "attribute"
    selector = result["data_schema"].schema[CONF_ATTRIBUTE]
    assert selector.config["entity_id"] == "climate.hall"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ATTRIBUTE: "current_temperature"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ATTRIBUTE] == "current_temperature"
    await hass.async_block_till_done()
    assert hass.states.get("sensor.conditioned").state == "21.5"


async def test_reconfigure_can_change_the_source(hass: HomeAssistant) -> None:
    """Replacing a failed sensor keeps the conditioner, its entity and history."""
    registry = _register(hass)
    _register(hass, "sensor.replacement")
    entry = await _ui_entry(hass, **{CONF_HIDE_SOURCE: True})
    (conditioner,) = er.async_entries_for_config_entry(registry, entry.entry_id)

    result = await entry.start_reconfigure_flow(hass)
    excluded = result["data_schema"].schema[CONF_SOURCE].config["exclude_entities"]
    assert excluded == [conditioner.entity_id]  # not its own source

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        _form(**{CONF_SOURCE: "sensor.replacement", CONF_HIDE_SOURCE: True}),
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_SOURCE] == "sensor.replacement"
    assert _hidden_by(hass) is None
    assert _hidden_by(hass, "sensor.replacement") is RegistryEntryHider.INTEGRATION

    hass.states.async_set("sensor.replacement", "9")
    await hass.async_block_till_done()
    assert er.async_entries_for_config_entry(registry, entry.entry_id) == [
        registry.async_get(conditioner.entity_id)
    ]
    assert hass.states.get(conditioner.entity_id).state == "9.0"


async def test_reconfigure_offers_the_current_attribute(hass: HomeAssistant) -> None:
    hass.states.async_set("climate.hall", "heat", {"current_temperature": 21.5})
    entry = await _ui_entry(
        hass, **{CONF_SOURCE: "climate.hall", CONF_ATTRIBUTE: "current_temperature"}
    )
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        _form(**{CONF_SOURCE: "climate.hall", CONF_USE_ATTRIBUTE: True}),
    )
    assert result["step_id"] == "attribute"
    (key,) = result["data_schema"].schema
    assert key.default() == "current_temperature"
