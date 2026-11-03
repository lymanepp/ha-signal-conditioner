"""Where time windows end: on the local clock, restarting at local midnight."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
import math

_HOUR = 3600
_DAY = 86400


def _local_time(day: date, seconds: float, time_zone: tzinfo) -> datetime:
    """The UTC instant of a local wall-clock time, ``seconds`` after midnight."""
    wall = datetime.combine(day, time(), tzinfo=time_zone) + timedelta(seconds=seconds)
    return wall.astimezone(UTC)


def next_window_end(after: datetime, duration: float, time_zone: tzinfo) -> datetime:
    """Return the first window end strictly after ``after``, in UTC.

    Windows restart at local midnight, so a 1-day window ends at local
    midnight and a 1-hour window on the hour, also in half-hour time zones.

    Up to an hour, windows are counted in real elapsed time: daylight saving
    shifts are whole hours, so 5-minute windows still end at :00, :05, ...
    and stay 5 minutes long through the repeated hour. Longer windows end at
    local clock times (07:00, 14:00, ...), so on a daylight saving day one of
    them is an hour shorter or longer. A duration that doesn't divide the day
    has a shorter last window before midnight.
    """
    after = after.astimezone(UTC)
    local = after.astimezone(time_zone)
    today = local.date()
    next_midnight = _local_time(today + timedelta(days=1), 0, time_zone)
    if duration <= _HOUR:
        midnight = _local_time(today, 0, time_zone)
        elapsed = (after - midnight).total_seconds()
        end = midnight + timedelta(
            seconds=(math.floor(elapsed / duration) + 1) * duration
        )
        return min(end, next_midnight)

    wall = (
        local.hour * _HOUR + local.minute * 60 + local.second + local.microsecond / 1e6
    )
    step = math.floor(wall / duration) + 1
    while step * duration < _DAY:
        end = _local_time(today, step * duration, time_zone)
        if end > after:
            return end
        step += 1  # a wall time repeated or skipped by daylight saving
    return next_midnight
