"""Make rejected readings visible.

A rejected reading keeps the value already in force, which is right for an
occasional glitch. But when every reading is rejected (a limit set in the
wrong unit, a source sending text, a broken calibration) the sensor quietly
shows an old value. A rejection streak that lasts raises a Repairs issue,
and counters are available in the config entry's diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# A streak must last this long, over at least this many readings, before it
# is reported: an occasional out-of-range glitch is what limits are for.
REJECTION_ISSUE_SECONDS = 600.0
REJECTION_ISSUE_MIN_READINGS = 3
_ISSUE_PREFIX = "readings_rejected_"


def rejection_issue_id(entry_id: str) -> str:
    """Return the Repairs issue ID for one conditioner's rejection streak."""
    return f"{_ISSUE_PREFIX}{entry_id}"


@dataclass(slots=True)
class _Rejection:
    reason: str
    value: str
    at: datetime


class RejectionMonitor:
    """Count accepted and rejected readings and report lasting rejection."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, name: str, source: str
    ) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._name = name
        self._source = source
        self.accepted = 0
        self.rejected = 0
        self._last: _Rejection | None = None
        self._streak_started: float | None = None
        self._streak_readings = 0
        self._reported = False

    @callback
    def record_rejected(
        self, value: object, reason: str, *, reported_elsewhere: bool = False
    ) -> None:
        """Count a rejected reading; report it once a streak has lasted.

        ``reported_elsewhere``: the cause already has its own Repairs issue
        (a unit mismatch), so it is counted but doesn't raise this one.
        """
        self.rejected += 1
        self._last = _Rejection(reason, str(value), dt_util.utcnow())
        if reported_elsewhere:
            return
        now = time.monotonic()
        if self._streak_started is None:
            self._streak_started = now
            self._streak_readings = 0
        self._streak_readings += 1
        if (
            not self._reported
            and self._streak_readings >= REJECTION_ISSUE_MIN_READINGS
            and now - self._streak_started >= REJECTION_ISSUE_SECONDS
        ):
            self._report(reason, value)

    @callback
    def record_accepted(self) -> None:
        """Count an accepted reading; it ends any rejection streak."""
        self.accepted += 1
        self._streak_started = None
        self._streak_readings = 0
        if self._reported:
            self._reported = False
            async_delete_issue(self._hass, DOMAIN, rejection_issue_id(self._entry_id))
            _LOGGER.info(
                "Signal Conditioner '%s' accepts readings from %s again",
                self._name,
                self._source,
            )

    @callback
    def async_stop(self) -> None:
        """Clear the issue on unload; a continuing streak reports again."""
        if self._reported:
            self._reported = False
            async_delete_issue(self._hass, DOMAIN, rejection_issue_id(self._entry_id))

    def as_dict(self) -> dict[str, Any]:
        """Counters and the last rejection, for diagnostics."""
        return {
            "accepted": self.accepted,
            "rejected": self.rejected,
            "rejecting_now": self._streak_started is not None,
            "rejected_in_a_row": self._streak_readings,
            "last_rejection": (
                None
                if self._last is None
                else {
                    "reason": self._last.reason,
                    "value": self._last.value,
                    "at": self._last.at.isoformat(),
                }
            ),
        }

    @callback
    def _report(self, reason: str, value: object) -> None:
        self._reported = True
        minutes = round((time.monotonic() - (self._streak_started or 0)) / 60)
        _LOGGER.warning(
            "Signal Conditioner '%s' has rejected every reading from %s for %s "
            "minutes; the last one, %s, was rejected: %s",
            self._name,
            self._source,
            minutes,
            value,
            reason,
        )
        async_create_issue(
            self._hass,
            DOMAIN,
            rejection_issue_id(self._entry_id),
            is_fixable=False,
            is_persistent=False,
            severity=IssueSeverity.WARNING,
            translation_key="readings_rejected",
            translation_placeholders={
                "conditioner": self._name,
                "source": self._source,
                "minutes": str(minutes),
                "value": str(value),
                "reason": reason,
            },
        )
