"""Live YAML reload regression tests modeled on Smartify's reload safeguards."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.const import CONF_NAME, CONF_SOURCE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.signal_conditioner import EVENT_YAML_RELOADED
from custom_components.signal_conditioner.const import DOMAIN


def _entry(unique_id: str, source: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=unique_id,
        unique_id=unique_id,
        source=source,
        data={CONF_NAME: unique_id, CONF_SOURCE: "sensor.raw"},
    )


async def test_reload_adds_updates_and_removes_yaml_but_preserves_ui(
    hass: HomeAssistant,
) -> None:
    """A live reload is a full reconciliation, not a merge or full restart."""
    keep = _entry("keep", SOURCE_IMPORT)
    drop = _entry("drop", SOURCE_IMPORT)
    ui = _entry("ui", SOURCE_USER)
    for entry in (keep, drop, ui):
        entry.add_to_hass(hass)

    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: {
                "keep": {CONF_SOURCE: "sensor.raw"},
                "drop": {CONF_SOURCE: "sensor.raw"},
            }
        },
    )
    await hass.async_block_till_done()
    reload_events: list[object] = []
    hass.bus.async_listen(EVENT_YAML_RELOADED, reload_events.append)

    yaml = {
        DOMAIN: {
            "keep": {CONF_NAME: "Updated", CONF_SOURCE: "sensor.new"},
            "added": {CONF_SOURCE: "sensor.new"},
        }
    }
    with patch(
        "custom_components.signal_conditioner.reload_helper."
        "async_integration_yaml_config",
        new_callable=AsyncMock,
        return_value=yaml,
    ) as loader:
        await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
    await hass.async_block_till_done()

    loader.assert_awaited_once_with(hass, DOMAIN, raise_on_failure=True)
    remaining = {e.unique_id: e for e in hass.config_entries.async_entries(DOMAIN)}
    assert set(remaining) == {"keep", "added", "ui"}
    assert remaining["keep"].data[CONF_SOURCE] == "sensor.new"
    assert remaining["keep"].title == "Updated"
    assert remaining["added"].source == SOURCE_IMPORT
    assert remaining["ui"].data == ui.data
    assert len(reload_events) == 1


async def test_reload_invalid_yaml_preserves_entries_and_fires_no_event(
    hass: HomeAssistant,
) -> None:
    """Validate the complete replacement before removing any old sensor."""
    current = _entry("current", SOURCE_IMPORT)
    current.add_to_hass(hass)
    assert await async_setup_component(
        hass, DOMAIN, {DOMAIN: {"current": {CONF_SOURCE: "sensor.raw"}}}
    )
    await hass.async_block_till_done()
    reload_events: list[object] = []
    hass.bus.async_listen(EVENT_YAML_RELOADED, reload_events.append)

    with patch(
        "custom_components.signal_conditioner.reload_helper."
        "async_integration_yaml_config",
        new_callable=AsyncMock,
        return_value={
            DOMAIN: {
                "bad": {
                    CONF_SOURCE: "sensor.raw",
                    "window": {"duration": -10},
                }
            }
        },
    ):
        with pytest.raises(Exception):
            await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)

    remaining = {e.unique_id for e in hass.config_entries.async_entries(DOMAIN)}
    assert remaining == {"current"}
    assert reload_events == []


async def test_yaml_collision_cannot_overwrite_a_ui_entry(hass: HomeAssistant) -> None:
    """UI entries take precedence even if their unique ID equals a YAML key."""
    ui = _entry("collision", SOURCE_USER)
    ui.add_to_hass(hass)
    original_data = dict(ui.data)
    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: {
                "collision": {
                    CONF_SOURCE: "sensor.different",
                    CONF_NAME: "Overwritten",
                }
            }
        },
    )
    await hass.async_block_till_done()
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].source == SOURCE_USER
    assert dict(entries[0].data) == original_data


async def test_reload_unchanged_yaml_does_not_update_entries(
    hass: HomeAssistant,
) -> None:
    """No-op reload must not reset an active averaging window."""
    yaml = {DOMAIN: {"stable": {CONF_SOURCE: "sensor.raw"}}}
    assert await async_setup_component(hass, DOMAIN, yaml)
    await hass.async_block_till_done()

    with (
        patch(
            "custom_components.signal_conditioner.reload_helper."
        "async_integration_yaml_config",
            new_callable=AsyncMock,
            return_value=yaml,
        ),
        patch.object(
            hass.config_entries,
            "async_update_entry",
            wraps=hass.config_entries.async_update_entry,
        ) as update,
    ):
        await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
    await hass.async_block_till_done()
    assert not update.called
