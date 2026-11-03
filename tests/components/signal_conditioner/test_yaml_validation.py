"""Invalid YAML must not take down the rest of the integration."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_NAME, CONF_SOURCE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.signal_conditioner.const import DOMAIN

BAD_WINDOW = {CONF_SOURCE: "sensor.raw", "window": {"duration": -10}}


def _entry(unique_id: str, source: str, **data: object) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=unique_id,
        unique_id=unique_id,
        source=source,
        data={CONF_NAME: unique_id, CONF_SOURCE: "sensor.raw", **data},
    )


def _issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, "invalid_yaml")


async def _reload(hass: HomeAssistant, yaml: dict) -> None:
    with patch(
        "custom_components.signal_conditioner.reload_helper."
        "async_integration_yaml_config",
        new_callable=AsyncMock,
        return_value=yaml,
    ):
        await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
    await hass.async_block_till_done()


async def test_invalid_key_does_not_block_other_entries(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """One bad key: UI and other YAML entries load, the bad key's entry stays."""
    hass.states.async_set("sensor.raw", "5")
    ui = _entry("sensor.raw", SOURCE_USER)
    broken = _entry("broken", SOURCE_IMPORT, window={"duration": 60})
    for entry in (ui, broken):
        entry.add_to_hass(hass)
    broken_data = dict(broken.data)

    assert await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: {"good": {CONF_SOURCE: "sensor.raw"}, "broken": BAD_WINDOW}},
    )
    await hass.async_block_till_done()

    assert ui.state is ConfigEntryState.LOADED
    by_id = {e.unique_id: e for e in hass.config_entries.async_entries(DOMAIN)}
    assert set(by_id) == {"sensor.raw", "good", "broken"}
    assert by_id["good"].state is ConfigEntryState.LOADED
    # Invalid is not removed: the existing entry keeps its old configuration.
    assert dict(by_id["broken"].data) == broken_data

    issue = _issue(hass)
    assert issue is not None
    assert issue.translation_placeholders == {"keys": "broken"}
    assert "Invalid Signal Conditioner YAML key 'broken'" in caplog.text


async def test_unusable_yaml_block_changes_nothing(hass: HomeAssistant) -> None:
    kept = _entry("kept", SOURCE_IMPORT)
    kept.add_to_hass(hass)

    assert await async_setup_component(hass, DOMAIN, {DOMAIN: ["not", "a", "map"]})
    await hass.async_block_till_done()

    assert [e.unique_id for e in hass.config_entries.async_entries(DOMAIN)] == ["kept"]
    issue = _issue(hass)
    assert issue is not None
    assert issue.translation_placeholders == {"keys": DOMAIN}


async def test_empty_yaml_block_removes_yaml_entries(hass: HomeAssistant) -> None:
    """A bare `signal_conditioner:` key means no YAML conditioners."""
    _entry("old", SOURCE_IMPORT).add_to_hass(hass)

    assert await async_setup_component(hass, DOMAIN, {DOMAIN: None})
    await hass.async_block_till_done()

    assert hass.config_entries.async_entries(DOMAIN) == []
    assert _issue(hass) is None


async def test_reload_reports_invalid_keys_and_clears_when_fixed(
    hass: HomeAssistant,
) -> None:
    assert await async_setup_component(
        hass, DOMAIN, {DOMAIN: {"garage": {CONF_SOURCE: "sensor.raw"}}}
    )
    await hass.async_block_till_done()
    assert _issue(hass) is None

    with pytest.raises(ServiceValidationError) as raised:
        await _reload(hass, {DOMAIN: {"garage": BAD_WINDOW}})
    assert raised.value.translation_key == "invalid_yaml"
    assert "garage" in raised.value.translation_placeholders["errors"]
    assert _issue(hass) is not None
    assert [e.unique_id for e in hass.config_entries.async_entries(DOMAIN)] == [
        "garage"
    ]

    await _reload(hass, {DOMAIN: {"garage": {CONF_SOURCE: "sensor.raw"}}})
    assert _issue(hass) is None


async def test_yaml_entry_cannot_be_reconfigured_in_the_ui(
    hass: HomeAssistant,
) -> None:
    entry = _entry("garage", SOURCE_IMPORT)
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "yaml_managed"
