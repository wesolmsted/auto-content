"""Daily micro slots and source rotation (DESIGN.md §7.10, Schedule). Pure."""

from collections.abc import Collection, Sequence
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from engine.db.models import MicroSourceRow


def slots_for_day(now: datetime, post_times: Sequence[time], timezone: str) -> list[datetime]:
    """Each post time on the local date of `now` in `timezone`, as UTC datetimes.

    A post time that falls in a DST gap is read with the offset before the jump; one that
    repeats as clocks fall back is its first occurrence (fold=0).
    """
    zone = ZoneInfo(timezone)
    today = now.astimezone(zone).date()
    return [
        datetime.combine(today, post_time, tzinfo=zone).astimezone(UTC) for post_time in post_times
    ]


def slots_to_fill(
    now: datetime,
    post_times: Sequence[time],
    timezone: str,
    late_grace_minutes: int,
    filled: Collection[datetime],
) -> list[datetime]:
    """Today's slots with no micro render yet that are at most `late_grace_minutes` past."""
    earliest = now - timedelta(minutes=late_grace_minutes)
    return [
        slot
        for slot in slots_for_day(now, post_times, timezone)
        if slot >= earliest and slot not in filled
    ]


def least_recently_used(sources: Sequence[MicroSourceRow]) -> MicroSourceRow | None:
    """The source used longest ago: never used first, ties by lowest id."""
    return min(
        sources,
        key=lambda source: (
            source.last_used_at is not None,
            source.last_used_at or datetime.min.replace(tzinfo=UTC),
            source.id,
        ),
        default=None,
    )


def take_next_text(source: MicroSourceRow) -> str:
    """The source's next on-screen text in file order, cycling; advances `next_text_index`."""
    texts = source.text_options_json
    text = str(texts[source.next_text_index % len(texts)])
    source.next_text_index += 1
    return text
