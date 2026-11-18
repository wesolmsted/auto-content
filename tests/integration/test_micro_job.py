import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import MicroSettings, Settings, load_settings
from engine.db.enums import MicroRenderStatus, MicroSetStatus, MicroSourceStatus
from engine.db.models import MicroRenderRow
from engine.micro.job import MicroRenderSummary, run_micro_renders
from engine.micro.models import MicroEdit, MicroSourceMedia
from engine.storage.media_store import LocalMediaStore, MediaKind
from tests.fakes.micro import (
    FakeMicroRenderer,
    add_micro_set,
    add_micro_video,
    fixed_duration_s,
    fixed_edit,
)

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SETTINGS = load_settings(REPO_CONFIG_DIR, env_file=None)
# 06:00 in New York (EDT) on 2026-06-10; the day's slots are 13:00, 17:00 and 22:00 UTC.
MORNING = datetime(2026, 6, 10, 10, 0, tzinfo=UTC)
SLOTS = [datetime(2026, 6, 10, hour, 0, tzinfo=UTC) for hour in (13, 17, 22)]
NEXT_MORNING = MORNING + timedelta(days=1)
NEXT_SLOTS = [slot + timedelta(days=1) for slot in SLOTS]


def run(
    session: Session,
    store: LocalMediaStore,
    now: datetime,
    renderer: FakeMicroRenderer | None = None,
    settings: Settings = SETTINGS,
) -> MicroRenderSummary:
    return run_micro_renders(
        session,
        store,
        settings,
        now,
        random.Random(1),
        renderer=renderer or FakeMicroRenderer(),
        edit_sampler=fixed_edit,
        duration_of=fixed_duration_s,
    )


def mirrored_edit(rng: random.Random, settings: MicroSettings, text: str) -> MicroEdit:
    return fixed_edit(rng, settings, text).model_copy(update={"mirror": True})


def renders(session: Session) -> list[MicroRenderRow]:
    return list(session.scalars(select(MicroRenderRow).order_by(MicroRenderRow.id)))


def test_fills_each_of_todays_slots_with_a_ready_render(
    db_session: Session, tmp_path: Path
) -> None:
    store = LocalMediaStore(tmp_path / "media")
    micro_set = add_micro_set(db_session, texts=("a", "b"))
    video = add_micro_video(db_session, micro_set, duration_s=10.0, has_audio=True)
    renderer = FakeMicroRenderer()

    summary = run(db_session, store, MORNING, renderer)

    assert summary == MicroRenderSummary(slots=3, ready=3, failed=0, no_source=0)
    rows = renders(db_session)
    assert [row.scheduled_for for row in rows] == SLOTS
    assert [row.text for row in rows] == ["a", "b", "a"]
    assert {row.caption for row in rows} == {"You Dont Dance"}
    assert {row.source_id for row in rows} == {video.id}
    for row in rows:
        out_path = store.path_for(MediaKind.RENDERS, f"micro_{row.id}.mp4")
        assert row.status is MicroRenderStatus.READY
        assert row.file_path == str(out_path)
        assert out_path.read_bytes() == b"micro video"
        assert row.duration_s == 9.5
        assert row.attempts == 1
        assert row.error is None
        assert row.ffmpeg_argv_json == ["ffmpeg", "-i", video.local_path, str(out_path)]
        assert row.edit_json == fixed_edit(random.Random(), SETTINGS.micro, row.text).model_dump(
            mode="json"
        )
    assert [call[1] for call in renderer.calls] == [
        MicroSourceMedia(path=Path(video.local_path or ""), duration_s=10.0, has_audio=True)
    ] * 3
    db_session.refresh(micro_set)
    db_session.refresh(video)
    assert micro_set.next_text_index == 3
    assert micro_set.last_used_at == SLOTS[2]
    assert video.last_used_at == SLOTS[2]


def test_the_sets_text_cutoff_goes_into_each_edit(db_session: Session, tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path / "media")
    micro_set = add_micro_set(db_session)
    micro_set.caption = "for a future auto-upload"
    micro_set.text_cutoff_s = 2.4
    db_session.commit()
    add_micro_video(db_session, micro_set)
    renderer = FakeMicroRenderer()

    run(db_session, store, MORNING, renderer)

    assert [call[0].text_cutoff_s for call in renderer.calls] == [2.4, 2.4, 2.4]
    assert [row.edit_json["text_cutoff_s"] for row in renders(db_session)] == [2.4, 2.4, 2.4]
    assert {row.caption for row in renders(db_session)} == {"You Dont Dance"}


def test_a_set_with_mirroring_off_never_mirrors(db_session: Session, tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path / "media")
    lyrics = add_micro_set(db_session, song_title="Lyrics")
    lyrics.mirror_allowed = False
    db_session.commit()
    add_micro_video(db_session, lyrics)
    plain = add_micro_set(db_session, song_title="Plain")
    add_micro_video(db_session, plain)
    renderer = FakeMicroRenderer()

    run_micro_renders(
        db_session,
        store,
        SETTINGS,
        MORNING,
        random.Random(1),
        renderer=renderer,
        edit_sampler=mirrored_edit,
        duration_of=fixed_duration_s,
    )

    mirror_by_caption = [(row.caption, row.edit_json["mirror"]) for row in renders(db_session)]
    assert mirror_by_caption == [("Lyrics", False), ("Plain", True), ("Lyrics", False)]


def test_rerun_fills_nothing_more(db_session: Session, tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path / "media")
    add_micro_video(db_session, add_micro_set(db_session))
    run(db_session, store, MORNING)

    summary = run(db_session, store, MORNING + timedelta(minutes=15))

    assert summary == MicroRenderSummary()
    assert len(renders(db_session)) == 3


def test_a_late_run_skips_slots_past_the_grace(db_session: Session, tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path / "media")
    add_micro_video(db_session, add_micro_set(db_session))

    summary = run(db_session, store, SLOTS[0] + timedelta(minutes=31))

    assert summary == MicroRenderSummary(slots=2, ready=2)
    assert [row.scheduled_for for row in renders(db_session)] == SLOTS[1:]


def test_sets_take_turns_and_keep_rotating_the_next_day(
    db_session: Session, tmp_path: Path
) -> None:
    store = LocalMediaStore(tmp_path / "media")
    first = add_micro_set(db_session, song_title="First", texts=("f1", "f2", "f3"))
    second = add_micro_set(db_session, song_title="Second", texts=("s1", "s2", "s3"))
    first_video = add_micro_video(db_session, first)
    second_video = add_micro_video(db_session, second)

    run(db_session, store, MORNING)
    run(db_session, store, NEXT_MORNING)

    rows = renders(db_session)
    assert [row.scheduled_for for row in rows] == SLOTS + NEXT_SLOTS
    assert [row.caption for row in rows] == [
        "First",
        "Second",
        "First",
        "Second",
        "First",
        "Second",
    ]
    assert [row.source_id for row in rows] == [first_video.id, second_video.id] * 3
    assert [row.text for row in rows] == ["f1", "s1", "f2", "s2", "f3", "s3"]


def test_videos_in_a_set_take_turns(db_session: Session, tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path / "media")
    micro_set = add_micro_set(db_session)
    one = add_micro_video(db_session, micro_set, name="one.mp4")
    two = add_micro_video(db_session, micro_set, name="two.mp4")

    run(db_session, store, MORNING)

    assert [row.source_id for row in renders(db_session)] == [one.id, two.id, one.id]


def test_set_without_a_ready_video_is_skipped(db_session: Session, tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path / "media")
    unready = add_micro_set(db_session, song_title="Unready")
    add_micro_video(db_session, unready, status=MicroSourceStatus.NEW)
    add_micro_video(db_session, unready, status=MicroSourceStatus.FAILED)
    ready = add_micro_set(db_session, song_title="Ready")
    ready_video = add_micro_video(db_session, ready)
    removed = add_micro_set(db_session, song_title="Removed", status=MicroSetStatus.REMOVED)
    add_micro_video(db_session, removed)

    summary = run(db_session, store, MORNING)

    assert summary == MicroRenderSummary(slots=3, ready=3)
    assert {row.source_id for row in renders(db_session)} == {ready_video.id}
    db_session.refresh(unready)
    assert unready.next_text_index == 0
    assert unready.last_used_at is None


def test_no_ready_video_leaves_every_slot_open(db_session: Session, tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path / "media")
    waiting = add_micro_set(db_session, status=MicroSetStatus.WAITING)
    add_micro_video(db_session, waiting)

    summary = run(db_session, store, MORNING)

    assert summary == MicroRenderSummary(slots=3, no_source=3)
    assert renders(db_session) == []
    # The slots are still open, so a later run fills them once a video is ready.
    add_micro_video(db_session, add_micro_set(db_session))
    assert run(db_session, store, MORNING + timedelta(minutes=15)).ready == 3


def test_a_failed_try_is_retried_and_the_render_ends_ready(
    db_session: Session, tmp_path: Path
) -> None:
    store = LocalMediaStore(tmp_path / "media")
    add_micro_video(db_session, add_micro_set(db_session))

    summary = run(db_session, store, MORNING, FakeMicroRenderer(failures=1))

    assert summary == MicroRenderSummary(slots=3, ready=3)
    first = renders(db_session)[0]
    assert first.status is MicroRenderStatus.READY
    assert first.attempts == 2
    assert first.error is None
    assert [row.attempts for row in renders(db_session)[1:]] == [1, 1]


def test_a_render_that_keeps_failing_is_failed_and_its_slot_stays_filled(
    db_session: Session, tmp_path: Path
) -> None:
    store = LocalMediaStore(tmp_path / "media")
    micro_set = add_micro_set(db_session, texts=("a", "b"))
    video = add_micro_video(db_session, micro_set)
    settings = SETTINGS.model_copy(
        update={"render": SETTINGS.render.model_copy(update={"max_attempts": 2})}
    )

    summary = run(db_session, store, MORNING, FakeMicroRenderer(failures=2), settings)

    assert summary == MicroRenderSummary(slots=3, ready=2, failed=1)
    failed, *rest = renders(db_session)
    out_path = store.path_for(MediaKind.RENDERS, f"micro_{failed.id}.mp4")
    assert failed.status is MicroRenderStatus.FAILED
    assert failed.attempts == 2
    assert failed.error == "ffmpeg exited with 1:\nboom"
    assert failed.ffmpeg_argv_json == ["ffmpeg", "-i", video.local_path, str(out_path)]
    assert failed.file_path is None
    assert failed.text == "a"
    assert [row.status for row in rest] == [MicroRenderStatus.READY] * 2
    assert [row.text for row in rest] == ["b", "a"]
    db_session.refresh(micro_set)
    assert micro_set.next_text_index == 3

    assert run(db_session, store, MORNING + timedelta(minutes=15)) == MicroRenderSummary()


def test_an_unexpected_render_error_is_recorded_not_raised(
    db_session: Session, tmp_path: Path
) -> None:
    store = LocalMediaStore(tmp_path / "media")
    add_micro_video(db_session, add_micro_set(db_session))

    def broken_renderer(*args: object) -> list[str]:
        raise ValueError("bad output")

    summary = run_micro_renders(
        db_session,
        store,
        SETTINGS,
        MORNING,
        random.Random(1),
        renderer=broken_renderer,
        edit_sampler=fixed_edit,
        duration_of=fixed_duration_s,
    )

    assert summary == MicroRenderSummary(slots=3, failed=3)
    assert {row.error for row in renders(db_session)} == {"bad output"}
    assert {row.attempts for row in renders(db_session)} == {SETTINGS.render.max_attempts}
