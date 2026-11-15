"""A day and a bit of `ace micro run` (render, then expire and notify) on fakes."""

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import load_settings
from engine.db.enums import MicroRenderStatus
from engine.db.models import MicroRenderRow
from engine.micro.job import run_micro_renders
from engine.micro.notify import send_due_micro_notifications, supersede_expired_micro
from engine.storage.media_store import LocalMediaStore
from tests.fakes.micro import (
    FakeMicroRenderer,
    add_micro_set,
    add_micro_video,
    fixed_duration_s,
    fixed_edit,
)
from tests.fakes.notify import FakeNotifier

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SETTINGS = load_settings(REPO_CONFIG_DIR, env_file=None).model_copy(
    update={"handoff_base_url": "https://mac.tailnet.test"}
)


def utc(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=UTC)


@dataclass
class Tick:
    rendered: int
    superseded: int
    sent: list[str]


def test_a_day_of_micro_runs(db_session: Session, tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path / "media")
    notifier = FakeNotifier()
    rng = random.Random(7)
    micro_set = add_micro_set(db_session, song_title="You Dont Dance", texts=("a", "b"))
    add_micro_video(db_session, micro_set)

    def tick(now: datetime) -> Tick:
        summary = run_micro_renders(
            db_session,
            store,
            SETTINGS,
            now,
            rng,
            renderer=FakeMicroRenderer(),
            edit_sampler=fixed_edit,
            duration_of=fixed_duration_s,
        )
        before = len(notifier.sent)
        superseded = supersede_expired_micro(db_session, SETTINGS, now)
        send_due_micro_notifications(db_session, notifier, SETTINGS, now)
        db_session.commit()
        return Tick(summary.ready, superseded, [n.click_url or "" for n in notifier.sent[before:]])

    def url(render_id: int) -> str:
        return f"https://mac.tailnet.test/micro/{render_id}"

    # New York is on EDT (UTC-4): the slots are 13:00, 17:00 and 22:00 UTC.
    assert tick(utc(10, 10)) == Tick(rendered=3, superseded=0, sent=[])  # 06:00
    assert tick(utc(10, 12, 59)) == Tick(rendered=0, superseded=0, sent=[])
    assert tick(utc(10, 13)) == Tick(rendered=0, superseded=0, sent=[url(1)])  # 09:00
    assert tick(utc(10, 13, 15)) == Tick(rendered=0, superseded=0, sent=[])
    assert tick(utc(10, 17, 5)) == Tick(rendered=0, superseded=0, sent=[url(2)])  # 13:05
    assert tick(utc(10, 22)) == Tick(rendered=0, superseded=0, sent=[url(3)])  # 18:00
    assert [n.body for n in notifier.sent] == ["10s · a", "10s · b", "10s · a"]
    assert {n.title for n in notifier.sent} == {"Micro ready: You Dont Dance"}

    # The next morning makes the next day's renders; texts keep cycling across days.
    assert tick(utc(11, 10)) == Tick(rendered=3, superseded=0, sent=[])
    # Exactly 24 h after its notification, render 1 is still ready (the cutoff is strict).
    assert tick(utc(11, 13)) == Tick(rendered=0, superseded=0, sent=[url(4)])
    assert tick(utc(11, 13, 1)) == Tick(rendered=0, superseded=1, sent=[])
    assert tick(utc(11, 17, 6)) == Tick(rendered=0, superseded=1, sent=[url(5)])

    rows = list(db_session.scalars(select(MicroRenderRow).order_by(MicroRenderRow.id)))
    assert [row.text for row in rows] == ["a", "b", "a", "b", "a", "b"]
    assert [row.status for row in rows] == [
        MicroRenderStatus.SUPERSEDED,
        MicroRenderStatus.SUPERSEDED,
        MicroRenderStatus.READY,
        MicroRenderStatus.READY,
        MicroRenderStatus.READY,
        MicroRenderStatus.READY,
    ]
    assert [row.notified_at for row in rows] == [
        utc(10, 13),
        utc(10, 17, 5),
        utc(10, 22),
        utc(11, 13),
        utc(11, 17, 6),
        None,
    ]
