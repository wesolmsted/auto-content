"""Daily micro slots and source rotation (DESIGN.md §7.10, Schedule). Pure."""

from collections.abc import Collection, Sequence
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from engine.db.enums import MicroSetStatus, MicroSourceStatus
from engine.db.models import MicroSetRow, MicroSourceRow

_NEVER = datetime.min.replace(tzinfo=UTC)


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


def pick_set_and_video(
    sets: Sequence[MicroSetRow], videos: Sequence[MicroSourceRow]
) -> tuple[MicroSetRow, MicroSourceRow] | None:
    """The `active` set used least recently that has a `ready` video, and its `ready` video
    used least recently. Never used comes first; ties go to the lowest id."""
    ready_by_set: dict[int, list[MicroSourceRow]] = {}
    for video in videos:
        if video.status is MicroSourceStatus.READY:
            ready_by_set.setdefault(video.set_id, []).append(video)
    candidates = [
        micro_set
        for micro_set in sets
        if micro_set.status is MicroSetStatus.ACTIVE and micro_set.id in ready_by_set
    ]
    if not candidates:
        return None
    chosen_set = min(candidates, key=_rotation_key)
    return chosen_set, min(ready_by_set[chosen_set.id], key=_rotation_key)


def take_next_text(micro_set: MicroSetRow) -> str:
    """The set's next on-screen text in file order, cycling; advances `next_text_index`."""
    texts = micro_set.text_options_json
    text = str(texts[micro_set.next_text_index % len(texts)])
    micro_set.next_text_index += 1
    return text


def _rotation_key(row: MicroSetRow | MicroSourceRow) -> tuple[bool, datetime, int]:
    return (row.last_used_at is not None, row.last_used_at or _NEVER, row.id)
