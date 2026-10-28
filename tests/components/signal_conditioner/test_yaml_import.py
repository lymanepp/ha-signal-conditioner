"""Tests for YAML import: the YAML stays the source of truth."""

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.const import CONF_NAME, CONF_SOURCE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.signal_conditioner import CONFIG_SCHEMA
from custom_components.signal_conditioner.const import (
    CONF_AVAILABILITY,
    CONF_DURATION,
    CONF_FOLLOW_SOURCE,
    CONF_MINIMUM,
    CONF_MODE,
    CONF_STALE_AFTER,
    CONF_VALUE_LIMITS,
    CONF_WINDOW,
    DOMAIN,
    LIMIT_MODE_CLAMP,
    LIMIT_MODE_REJECT,
)


def _entry(unique_id: str, source: str, **data: object) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=unique_id,
        unique_id=unique_id,
        source=source,
        data={CONF_NAME: unique_id, CONF_SOURCE: "sensor.raw", **data},
    )


async def test_removed_yaml_key_removes_only_its_imported_entry(
    hass: HomeAssistant,
) -> None:
    kept = _entry("kept", SOURCE_IMPORT)
    dropped = _entry("dropped", SOURCE_IMPORT)
    ui_made = _entry("sensor.raw", SOURCE_USER)
    for entry in (kept, dropped, ui_made):
        entry.add_to_hass(hass)

    assert await async_setup_component(
        hass, DOMAIN, {DOMAIN: {"kept": {CONF_SOURCE: "sensor.raw"}}}
    )
    await hass.async_block_till_done()

    remaining = {e.unique_id for e in hass.config_entries.async_entries(DOMAIN)}
    assert remaining == {"kept", "sensor.raw"}


async def test_reimport_replaces_data_instead_of_merging(
    hass: HomeAssistant,
) -> None:
    """Deleting a block from the YAML removes it from the entry."""
    entry = _entry(
        "garage",
        SOURCE_IMPORT,
        **{
            CONF_WINDOW: {CONF_DURATION: 60, "output": "mean"},
            CONF_VALUE_LIMITS: {CONF_MINIMUM: 0},
        },
    )
    entry.add_to_hass(hass)

    assert await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: {"garage": {CONF_SOURCE: "sensor.other", CONF_NAME: "Garage"}}},
    )
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert CONF_WINDOW not in entries[0].data
    assert CONF_VALUE_LIMITS not in entries[0].data
    assert entries[0].data[CONF_SOURCE] == "sensor.other"
    assert entries[0].title == "Garage"


async def test_unchanged_reimport_leaves_entry_alone(hass: HomeAssistant) -> None:
    """An identical YAML entry causes no update (and so no reload)."""
    yaml = {DOMAIN: {"same": {CONF_SOURCE: "sensor.raw"}}}
    data = {**CONFIG_SCHEMA(yaml)[DOMAIN]["same"], CONF_NAME: "Same"}
    entry = MockConfigEntry(
        domain=DOMAIN, title="Same", unique_id="same", source=SOURCE_IMPORT, data=data
    )
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_update_entry",
        wraps=hass.config_entries.async_update_entry,
    ) as update:
        assert await async_setup_component(hass, DOMAIN, yaml)
        await hass.async_block_till_done()
    assert not any(call.args[0] is entry for call in update.call_args_list)
    assert dict(entry.data) == data


def test_yaml_limit_mode_defaults_to_reject_and_accepts_clamp() -> None:
    validated = CONFIG_SCHEMA(
        {
            DOMAIN: {
                "a": {CONF_SOURCE: "sensor.a", CONF_VALUE_LIMITS: {CONF_MINIMUM: 0}},
                "b": {
                    CONF_SOURCE: "sensor.b",
                    CONF_VALUE_LIMITS: {CONF_MINIMUM: 0, CONF_MODE: LIMIT_MODE_CLAMP},
                },
            }
        }
    )[DOMAIN]
    assert validated["a"][CONF_VALUE_LIMITS][CONF_MODE] == LIMIT_MODE_REJECT
    assert validated["b"][CONF_VALUE_LIMITS][CONF_MODE] == LIMIT_MODE_CLAMP


def test_yaml_availability_block() -> None:
    validated = CONFIG_SCHEMA(
        {
            DOMAIN: {
                "a": {CONF_SOURCE: "sensor.a", CONF_AVAILABILITY: {}},
                "b": {
                    CONF_SOURCE: "sensor.b",
                    CONF_AVAILABILITY: {
                        CONF_FOLLOW_SOURCE: False,
                        CONF_STALE_AFTER: 900,
                    },
                },
            }
        }
    )[DOMAIN]
    assert validated["a"][CONF_AVAILABILITY] == {CONF_FOLLOW_SOURCE: True}
    assert validated["b"][CONF_AVAILABILITY] == {
        CONF_FOLLOW_SOURCE: False,
        CONF_STALE_AFTER: 900,
    }
