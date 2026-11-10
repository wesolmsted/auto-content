from datetime import UTC, datetime, time
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import QuietHours, Settings, load_settings
from engine.db.enums import Platform, RenderStatus, SnippetType
from engine.db.models import RecipeRow, RenderRow, SnippetRow, SongRow
from engine.notify.handoff import send_pending_handoff_notifications
from engine.notify.models import Notification
from tests.fakes.notify import FakeNotifier

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
BASE_SETTINGS = load_settings(REPO_CONFIG_DIR, env_file=None)
# The fixed recipe cutting every beat with the high-contrast look, 7 s long.
EVERY_BEAT_RECIPE = BASE_SETTINGS.recipes.fixed[2]
NOON_UTC = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
EARLIER = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)


def make_settings(quiet_hours: QuietHours | None = None) -> Settings:
    return BASE_SETTINGS.model_copy(
        update={
            "handoff_base_url": "https://mac.tailnet.test/",
            "notify": BASE_SETTINGS.notify.model_copy(
                update={"timezone": "UTC", "quiet_hours": quiet_hours}
            ),
        }
    )


def add_render(
    session: Session,
    song_title: str = "Night Drive",
    status: RenderStatus = RenderStatus.READY,
    notified_at: datetime | None = None,
    targets: tuple[Platform, ...] = (Platform.IG_TRIAL,),
    duration_s: float = 7.0,
) -> RenderRow:
    song = SongRow(title=song_title, audio_path=f"{song_title}.wav")
    session.add(song)
    session.flush()
    snippet = SnippetRow(song_id=song.id, snippet_type=SnippetType.DROP, start_s=30, end_s=37)
    if session.get(RecipeRow, EVERY_BEAT_RECIPE.id) is None:
        session.add(
            RecipeRow(id=EVERY_BEAT_RECIPE.id, params_json=EVERY_BEAT_RECIPE.canonical_params())
        )
    session.add(snippet)
    session.flush()
    render = RenderRow(
        recipe_id=EVERY_BEAT_RECIPE.id,
        snippet_id=snippet.id,
        timeline_json={},
        file_path=f"renders/{song_title}.mp4",
        duration_s=duration_s,
        targets_json=[str(target) for target in targets],
        status=status,
        notified_at=notified_at,
    )
    session.add(render)
    session.flush()
    return render


def notified_at_by_id(session: Session) -> dict[int, datetime | None]:
    rows = session.execute(select(RenderRow.id, RenderRow.notified_at)).all()
    return {render_id: notified_at for render_id, notified_at in rows}


def test_sends_one_notification_per_ready_unnotified_render_and_marks_it(
    db_session: Session,
) -> None:
    render = add_render(db_session, targets=(Platform.IG_TRIAL, Platform.TIKTOK))
    notifier = FakeNotifier()

    sent = send_pending_handoff_notifications(db_session, notifier, make_settings(), NOON_UTC)

    assert sent == 1
    assert notifier.sent == [
        Notification(
            title="Hand-off ready: Night Drive · every beat, high contrast",
            body="7s for IG Trial + TikTok",
            click_url=f"https://mac.tailnet.test/handoff/{render.id}",
            tags=("clapper", "ig_trial", "tiktok"),
        )
    ]
    assert notified_at_by_id(db_session) == {render.id: NOON_UTC}


def test_skips_renders_already_notified_or_not_ready(db_session: Session) -> None:
    pending = add_render(db_session, song_title="Pending")
    notified = add_render(db_session, song_title="Notified", notified_at=EARLIER)
    rendering = add_render(db_session, song_title="Rendering", status=RenderStatus.RENDERING)
    approved = add_render(db_session, song_title="Approved", status=RenderStatus.APPROVED)
    notifier = FakeNotifier()

    sent = send_pending_handoff_notifications(db_session, notifier, make_settings(), NOON_UTC)

    assert sent == 1
    assert [n.click_url for n in notifier.sent] == [
        f"https://mac.tailnet.test/handoff/{pending.id}"
    ]
    assert notified_at_by_id(db_session) == {
        pending.id: NOON_UTC,
        notified.id: EARLIER,
        rendering.id: None,
        approved.id: None,
    }


def test_a_failed_send_stays_unnotified_and_the_others_still_go_out(
    db_session: Session,
) -> None:
    first = add_render(db_session, song_title="First")
    failing = add_render(db_session, song_title="Failing")
    last = add_render(db_session, song_title="Last")
    notifier = FakeNotifier(fail_if=lambda notification: "Failing" in notification.title)

    sent = send_pending_handoff_notifications(db_session, notifier, make_settings(), NOON_UTC)

    assert sent == 2
    assert len(notifier.attempted) == 3
    assert notified_at_by_id(db_session) == {
        first.id: NOON_UTC,
        failing.id: None,
        last.id: NOON_UTC,
    }

    retry = FakeNotifier()
    assert send_pending_handoff_notifications(db_session, retry, make_settings(), NOON_UTC) == 1
    assert [n.click_url for n in retry.sent] == [f"https://mac.tailnet.test/handoff/{failing.id}"]


def test_inside_quiet_hours_nothing_is_sent_and_renders_stay_pending(
    db_session: Session,
) -> None:
    render = add_render(db_session)
    notifier = FakeNotifier()
    settings = make_settings(QuietHours(start=time(11), end=time(13)))

    sent = send_pending_handoff_notifications(db_session, notifier, settings, NOON_UTC)

    assert sent == 0
    assert notifier.attempted == []
    assert notified_at_by_id(db_session) == {render.id: None}

    after_quiet_hours = datetime(2026, 6, 10, 13, 0, tzinfo=UTC)
    assert (
        send_pending_handoff_notifications(db_session, notifier, settings, after_quiet_hours) == 1
    )


@pytest.mark.parametrize(
    ("now_utc", "expected_sent"),
    [
        (datetime(2026, 6, 10, 10, 0, tzinfo=UTC), 0),  # 06:00 EDT
        (datetime(2026, 6, 10, 21, 30, tzinfo=UTC), 1),  # 17:30 EDT
        (datetime(2026, 6, 10, 23, 30, tzinfo=UTC), 0),  # 19:30 EDT
        (datetime(2026, 11, 2, 21, 30, tzinfo=UTC), 1),  # 16:30 EST, after fall back
        (datetime(2026, 11, 2, 0, 30, tzinfo=UTC), 0),  # 19:30 EST
    ],
)
def test_owner_posting_window_in_new_york(
    db_session: Session, now_utc: datetime, expected_sent: int
) -> None:
    add_render(db_session)
    settings = make_settings(QuietHours(start=time(19), end=time(16)))
    settings = settings.model_copy(
        update={"notify": settings.notify.model_copy(update={"timezone": "America/New_York"})}
    )
    notifier = FakeNotifier()
    sent = send_pending_handoff_notifications(db_session, notifier, settings, now_utc)
    assert sent == expected_sent
    assert len(notifier.sent) == expected_sent


def test_nothing_pending_sends_nothing(db_session: Session) -> None:
    notifier = FakeNotifier()
    assert send_pending_handoff_notifications(db_session, notifier, make_settings(), NOON_UTC) == 0
    assert notifier.attempted == []


def test_missing_handoff_base_url_is_a_clear_error(db_session: Session) -> None:
    add_render(db_session)
    settings = make_settings().model_copy(update={"handoff_base_url": None})
    with pytest.raises(ValueError, match="HANDOFF_BASE_URL"):
        send_pending_handoff_notifications(db_session, FakeNotifier(), settings, NOON_UTC)
