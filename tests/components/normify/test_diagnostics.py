"""Tests for Normify diagnostics."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, CONF_SOURCE
from homeassistant.core import HomeAssistant

from custom_components.normify.const import CONF_DATA_POINTS, DOMAIN
from custom_components.normify.diagnostics import async_get_config_entry_diagnostics


async def test_config_entry_diagnostics(hass: HomeAssistant) -> None:
    """Diagnostics include the entry identity and configuration."""
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Garage humidity",
        data={
            CONF_NAME: "Garage humidity",
            CONF_SOURCE: "sensor.garage_humidity_raw",
            CONF_DATA_POINTS: [[0.0, 0.0], [100.0, 100.0]],
        },
        source="user",
        unique_id="garage_humidity",
        discovery_keys={},
        options={},
        subentries_data=[],
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["title"] == "Garage humidity"
    assert diagnostics["entry"]["unique_id"] == "garage_humidity"
    assert diagnostics["entry"]["data"][CONF_SOURCE] == "sensor.garage_humidity_raw"
