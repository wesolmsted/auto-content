from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import Settings, load_settings
from engine.db.enums import MicroRenderStatus
from engine.db.models import MicroRenderRow
from engine.micro.notify import (
    micro_notification,
    send_due_micro_notifications,
    supersede_expired_micro,
)
from engine.notify.models import Notification
from tests.fakes.micro import add_micro_set, add_micro_video
from tests.fakes.notify import FakeNotifier

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
# The repo defaults keep quiet hours (19:00-16:00 New York); micro notifications ignore them.
SETTINGS = load_settings(REPO_CONFIG_DIR, env_file=None).model_copy(
    update={"handoff_base_url": "https://mac.tailnet.test/"}
)
# 09:00 in New York: inside quiet hours.
SLOT = datetime(2026, 6, 10, 13, 0, tzinfo=UTC)


def add_render(
    session: Session,
    scheduled_for: datetime = SLOT,
    status: MicroRenderStatus = MicroRenderStatus.READY,
    notified_at: datetime | None = None,
    text: str = "wait for it",
    duration_s: float | None = 9.6,
) -> MicroRenderRow:
    micro_set = add_micro_set(session)
    video = add_micro_video(session, micro_set)
    render = MicroRenderRow(
        source_id=video.id,
        scheduled_for=scheduled_for,
        edit_json={},
        text=text,
        caption="You Dont Dance",
        file_path="/media/renders/micro.mp4" if duration_s else None,
        duration_s=duration_s,
        status=status,
        notified_at=notified_at,
    )
    session.add(render)
    session.commit()
    return render


def statuses(session: Session) -> dict[int, MicroRenderStatus]:
    rows = session.execute(select(MicroRenderRow.id, MicroRenderRow.status)).all()
    return {render_id: status for render_id, status in rows}


def test_notification_names_the_song_length_and_text_and_links_to_the_micro_page(
    db_session: Session,
) -> None:
    render = add_render(db_session, text="POV: the drop", duration_s=9.6)

    assert micro_notification(render, "https://mac.tailnet.test") == Notification(
        title="Micro ready: You Dont Dance",
        body="10s · POV: the drop",
        click_url=f"https://mac.tailnet.test/micro/{render.id}",
        tags=("scissors",),
    )


def test_sends_due_renders_even_in_quiet_hours_and_marks_them(db_session: Session) -> None:
    render = add_render(db_session, scheduled_for=SLOT)
    notifier = FakeNotifier()

    sent = send_due_micro_notifications(db_session, notifier, SETTINGS, SLOT)

    assert sent == 1
    assert notifier.sent == [micro_notification(render, "https://mac.tailnet.test")]
    db_session.refresh(render)
    assert render.notified_at == SLOT


def test_a_render_whose_slot_has_not_come_waits(db_session: Session) -> None:
    render = add_render(db_session, scheduled_for=SLOT)
    notifier = FakeNotifier()

    sent = send_due_micro_notifications(db_session, notifier, SETTINGS, SLOT - timedelta(seconds=1))

    assert sent == 0
    assert notifier.attempted == []
    db_session.refresh(render)
    assert render.notified_at is None


@pytest.mark.parametrize(
    "status",
    [
        MicroRenderStatus.RENDERING,
        MicroRenderStatus.FAILED,
        MicroRenderStatus.DOWNLOADED,
        MicroRenderStatus.SUPERSEDED,
    ],
)
def test_only_ready_renders_are_notified(db_session: Session, status: MicroRenderStatus) -> None:
    add_render(db_session, status=status)
    notifier = FakeNotifier()

    assert send_due_micro_notifications(db_session, notifier, SETTINGS, SLOT) == 0
    assert notifier.attempted == []


def test_an_already_notified_render_is_not_sent_again(db_session: Session) -> None:
    add_render(db_session, notified_at=SLOT)
    notifier = FakeNotifier()

    assert send_due_micro_notifications(db_session, notifier, SETTINGS, SLOT) == 0
    assert notifier.attempted == []


def test_a_failed_send_stays_pending_and_the_others_still_go(db_session: Session) -> None:
    failing = add_render(db_session, text="fails")
    working = add_render(db_session, text="works")
    notifier = FakeNotifier(fail_if=lambda notification: "fails" in notification.body)

    sent = send_due_micro_notifications(db_session, notifier, SETTINGS, SLOT)

    assert sent == 1
    assert len(notifier.attempted) == 2
    db_session.refresh(failing)
    db_session.refresh(working)
    assert failing.notified_at is None
    assert working.notified_at == SLOT
    assert send_due_micro_notifications(db_session, FakeNotifier(), SETTINGS, SLOT) == 1


def test_sending_without_a_handoff_base_url_raises(db_session: Session) -> None:
    add_render(db_session)
    settings = SETTINGS.model_copy(update={"handoff_base_url": None})

    with pytest.raises(ValueError, match="HANDOFF_BASE_URL"):
        send_due_micro_notifications(db_session, FakeNotifier(), settings, SLOT)


def make_settings(expire_hours: int) -> Settings:
    return SETTINGS.model_copy(
        update={"micro": SETTINGS.micro.model_copy(update={"expire_hours": expire_hours})}
    )


def test_expiry_counts_from_notified_at_strictly(db_session: Session) -> None:
    now = SLOT + timedelta(hours=24)
    expired = add_render(db_session, notified_at=now - timedelta(hours=24, seconds=1))
    at_cutoff = add_render(db_session, notified_at=now - timedelta(hours=24))
    recent = add_render(db_session, notified_at=now - timedelta(hours=1))
    never_notified = add_render(db_session, notified_at=None)
    downloaded = add_render(
        db_session,
        status=MicroRenderStatus.DOWNLOADED,
        notified_at=now - timedelta(hours=48),
    )

    assert supersede_expired_micro(db_session, make_settings(24), now) == 1

    db_session.commit()
    assert statuses(db_session) == {
        expired.id: MicroRenderStatus.SUPERSEDED,
        at_cutoff.id: MicroRenderStatus.READY,
        recent.id: MicroRenderStatus.READY,
        never_notified.id: MicroRenderStatus.READY,
        downloaded.id: MicroRenderStatus.DOWNLOADED,
    }


def test_expiry_uses_micro_expire_hours(db_session: Session) -> None:
    now = SLOT + timedelta(hours=10)
    render = add_render(db_session, notified_at=SLOT)

    assert supersede_expired_micro(db_session, make_settings(24), now) == 0
    assert supersede_expired_micro(db_session, make_settings(6), now) == 1
    db_session.refresh(render)
    assert render.status is MicroRenderStatus.SUPERSEDED
