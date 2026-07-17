"""Tests for Normify setup and YAML import."""

from homeassistant.const import CONF_SOURCE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.normify.const import CONF_DATA_POINTS, DOMAIN


async def test_yaml_import(hass: HomeAssistant) -> None:
    """YAML definitions are imported into config entries."""
    config = {
        DOMAIN: {
            "garage_humidity": {
                CONF_SOURCE: "sensor.garage_humidity_raw",
                CONF_DATA_POINTS: [[0.0, 0.0], [100.0, 100.0]],
            }
        }
    }

    assert await async_setup_component(hass, DOMAIN, config)
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].unique_id == "garage_humidity"
    assert entries[0].title == "Garage Humidity"
