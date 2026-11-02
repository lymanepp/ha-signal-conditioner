"""Source identity: several conditioners per source, renames, device links."""

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntryState
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_ICON,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
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


def _source_on_device(hass: HomeAssistant) -> dr.DeviceEntry:
    """Register SOURCE on a device owned by another integration."""
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("test", "probe")}
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        "test",
        "raw",
        suggested_object_id="raw",
        config_entry=owner,
        device_id=device.id,
    )
    hass.states.async_set(SOURCE, "5")
    return device


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _setup_yaml(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up a YAML entry with matching YAML, so the import keeps it."""
    entry.add_to_hass(hass)
    assert await async_setup_component(
        hass, DOMAIN, {DOMAIN: {"garage": {CONF_NAME: "Garage", CONF_SOURCE: SOURCE}}}
    )
    await hass.async_block_till_done()


def _conditioner(hass: HomeAssistant, entry: MockConfigEntry) -> er.RegistryEntry:
    """Return the entry's sensor; a device link prefixes its entity ID."""
    (entity_entry,) = er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    )
    return entity_entry


def _ui_entry(**kwargs: object) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Conditioned",
        data={CONF_NAME: "Conditioned", CONF_SOURCE: SOURCE},
        **kwargs,
    )


async def test_one_source_can_have_several_conditioners(hass: HomeAssistant) -> None:
    hass.states.async_set(SOURCE, "5")
    for name in ("Fast", "Slow"):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: name,
                CONF_SOURCE: SOURCE,
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
        assert result["result"].unique_id is None
    await hass.async_block_till_done()

    assert hass.states.get("sensor.fast").state == "5.0"
    assert hass.states.get("sensor.slow").state == "5.0"


async def test_migration_keeps_entity_id_and_frees_the_source(
    hass: HomeAssistant,
) -> None:
    """A 1.0 entry keyed by its source becomes 1.1 without a new entity."""
    hass.states.async_set(SOURCE, "5")
    entry = _ui_entry(unique_id=SOURCE, version=1, minor_version=0)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}.{SOURCE}",
        suggested_object_id="my_conditioner",
        config_entry=entry,
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 1
    assert entry.unique_id is None
    entity_entry = registry.async_get("sensor.my_conditioner")
    assert entity_entry is not None
    assert entity_entry.unique_id == f"{DOMAIN}.{entry.entry_id}"
    assert hass.states.get("sensor.my_conditioner").state == "5.0"
    assert er.async_entries_for_config_entry(registry, entry.entry_id) == [entity_entry]


async def test_migration_keeps_yaml_unique_ids(hass: HomeAssistant) -> None:
    hass.states.async_set(SOURCE, "5")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Garage",
        unique_id="garage",
        source=SOURCE_IMPORT,
        version=1,
        minor_version=0,
        data={CONF_NAME: "Garage", CONF_SOURCE: SOURCE},
    )
    await _setup_yaml(hass, entry)

    assert entry.minor_version == 1
    assert entry.unique_id == "garage"


async def test_entry_from_a_future_version_is_not_loaded(
    hass: HomeAssistant,
) -> None:
    entry = _ui_entry(version=2, minor_version=0)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_ui_conditioner_links_to_source_device(hass: HomeAssistant) -> None:
    device = _source_on_device(hass)
    entry = _ui_entry()
    await _setup(hass, entry)

    assert _conditioner(hass, entry).device_id == device.id


async def test_ui_conditioner_follows_source_rename(hass: HomeAssistant) -> None:
    _source_on_device(hass)
    entry = _ui_entry()
    await _setup(hass, entry)

    er.async_get(hass).async_update_entity(SOURCE, new_entity_id="sensor.renamed")
    hass.states.async_remove(SOURCE)
    hass.states.async_set("sensor.renamed", "7")
    await hass.async_block_till_done()

    assert entry.data[CONF_SOURCE] == "sensor.renamed"
    assert entry.state is ConfigEntryState.LOADED
    hass.states.async_set("sensor.renamed", "8")
    await hass.async_block_till_done()
    assert hass.states.get(_conditioner(hass, entry).entity_id).state == "8.0"


async def test_yaml_conditioner_is_not_linked_and_keeps_its_source(
    hass: HomeAssistant,
) -> None:
    """YAML owns the source reference; a rename can't be written back."""
    _source_on_device(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Garage",
        unique_id="garage",
        source=SOURCE_IMPORT,
        data={CONF_NAME: "Garage", CONF_SOURCE: SOURCE},
    )
    await _setup_yaml(hass, entry)

    assert _conditioner(hass, entry).device_id is None

    er.async_get(hass).async_update_entity(SOURCE, new_entity_id="sensor.renamed")
    await hass.async_block_till_done()
    assert entry.data[CONF_SOURCE] == SOURCE
