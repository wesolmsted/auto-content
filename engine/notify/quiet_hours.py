"""Quiet hours (DESIGN.md §7.5): notifications due inside the window are held until it ends."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from engine.config import NotifySettings


def is_quiet(moment: datetime, settings: NotifySettings) -> bool:
    """Whether `moment` falls in the quiet-hours window `[start, end)`, read as local time in
    `settings.timezone`. A window with `start > end` wraps past midnight; `start == end` is
    empty."""
    window = settings.quiet_hours
    if window is None or window.start == window.end:
        return False
    local_time = moment.astimezone(ZoneInfo(settings.timezone)).time()
    if window.start < window.end:
        return window.start <= local_time < window.end
    return local_time >= window.start or local_time < window.end


def next_send_time(now: datetime, settings: NotifySettings) -> datetime:
    """The earliest moment at or after `now` outside quiet hours, in UTC."""
    send_at = now.astimezone(UTC)
    # A window end that falls in a DST gap can land back inside an almost-24-hour window.
    while is_quiet(send_at, settings):
        send_at = _end_of_window_after(send_at, settings)
    return send_at


def _end_of_window_after(moment: datetime, settings: NotifySettings) -> datetime:
    assert settings.quiet_hours is not None
    end = settings.quiet_hours.end
    zone = ZoneInfo(settings.timezone)
    local = moment.astimezone(zone)
    end_date = local.date() if local.time() < end else local.date() + timedelta(days=1)
    # fold=1 picks the second occurrence when the end time repeats as clocks fall back.
    for fold in (0, 1):
        candidate = datetime.combine(end_date, end, tzinfo=zone).replace(fold=fold).astimezone(UTC)
        if candidate > moment:
            return candidate
    raise AssertionError("the window end on its own day or the next is always later")
