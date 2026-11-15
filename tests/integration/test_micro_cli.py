import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from engine.cli import app
from engine.config import load_settings
from engine.db.enums import MicroRenderStatus, MicroSetStatus, MicroSourceStatus
from engine.db.models import MicroRenderRow
from engine.db.session import create_db_engine, make_session_factory
from engine.micro.models import MicroIngestSummary
from tests.fakes.drive import FakeDriveClient
from tests.fakes.micro import (
    FakeMicroRenderer,
    add_micro_set,
    add_micro_video,
    fixed_duration_s,
    fixed_edit,
)
from tests.fakes.notify import FakeNotifier

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
# 06:00 in New York on 2026-06-10: all three of the day's slots are ahead.
MORNING = datetime(2026, 6, 10, 10, 0, tzinfo=UTC)
FIRST_SLOT = datetime(2026, 6, 10, 13, 0, tzinfo=UTC)
INGEST_SUMMARY = MicroIngestSummary(
    sets=2,
    new_sets=1,
    waiting_sets=1,
    removed_sets=0,
    new_videos=3,
    ready_videos=2,
    duplicate_videos=1,
    removed_videos=0,
    failed_videos=0,
)
INGEST_LINE = (
    "sets=2 new_sets=1 waiting_sets=1 removed_sets=0 new_videos=3 ready_videos=2 "
    "duplicate_videos=1 removed_videos=0 failed_videos=0"
)

runner = CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(REPO_CONFIG_DIR / "default.toml", config_dir / "default.toml")
    monkeypatch.setenv("ACE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ACE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ACE_DRIVE__FOOTAGE_INBOX_FOLDER_ID", "inbox")
    monkeypatch.setenv("HANDOFF_BASE_URL", "https://mac.tailnet.test")
    for name in ("NTFY_SERVER", "NTFY_TOPIC", "NTFY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    monkeypatch.setattr("engine.micro.cli.now_utc", lambda: MORNING)
    return tmp_path / "data"


@pytest.fixture
def session(data_dir: Path) -> Iterator[Session]:
    engine = create_db_engine(load_settings().db_url)
    with make_session_factory(engine)() as db_session:
        yield db_session
    engine.dispose()


@pytest.fixture
def fake_renderer(monkeypatch: pytest.MonkeyPatch) -> FakeMicroRenderer:
    renderer = FakeMicroRenderer()
    monkeypatch.setattr("engine.micro.cli.render_micro", renderer)
    monkeypatch.setattr("engine.micro.cli.sample_edit", fixed_edit)
    monkeypatch.setattr("engine.micro.cli.output_duration_s", fixed_duration_s)
    return renderer


@pytest.fixture
def fake_notifier(monkeypatch: pytest.MonkeyPatch) -> FakeNotifier:
    notifier = FakeNotifier()
    monkeypatch.setattr(
        "engine.micro.cli.ntfy_notifier_from_settings", lambda settings, client: notifier
    )
    return notifier


@pytest.fixture
def fake_ingest(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    drive = FakeDriveClient()
    calls: list[dict[str, Any]] = []

    def poll(*args: Any, **kwargs: Any) -> MicroIngestSummary:
        calls.append({"args": args, "kwargs": kwargs})
        return INGEST_SUMMARY

    monkeypatch.setattr("engine.micro.cli.make_drive_client", lambda settings: drive)
    monkeypatch.setattr("engine.micro.cli.poll_micro_sets", poll)
    return calls


def renders(session: Session) -> list[MicroRenderRow]:
    session.expire_all()
    return list(session.scalars(select(MicroRenderRow).order_by(MicroRenderRow.id)))


def add_render(
    session: Session, scheduled_for: datetime, notified_at: datetime | None = None
) -> MicroRenderRow:
    video = add_micro_video(session, add_micro_set(session))
    render = MicroRenderRow(
        source_id=video.id,
        scheduled_for=scheduled_for,
        edit_json={},
        text="wait for it",
        caption="You Dont Dance",
        file_path="/media/renders/micro.mp4",
        duration_s=9.5,
        status=MicroRenderStatus.READY,
        notified_at=notified_at,
    )
    session.add(render)
    session.commit()
    return render


def test_ingest_polls_the_micro_sets_and_prints_the_counts(
    data_dir: Path, fake_ingest: list[dict[str, Any]]
) -> None:
    result = runner.invoke(app, ["micro", "ingest"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == INGEST_LINE
    [call] = fake_ingest
    assert isinstance(call["args"][1], FakeDriveClient)
    assert call["args"][3].footage_inbox_folder_id == "inbox"


def test_ingest_without_an_inbox_folder_explains_and_exits_1(
    data_dir: Path, fake_ingest: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ACE_DRIVE__FOOTAGE_INBOX_FOLDER_ID")

    result = runner.invoke(app, ["micro", "ingest"])

    assert result.exit_code == 1
    assert "drive.footage_inbox_folder_id" in result.output
    assert fake_ingest == []


def test_render_fills_todays_slots_and_prints_the_counts(
    session: Session, fake_renderer: FakeMicroRenderer
) -> None:
    add_micro_video(session, add_micro_set(session, texts=("a", "b")))

    result = runner.invoke(app, ["micro", "render"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "slots=3 ready=3 failed=0 no_source=0"
    rows = renders(session)
    assert [row.text for row in rows] == ["a", "b", "a"]
    assert [row.status for row in rows] == [MicroRenderStatus.READY] * 3
    assert len(fake_renderer.calls) == 3


def test_render_exits_1_when_a_render_failed(
    session: Session, fake_renderer: FakeMicroRenderer
) -> None:
    add_micro_video(session, add_micro_set(session))
    fake_renderer.failures = 3

    result = runner.invoke(app, ["micro", "render"])

    assert result.exit_code == 1
    assert result.stdout.strip() == "slots=3 ready=2 failed=1 no_source=0"


def test_render_before_init_says_to_run_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACE_DATA_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("ACE_CONFIG_DIR", str(REPO_CONFIG_DIR))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["micro", "render"])

    assert result.exit_code == 1
    assert "ace init" in result.output


def test_notify_supersedes_expired_renders_then_sends_due_ones(
    session: Session, fake_notifier: FakeNotifier
) -> None:
    expired = add_render(
        session, MORNING - timedelta(days=2), notified_at=MORNING - timedelta(hours=25)
    )
    due = add_render(session, MORNING - timedelta(minutes=5))
    future = add_render(session, FIRST_SLOT)

    result = runner.invoke(app, ["micro", "notify"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "superseded 1 expired micro render(s)",
        "sent 1 micro notification(s)",
    ]
    assert [n.click_url for n in fake_notifier.sent] == [f"https://mac.tailnet.test/micro/{due.id}"]
    by_id = {row.id: row for row in renders(session)}
    assert by_id[expired.id].status is MicroRenderStatus.SUPERSEDED
    assert by_id[due.id].notified_at == MORNING
    assert by_id[future.id].notified_at is None


def test_notify_without_ntfy_config_explains_and_exits_1(session: Session) -> None:
    add_render(session, MORNING - timedelta(minutes=5))

    result = runner.invoke(app, ["micro", "notify"])

    assert result.exit_code == 1
    assert "NTFY_TOPIC" in result.output
    assert renders(session)[0].notified_at is None


def test_run_ingests_renders_and_notifies(
    session: Session,
    fake_ingest: list[dict[str, Any]],
    fake_renderer: FakeMicroRenderer,
    fake_notifier: FakeNotifier,
) -> None:
    add_micro_video(session, add_micro_set(session))

    result = runner.invoke(app, ["micro", "run"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        INGEST_LINE,
        "slots=3 ready=3 failed=0 no_source=0",
        "superseded 0 expired micro render(s)",
        "sent 0 micro notification(s)",
    ]
    assert len(fake_ingest) == 1


def test_run_keeps_going_after_a_failed_step_and_exits_1(
    session: Session,
    fake_renderer: FakeMicroRenderer,
    fake_notifier: FakeNotifier,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_drive(settings: Any) -> FakeDriveClient:
        raise ValueError("drive is down")

    monkeypatch.setattr("engine.micro.cli.make_drive_client", broken_drive)
    add_micro_video(session, add_micro_set(session))
    add_render(session, MORNING - timedelta(minutes=5))

    result = runner.invoke(app, ["micro", "run"])

    assert result.exit_code == 1
    assert result.stdout.splitlines() == [
        "drive is down",
        "micro ingest failed",
        "slots=3 ready=3 failed=0 no_source=0",
        "superseded 0 expired micro render(s)",
        "sent 1 micro notification(s)",
    ]


def test_run_reports_an_unexpected_error_in_a_step_and_continues(
    session: Session,
    fake_ingest: list[dict[str, Any]],
    fake_notifier: FakeNotifier,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_sampler(*args: Any) -> None:
        raise RuntimeError("sampler exploded")

    monkeypatch.setattr("engine.micro.cli.sample_edit", broken_sampler)
    add_micro_video(session, add_micro_set(session))

    result = runner.invoke(app, ["micro", "run"])

    assert result.exit_code == 1
    assert result.stdout.splitlines() == [
        INGEST_LINE,
        "micro render failed: RuntimeError: sampler exploded",
        "superseded 0 expired micro render(s)",
        "sent 0 micro notification(s)",
    ]


def test_list_with_no_sets(data_dir: Path) -> None:
    result = runner.invoke(app, ["micro", "list"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "no micro sets"


def test_list_shows_each_set_and_its_videos(session: Session) -> None:
    active = add_micro_set(session, song_title="You Dont Dance", texts=("a", "b", "c"))
    active.next_text_index = 4
    one = add_micro_video(session, active, name="one.mp4")
    add_micro_video(session, active, name="two.mp4", status=MicroSourceStatus.NEW)
    waiting = add_micro_set(session, song_title="Later", status=MicroSetStatus.WAITING)
    waiting.song_title = None
    waiting.text_options_json = []
    waiting.error = "no .txt file in the folder"
    session.commit()
    for hour in (13, 17):
        session.add(
            MicroRenderRow(
                source_id=one.id,
                scheduled_for=datetime(2026, 6, 10, hour, tzinfo=UTC),
                edit_json={},
                text="a",
                caption="You Dont Dance",
            )
        )
    session.commit()

    result = runner.invoke(app, ["micro", "list"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f'set {active.id} you dont dance_micro status=active song="You Dont Dance" texts=3 '
        'next_text="b"',
        f"  video {one.id} one.mp4 status=ready renders=2",
        f"  video {one.id + 1} two.mp4 status=new renders=0",
        f"set {waiting.id} later_micro status=waiting song=- texts=0 next_text=- "
        'error="no .txt file in the folder"',
        "  no videos",
    ]
