"""Diagnostics for Signal Conditioner."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SOURCE
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the configuration, the source's state and reading counters."""
    sensor = getattr(entry, "runtime_data", None)
    source = hass.states.get(entry.data[CONF_SOURCE])
    return {
        "entry": {
            "title": entry.title,
            "source": entry.source,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "source_state": None if source is None else source.as_dict(),
        "sensor": None
        if sensor is None
        else {
            "entity_id": sensor.entity_id,
            "state": sensor.state,
            "available": sensor.available,
            "readings": sensor.rejections.as_dict(),
        },
    }
