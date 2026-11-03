"""Window ends follow the local clock, including daylight saving days."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_NAME, CONF_SOURCE
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.signal_conditioner.const import (
    CONF_DURATION,
    CONF_WINDOW,
    DOMAIN,
)
from custom_components.signal_conditioner.windows import next_window_end

NEW_YORK = ZoneInfo("America/New_York")
KOLKATA = ZoneInfo("Asia/Kolkata")


def _ends(start: datetime, duration: float, zone: ZoneInfo, count: int) -> list[str]:
    """Successive window ends as local wall-clock times."""
    result = []
    at = start
    for _ in range(count):
        at = next_window_end(at, duration, zone)
        assert at.tzinfo is UTC
        result.append(at.astimezone(zone).strftime("%m-%d %H:%M"))
    return result


def _local(zone: ZoneInfo, *args: int, fold: int = 0) -> datetime:
    return datetime(*args, tzinfo=zone, fold=fold).astimezone(UTC)


def test_daily_window_ends_at_local_midnight() -> None:
    """Not UTC midnight (19:00 or 20:00 in New York)."""
    start = _local(NEW_YORK, 2026, 7, 1, 15, 0)
    assert _ends(start, 86400, NEW_YORK, 2) == ["07-02 00:00", "07-03 00:00"]


def test_hourly_window_ends_on_the_local_hour_in_half_hour_zones() -> None:
    start = _local(KOLKATA, 2026, 7, 1, 10, 10)
    assert _ends(start, 3600, KOLKATA, 2) == ["07-01 11:00", "07-01 12:00"]


@pytest.mark.parametrize(
    ("start", "duration", "expected"),
    [
        # Spring forward, 02:00 -> 03:00: a 23-hour day.
        ((2026, 3, 8, 0, 30), 3600, ["03-08 01:00", "03-08 03:00", "03-08 04:00"]),
        ((2026, 3, 7, 12, 0), 86400, ["03-08 00:00", "03-09 00:00"]),
        # Fall back, 02:00 -> 01:00: a 25-hour day, 01:xx happens twice.
        ((2026, 11, 1, 0, 30), 3600, ["11-01 01:00", "11-01 01:00", "11-01 02:00"]),
        ((2026, 10, 31, 12, 0), 86400, ["11-01 00:00", "11-02 00:00"]),
    ],
)
def test_daylight_saving_days(
    start: tuple[int, ...], duration: float, expected: list[str]
) -> None:
    assert _ends(_local(NEW_YORK, *start), duration, NEW_YORK, len(expected)) == (
        expected
    )


def test_short_window_spans_the_repeated_hour_evenly() -> None:
    """Five real minutes apart, also when the clock falls back."""
    start = _local(NEW_YORK, 2026, 11, 1, 1, 52)
    ends = [start]
    for _ in range(3):
        ends.append(next_window_end(ends[-1], 300, NEW_YORK))
    assert [b - a for a, b in zip(ends[1:], ends[2:], strict=False)] == [
        timedelta(minutes=5)
    ] * 2
    assert ends[1].astimezone(NEW_YORK).strftime("%H:%M") == "01:55"
    assert ends[2].astimezone(NEW_YORK).fold == 1  # 01:00 the second time


def test_uneven_duration_restarts_at_midnight() -> None:
    """7-hour windows: 07:00, 14:00, 21:00, then a 3-hour one to midnight."""
    start = _local(NEW_YORK, 2026, 7, 1, 0, 0)
    assert _ends(start, 7 * 3600, NEW_YORK, 5) == [
        "07-01 07:00",
        "07-01 14:00",
        "07-01 21:00",
        "07-02 00:00",
        "07-02 07:00",
    ]


def test_long_windows_keep_local_clock_times_across_dst() -> None:
    """On the spring-forward day the 00:00-07:00 window is 6 real hours."""
    start = _local(NEW_YORK, 2026, 3, 8, 0, 0)
    assert _ends(start, 7 * 3600, NEW_YORK, 3) == [
        "03-08 07:00",
        "03-08 14:00",
        "03-08 21:00",
    ]


def test_end_is_strictly_after_a_boundary() -> None:
    at = _local(NEW_YORK, 2026, 7, 1, 10, 5)
    assert next_window_end(at, 300, NEW_YORK) == at + timedelta(minutes=5)


async def test_sensor_daily_mean_covers_the_local_day(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to(_local(NEW_YORK, 2026, 7, 1, 18, 0))
    hass.states.async_set("sensor.raw", "10")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Daily",
        data={
            CONF_NAME: "Daily",
            CONF_SOURCE: "sensor.raw",
            CONF_WINDOW: {CONF_DURATION: 86400},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async def tick(hours: float) -> None:
        freezer.tick(timedelta(hours=hours))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    await tick(2)  # 20:00 local is midnight UTC: no window ends here
    hass.states.async_set("sensor.raw", "40")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.daily").state == "10.0"
    await tick(4)  # local midnight: 10 for 2 h, 40 for 4 h
    assert float(hass.states.get("sensor.daily").state) == 30.0
