from datetime import UTC, datetime, time

import pytest

from engine.db.models import MicroSourceRow
from engine.micro.schedule import (
    least_recently_used,
    slots_for_day,
    slots_to_fill,
    take_next_text,
)

NEW_YORK = "America/New_York"
POST_TIMES = (time(9, 0), time(13, 0), time(18, 0))
GRACE_MINUTES = 30


def utc(year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


# 2026-06-10 is in EDT (UTC-4): the slots are 13:00, 17:00 and 22:00 UTC.
SUMMER_SLOTS = [utc(2026, 6, 10, 13), utc(2026, 6, 10, 17), utc(2026, 6, 10, 22)]


def fill(now: datetime, filled: set[datetime] | None = None) -> list[datetime]:
    return slots_to_fill(now, POST_TIMES, NEW_YORK, GRACE_MINUTES, filled or set())


def test_early_morning_run_returns_all_of_todays_slots_in_utc() -> None:
    assert fill(utc(2026, 6, 10, 10)) == SUMMER_SLOTS


def test_slot_exactly_at_the_grace_boundary_is_still_filled() -> None:
    assert fill(utc(2026, 6, 10, 13, 30)) == SUMMER_SLOTS


def test_slot_one_second_past_the_grace_is_skipped() -> None:
    assert fill(utc(2026, 6, 10, 13, 30, 1)) == SUMMER_SLOTS[1:]


def test_slots_with_a_render_are_not_returned() -> None:
    filled = {SUMMER_SLOTS[0], SUMMER_SLOTS[2]}
    assert fill(utc(2026, 6, 10, 10), filled) == [SUMMER_SLOTS[1]]


def test_filled_slots_from_another_day_do_not_count() -> None:
    assert fill(utc(2026, 6, 10, 10), {utc(2026, 6, 9, 13)}) == SUMMER_SLOTS


def test_run_after_the_last_slot_returns_nothing() -> None:
    assert fill(utc(2026, 6, 10, 22, 31)) == []


def test_today_is_the_local_date_not_the_utc_date() -> None:
    # 02:00 UTC on June 11 is 22:00 on June 10 in New York: every slot that day has passed.
    assert fill(utc(2026, 6, 11, 2)) == []


def test_zero_grace_keeps_a_slot_only_up_to_its_exact_time() -> None:
    now = SUMMER_SLOTS[0]
    assert slots_to_fill(now, POST_TIMES, NEW_YORK, 0, set()) == SUMMER_SLOTS
    later = utc(2026, 6, 10, 13, 0, 1)
    assert slots_to_fill(later, POST_TIMES, NEW_YORK, 0, set()) == SUMMER_SLOTS[1:]


def test_spring_forward_day_uses_the_new_offset_for_slots_after_the_change() -> None:
    # 2026-03-08: clocks jump from 02:00 EST to 03:00 EDT. At 01:00 EST (06:00 UTC) the slots
    # are already on EDT.
    assert slots_for_day(utc(2026, 3, 8, 6), POST_TIMES, NEW_YORK) == [
        utc(2026, 3, 8, 13),
        utc(2026, 3, 8, 17),
        utc(2026, 3, 8, 22),
    ]
    assert slots_for_day(utc(2026, 3, 7, 12), POST_TIMES, NEW_YORK) == [
        utc(2026, 3, 7, 14),
        utc(2026, 3, 7, 18),
        utc(2026, 3, 7, 23),
    ]


def test_fall_back_day_uses_the_new_offset_for_slots_after_the_change() -> None:
    # 2026-11-01: clocks fall back from 02:00 EDT to 01:00 EST. At 01:00 EDT (05:00 UTC) the
    # slots are on EST.
    assert slots_for_day(utc(2026, 11, 1, 5), POST_TIMES, NEW_YORK) == [
        utc(2026, 11, 1, 14),
        utc(2026, 11, 1, 18),
        utc(2026, 11, 1, 23),
    ]


def test_slot_in_the_spring_forward_gap_lands_an_hour_later_on_the_wall_clock() -> None:
    # 02:30 doesn't exist on 2026-03-08; read with the EST offset it is 07:30 UTC (03:30 EDT).
    assert slots_for_day(utc(2026, 3, 8, 6), (time(2, 30),), NEW_YORK) == [utc(2026, 3, 8, 7, 30)]


def test_slot_in_the_repeated_fall_back_hour_is_its_first_occurrence() -> None:
    # 01:30 happens twice on 2026-11-01; the first time is 01:30 EDT = 05:30 UTC.
    assert slots_for_day(utc(2026, 11, 1, 4), (time(1, 30),), NEW_YORK) == [utc(2026, 11, 1, 5, 30)]


def test_utc_timezone_slots_match_the_post_times() -> None:
    assert slots_for_day(utc(2026, 6, 10, 23, 59), POST_TIMES, "UTC") == [
        utc(2026, 6, 10, 9),
        utc(2026, 6, 10, 13),
        utc(2026, 6, 10, 18),
    ]


def source(source_id: int, last_used_at: datetime | None = None) -> MicroSourceRow:
    return MicroSourceRow(
        id=source_id,
        drive_file_id=f"d{source_id}",
        name=f"clip{source_id}_micro.mp4",
        text_options_json=["one", "two", "three"],
        next_text_index=0,
        last_used_at=last_used_at,
    )


def test_no_sources_means_no_choice() -> None:
    assert least_recently_used([]) is None


def test_never_used_source_comes_before_any_used_one() -> None:
    used = source(1, last_used_at=utc(2026, 6, 1, 12))
    unused = source(2)
    assert least_recently_used([used, unused]) is unused


def test_least_recently_used_source_wins() -> None:
    recent = source(1, last_used_at=utc(2026, 6, 9, 12))
    older = source(2, last_used_at=utc(2026, 6, 8, 12))
    newest = source(3, last_used_at=utc(2026, 6, 10, 12))
    assert least_recently_used([recent, older, newest]) is older


@pytest.mark.parametrize("last_used_at", [None, utc(2026, 6, 9, 12)])
def test_ties_go_to_the_lowest_id(last_used_at: datetime | None) -> None:
    sources = [source(7, last_used_at), source(3, last_used_at), source(5, last_used_at)]
    chosen = least_recently_used(sources)
    assert chosen is not None
    assert chosen.id == 3


def test_take_next_text_returns_texts_in_order_and_cycles() -> None:
    row = source(1)
    assert [take_next_text(row) for _ in range(5)] == ["one", "two", "three", "one", "two"]
    assert row.next_text_index == 5


def test_take_next_text_wraps_an_index_past_the_end() -> None:
    row = source(1)
    row.next_text_index = 7
    assert take_next_text(row) == "two"
    assert row.next_text_index == 8


def test_single_text_repeats() -> None:
    row = source(1)
    row.text_options_json = ["only"]
    assert [take_next_text(row) for _ in range(3)] == ["only", "only", "only"]
