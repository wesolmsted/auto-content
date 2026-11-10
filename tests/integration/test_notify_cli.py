import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from typer.testing import CliRunner

from engine.cli import app
from engine.config import load_settings
from engine.db.enums import RenderStatus
from engine.db.migrate import upgrade_to_head
from engine.db.models import RenderRow
from engine.db.session import create_db_engine, make_session_factory, session_scope
from tests.integration.test_notify_handoff import add_render

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SERVER = "https://ntfy.example.test"
TOPIC = "ace-topic-cli-4b8e1f"
TOPIC_URL = f"{SERVER}/{TOPIC}"

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repo default config, a temp data dir, no quiet hours and no ntfy settings."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(REPO_CONFIG_DIR / "default.toml", config_dir / "default.toml")
    monkeypatch.setenv("ACE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ACE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ACE_NOTIFY__QUIET_HOURS__START", "00:00:00")
    monkeypatch.setenv("ACE_NOTIFY__QUIET_HOURS__END", "00:00:00")
    for name in ("NTFY_SERVER", "NTFY_TOPIC", "NTFY_TOKEN", "HANDOFF_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def ntfy_env(cli_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NTFY_SERVER", SERVER)
    monkeypatch.setenv("NTFY_TOPIC", TOPIC)
    monkeypatch.setenv("HANDOFF_BASE_URL", "https://mac.tailnet.test")


@respx.mock
def test_notify_test_sends_a_test_notification(ntfy_env: None) -> None:
    route = respx.post(TOPIC_URL).mock(return_value=httpx.Response(200))
    result = runner.invoke(app, ["notify", "test"])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert route.calls.last.request.headers["Click"] == "https://mac.tailnet.test"
    assert "sent" in result.output
    assert TOPIC not in result.output


def test_notify_test_without_ntfy_config_exits_1_naming_what_is_missing(cli_env: Path) -> None:
    result = runner.invoke(app, ["notify", "test"])
    assert result.exit_code == 1
    assert "NTFY_SERVER" not in result.output
    assert "NTFY_TOPIC" in result.output


@respx.mock
def test_notify_test_reports_a_delivery_failure(ntfy_env: None) -> None:
    respx.post(TOPIC_URL).mock(return_value=httpx.Response(401))
    result = runner.invoke(app, ["notify", "test"])
    assert result.exit_code == 1
    assert "401" in result.output


@respx.mock
def test_notify_pending_sends_and_records_ready_renders(ntfy_env: None) -> None:
    route = respx.post(TOPIC_URL).mock(return_value=httpx.Response(200))
    settings = load_settings()
    settings.db_path.parent.mkdir(parents=True)
    db_url = settings.db_url
    upgrade_to_head(db_url)
    engine = create_db_engine(db_url)
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        render_id = add_render(session).id

    result = runner.invoke(app, ["notify", "pending"])

    assert result.exit_code == 0, result.output
    assert "1" in result.output
    assert route.call_count == 1
    assert route.calls.last.request.headers["Click"] == (
        f"https://mac.tailnet.test/handoff/{render_id}"
    )
    with factory() as session:
        notified_at = session.execute(
            select(RenderRow.notified_at).where(RenderRow.id == render_id)
        ).scalar_one()
    engine.dispose()
    assert notified_at is not None


@respx.mock
def test_notify_pending_supersedes_expired_renders_before_sending(ntfy_env: None) -> None:
    route = respx.post(TOPIC_URL).mock(return_value=httpx.Response(200))
    settings = load_settings()
    settings.db_path.parent.mkdir(parents=True)
    upgrade_to_head(settings.db_url)
    engine = create_db_engine(settings.db_url)
    factory = make_session_factory(engine)
    notified_long_ago = datetime.now(UTC) - timedelta(hours=settings.handoff.expire_hours + 1)
    notified_recently = datetime.now(UTC) - timedelta(hours=1)
    with session_scope(factory) as session:
        expired_id = add_render(session, "Old Song", notified_at=notified_long_ago).id
        recent_id = add_render(session, "Recent Song", notified_at=notified_recently).id
        pending_id = add_render(session, "New Song").id

    result = runner.invoke(app, ["notify", "pending"])

    assert result.exit_code == 0, result.output
    assert "superseded 1 expired render(s)" in result.output
    assert "sent 1 hand-off notification(s)" in result.output
    assert route.call_count == 1
    with factory() as session:
        rows = session.execute(select(RenderRow.id, RenderRow.status)).all()
    engine.dispose()
    assert dict(rows) == {
        expired_id: RenderStatus.SUPERSEDED,
        recent_id: RenderStatus.READY,
        pending_id: RenderStatus.READY,
    }


def test_notify_pending_without_ntfy_config_exits_1(cli_env: Path) -> None:
    result = runner.invoke(app, ["notify", "pending"])
    assert result.exit_code == 1
    assert "NTFY_TOPIC" in result.output
