from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.config import NotifySettings, QuietHours, load_settings
from engine.notify.quiet_hours import is_quiet, next_send_time

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
DEFAULT_NOTIFY = load_settings(REPO_CONFIG_DIR, env_file=None).notify
NEW_YORK = ZoneInfo("America/New_York")


def notify_settings(start: time, end: time, timezone: str = "America/New_York") -> NotifySettings:
    return DEFAULT_NOTIFY.model_copy(
        update={"timezone": timezone, "quiet_hours": QuietHours(start=start, end=end)}
    )


OVERNIGHT = notify_settings(time(22), time(8))
DAYTIME = notify_settings(time(12), time(14))


def new_york(
    year: int, month: int, day: int, hour: int, minute: int = 0, fold: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=NEW_YORK, fold=fold)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (new_york(2026, 6, 10, 21, 59), new_york(2026, 6, 10, 21, 59)),
        (new_york(2026, 6, 10, 22), new_york(2026, 6, 11, 8)),
        (new_york(2026, 6, 10, 23, 30), new_york(2026, 6, 11, 8)),
        (new_york(2026, 6, 11, 3), new_york(2026, 6, 11, 8)),
        (new_york(2026, 6, 11, 8), new_york(2026, 6, 11, 8)),
        (new_york(2026, 6, 11, 12), new_york(2026, 6, 11, 12)),
    ],
)
def test_window_crossing_midnight_holds_until_the_window_ends(
    now: datetime, expected: datetime
) -> None:
    assert next_send_time(now.astimezone(UTC), OVERNIGHT) == expected.astimezone(UTC)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (new_york(2026, 6, 10, 11, 59), new_york(2026, 6, 10, 11, 59)),
        (new_york(2026, 6, 10, 12), new_york(2026, 6, 10, 14)),
        (new_york(2026, 6, 10, 13, 59), new_york(2026, 6, 10, 14)),
        (new_york(2026, 6, 10, 14), new_york(2026, 6, 10, 14)),
        (new_york(2026, 6, 10, 23), new_york(2026, 6, 10, 23)),
    ],
)
def test_window_within_one_day(now: datetime, expected: datetime) -> None:
    assert next_send_time(now.astimezone(UTC), DAYTIME) == expected.astimezone(UTC)


def test_result_is_utc() -> None:
    sent = next_send_time(new_york(2026, 6, 10, 23), OVERNIGHT)
    assert sent.tzinfo == UTC
    assert sent == datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def test_window_is_read_in_the_configured_timezone() -> None:
    now = datetime(2026, 6, 10, 23, 0, tzinfo=UTC)  # 19:00 in New York, 08:00 in Tokyo
    assert next_send_time(now, OVERNIGHT) == now
    tokyo = notify_settings(time(7), time(9), timezone="Asia/Tokyo")
    assert next_send_time(now, tokyo) == datetime(2026, 6, 11, 0, 0, tzinfo=UTC)


def test_no_quiet_hours_or_an_empty_window_never_holds() -> None:
    now = new_york(2026, 6, 10, 23)
    assert next_send_time(now, DEFAULT_NOTIFY.model_copy(update={"quiet_hours": None})) == now
    assert next_send_time(now, notify_settings(time(22), time(22))) == now


def test_window_ending_in_the_spring_forward_gap_ends_soon_after_the_clock_jumps() -> None:
    # 2026-03-08 in New York: 02:00 EST jumps to 03:00 EDT, so 02:30 never happens.
    settings = notify_settings(time(1), time(2, 30))
    now = new_york(2026, 3, 8, 1, 15)
    sent = next_send_time(now, settings)
    assert sent > now
    assert sent.astimezone(NEW_YORK).time() >= time(3)
    assert sent <= new_york(2026, 3, 8, 3, 30)
    assert not is_quiet(sent, settings)


def test_window_ending_in_the_repeated_fall_back_hour() -> None:
    # 2026-11-01 in New York: 01:00-02:00 happens twice, first EDT, then EST.
    settings = notify_settings(time(0), time(1, 30))
    first_pass = new_york(2026, 11, 1, 1, 10)
    # Ambiguous local times never compare equal across zones (PEP 495), so compare in UTC.
    assert next_send_time(first_pass, settings) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)

    second_pass = new_york(2026, 11, 1, 1, 10, fold=1)
    sent = next_send_time(second_pass, settings)
    assert sent - second_pass.astimezone(UTC) == timedelta(minutes=20)


minutes_of_day = st.integers(min_value=0, max_value=24 * 60 - 1).map(
    lambda minutes: time(minutes // 60, minutes % 60)
)
instants = st.datetimes(
    min_value=datetime(2026, 1, 1), max_value=datetime(2028, 12, 31), timezones=st.just(UTC)
)


@given(
    now=instants,
    start=minutes_of_day,
    end=minutes_of_day,
    timezone=st.sampled_from(["America/New_York", "Europe/London", "Australia/Sydney", "UTC"]),
)
def test_send_time_is_never_before_now_and_never_inside_the_window(
    now: datetime, start: time, end: time, timezone: str
) -> None:
    settings = notify_settings(start, end, timezone)
    sent = next_send_time(now, settings)
    assert sent >= now
    assert sent - now <= timedelta(days=1, hours=1)
    assert not is_quiet(sent, settings)
    if not is_quiet(now, settings):
        assert sent == now
