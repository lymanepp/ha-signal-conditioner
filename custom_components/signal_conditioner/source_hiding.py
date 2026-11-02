"""Hide and unhide source entities for the hide_source option.

Ownership lives in the entity registry, not in memory: a source hidden with
``hidden_by=INTEGRATION`` stays that way across restarts, so it is unhidden
only on a real change (entry deleted or disabled, hide_source turned off, or
the source replaced), never on a plain reload or shutdown. A source the user
hid themselves is never touched.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SOURCE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryHider

from .const import CONF_HIDE_SOURCE, DOMAIN


def _hidden_source(data: Mapping[str, Any]) -> str | None:
    """Return the source an entry's data asks to hide, if any."""
    if data.get(CONF_HIDE_SOURCE):
        return str(data[CONF_SOURCE])
    return None


@callback
def async_hide_source(hass: HomeAssistant, source: str) -> bool:
    """Hide a visible source; return False if it is not registered yet."""
    registry = er.async_get(hass)
    registry_entry = registry.async_get(source)
    if registry_entry is None:
        return False
    if registry_entry.hidden_by is None:
        registry.async_update_entity(source, hidden_by=RegistryEntryHider.INTEGRATION)
    return True


@callback
def async_unhide_source(hass: HomeAssistant, source: str, entry_id: str) -> None:
    """Unhide a source we hid, unless another active entry still hides it."""
    for other in hass.config_entries.async_entries(DOMAIN):
        if (
            other.entry_id != entry_id
            and other.disabled_by is None
            and _hidden_source(other.data) == source
        ):
            return
    registry = er.async_get(hass)
    registry_entry = registry.async_get(source)
    if (
        registry_entry is not None
        and registry_entry.hidden_by is RegistryEntryHider.INTEGRATION
    ):
        registry.async_update_entity(source, hidden_by=None)


@callback
def async_update_hidden_source(
    hass: HomeAssistant, entry: ConfigEntry, new_data: Mapping[str, Any]
) -> None:
    """Unhide the old source when an entry stops hiding it.

    Call before the entry's data changes. Hiding the new source happens when
    the reloaded entry is set up.
    """
    old_source = _hidden_source(entry.data)
    if old_source is not None and old_source != _hidden_source(new_data):
        async_unhide_source(hass, old_source, entry.entry_id)


@callback
def async_release_entry_source(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unhide the source of an entry that is being deleted or disabled."""
    if (source := _hidden_source(entry.data)) is not None:
        async_unhide_source(hass, source, entry.entry_id)
