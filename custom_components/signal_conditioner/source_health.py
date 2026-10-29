"""Diagnose actionable source configuration problems without noisy Repairs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import EventEntityRegistryUpdatedData
from homeassistant.helpers.event import (
    async_call_later,
    async_track_entity_registry_updated_event,
)
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Time for YAML, templates and other integrations to settle, also on reloads.
_SOURCE_REPAIR_GRACE_SECONDS = 30.0
_ISSUE_PREFIX = "source_reference_"


def source_issue_id(entry_id: str) -> str:
    """Return the stable Repairs ID for one conditioner config entry."""
    return f"{_ISSUE_PREFIX}{entry_id}"


class SourceReferenceMonitor:
    """Report only persistent missing or disabled source entity references.

    An absent state is not automatically a missing source: a registered,
    enabled entity may be temporarily unloaded. Unknown/unavailable states
    are ordinary runtime conditions, never Repairs issues.
    """

    def __init__(
        self, hass: HomeAssistant, entry_id: str, name: str, source: str
    ) -> None:
        self._hass = hass
        self._name = name
        self._source = source
        self._issue_id = source_issue_id(entry_id)
        self._timer: CALLBACK_TYPE | None = None
        self._start_listener: CALLBACK_TYPE | None = None
        self._reported_problem: str | None = None
        self._checked_once = False
        self._stopped = False

    @callback
    def async_start(self, on_remove: Callable[[CALLBACK_TYPE], None]) -> None:
        """Begin watching registry changes and defer checks until HA starts."""
        on_remove(self.async_stop)
        on_remove(
            async_track_entity_registry_updated_event(
                self._hass, [self._source], self._registry_updated
            )
        )
        if self._hass.state is CoreState.running:
            self._check()
        else:
            # No startup false positives if an integration starts slowly.
            self._start_listener = self._hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._async_started
            )

    @callback
    def async_source_changed(self) -> None:
        """Reassess the reference when its state is created or removed."""
        if self._stopped:
            return
        if self._hass.state is CoreState.running:
            self._check()
        elif self._problem() is None:
            self._clear_issue()

    @callback
    def _registry_updated(self, _event: Event[EventEntityRegistryUpdatedData]) -> None:
        """React to source disablement, removal or entity ID changes."""
        if not self._stopped:
            self.async_source_changed()

    @callback
    def _async_started(self, _event: Event) -> None:
        self._start_listener = None
        if not self._stopped:
            self._check()

    @callback
    def _problem(self) -> str | None:
        """Separate invalid references from normal offline states."""
        # A real state always wins, even if a stale registry record says that
        # a differently provided entity with this ID is disabled.
        if self._hass.states.get(self._source) is not None:
            return None  # Includes unknown and unavailable.
        registry_entry = er.async_get(self._hass).async_get(self._source)
        if registry_entry is not None and registry_entry.disabled_by is not None:
            return "disabled"
        if registry_entry is not None:
            return None  # Registered but not currently loaded.
        return "missing"

    @callback
    def _check(self) -> None:
        problem = self._problem()
        if problem is None:
            self._cancel_timer()
            self._clear_issue()
        # A currently raised issue should not be churned on every event.
        elif problem == self._reported_problem:
            self._cancel_timer()
        else:
            self._schedule_validation()

    @callback
    def _schedule_validation(self) -> None:
        self._cancel_timer()
        self._timer = async_call_later(
            self._hass, _SOURCE_REPAIR_GRACE_SECONDS, self._validate
        )

    @callback
    def _validate(self, _now: datetime) -> None:
        self._timer = None
        if self._stopped:
            return
        problem = self._problem()
        if problem is None:
            self._clear_issue()
            return
        if problem == self._reported_problem:
            return
        async_create_issue(
            self._hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            # Re-checkable on startup: don't persist a potentially stale alert.
            is_persistent=False,
            severity=IssueSeverity.ERROR,
            translation_key=f"{problem}_source",
            translation_placeholders={
                "conditioner": self._name,
                "source": self._source,
            },
        )
        self._reported_problem = problem
        _LOGGER.warning(
            "Signal Conditioner '%s' references a %s source entity '%s'",
            self._name,
            problem,
            self._source,
        )

    @callback
    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _clear_issue(self) -> None:
        # Also clear a stale issue retained in the registry across restarts.
        if not self._checked_once or self._reported_problem is not None:
            async_delete_issue(self._hass, DOMAIN, self._issue_id)
            if self._reported_problem is not None:
                _LOGGER.info(
                    "Signal Conditioner '%s' source '%s' is no longer %s",
                    self._name,
                    self._source,
                    self._reported_problem,
                )
            self._reported_problem = None
        self._checked_once = True

    @callback
    def async_stop(self) -> None:
        """Cancel pending callbacks and delete issues on unload/reload."""
        self._stopped = True
        self._cancel_timer()
        if self._start_listener is not None:
            self._start_listener()
            self._start_listener = None
        self._clear_issue()
