import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from typer.testing import CliRunner

from engine.cli import app
from engine.config import load_settings
from engine.db.enums import MatchMethod, Platform, PostStatus
from engine.db.migrate import upgrade_to_head
from engine.db.models import PostRow
from engine.db.session import create_db_engine, make_session_factory, session_scope
from engine.measure.auth import StoredToken, load_token, save_token, token_path
from tests.integration.test_measure_job import add_post

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
GRAPH = "https://graph.example.test/v23.0"
IG_USER = "17841400000000000"
SECRET_ENV = ("META_APP_ID", "META_APP_SECRET", "META_IG_USER_ID")

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repo default config with fake API hosts, a temp data dir and no platform credentials."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(REPO_CONFIG_DIR / "default.toml", config_dir / "default.toml")
    monkeypatch.setenv("ACE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ACE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ACE_MEASURE__META_GRAPH_URL", GRAPH)
    monkeypatch.setenv("ACE_HTTP__MAX_RETRIES", "0")
    for name in SECRET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    settings.db_path.parent.mkdir(parents=True)
    upgrade_to_head(settings.db_url)
    return tmp_path


@pytest.fixture
def secrets_dir(cli_env: Path) -> Path:
    return load_settings().secrets_dir


def add_awaiting_post(
    platform: Platform = Platform.IG_TRIAL, downloaded_hours_ago: float = 2
) -> int:
    engine = create_db_engine(load_settings().db_url)
    with session_scope(make_session_factory(engine)) as session:
        downloaded = datetime.now(UTC) - timedelta(hours=downloaded_hours_ago)
        post_id = add_post(session, platform=platform, downloaded_at=downloaded).id
    engine.dispose()
    return post_id


def read_post(post_id: int) -> PostRow:
    engine = create_db_engine(load_settings().db_url)
    with make_session_factory(engine)() as session:
        post = session.scalars(select(PostRow).where(PostRow.id == post_id)).one()
        session.expunge(post)
    engine.dispose()
    return post


def test_measure_due_without_a_token_skips_instagram(cli_env: Path) -> None:
    result = runner.invoke(app, ["measure", "due"])
    assert result.exit_code == 0, result.output
    assert "skipping ig_trial: no Meta token, run `ace auth meta`" in result.output
    assert "matched 0" in result.output


@respx.mock
def test_measure_due_matches_with_the_meta_reader_and_warns_of_expiry(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("META_IG_USER_ID", IG_USER)
    expires = datetime.now(UTC) + timedelta(days=3)
    save_token(
        StoredToken(access_token="EAA-token", expires_at=expires),
        token_path(secrets_dir, Platform.IG_TRIAL),
    )
    post_id = add_awaiting_post()
    posted = (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0)
    reel = {
        "id": "1790",
        "permalink": "https://www.instagram.com/reel/abc/",
        "timestamp": posted.strftime("%Y-%m-%dT%H:%M:%S+0000"),
        "caption": "Night Drive",
        "media_type": "VIDEO",
        "media_product_type": "REELS",
    }
    route = respx.get(f"{GRAPH}/{IG_USER}/media").mock(
        return_value=httpx.Response(200, json={"data": [reel]})
    )

    result = runner.invoke(app, ["measure", "due"])

    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert "matched 1" in result.output
    assert f"warning: ig_trial token expires on {expires.date().isoformat()}" in result.output
    assert "EAA-token" not in result.output
    post = read_post(post_id)
    assert (post.status, post.external_id, post.posted_at) == (PostStatus.MATCHED, "1790", posted)


def test_posts_link_matches_the_post_manually(cli_env: Path) -> None:
    post_id = add_awaiting_post(Platform.IG_TRIAL)
    render_id = read_post(post_id).render_id

    result = runner.invoke(
        app,
        ["posts", "link", str(render_id), "ig_trial", "https://instagram.com/reel/C9zz?igsh=x"],
    )

    assert result.exit_code == 0, result.output
    assert "https://www.instagram.com/reel/C9zz/" in result.output
    post = read_post(post_id)
    assert (post.status, post.match_method, post.external_id) == (
        PostStatus.MATCHED,
        MatchMethod.MANUAL,
        "C9zz",
    )


def test_posts_link_with_a_bad_link_exits_1(cli_env: Path) -> None:
    post_id = add_awaiting_post(Platform.IG_TRIAL)
    render_id = read_post(post_id).render_id

    result = runner.invoke(
        app, ["posts", "link", str(render_id), "ig_trial", "https://www.instagram.com/someone/"]
    )

    assert result.exit_code == 1
    assert "error: not a reel link" in result.output
    assert read_post(post_id).status == PostStatus.AWAITING_POST


def test_auth_meta_without_app_credentials_exits_1(cli_env: Path) -> None:
    result = runner.invoke(app, ["auth", "meta"])
    assert result.exit_code == 1
    assert "META_APP_ID" in result.output


@respx.mock
def test_auth_meta_exchanges_the_pasted_token_and_saves_it_0600(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("META_APP_ID", "app-1")
    monkeypatch.setenv("META_APP_SECRET", "app-secret")
    route = respx.get(f"{GRAPH}/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "EAA-long", "expires_in": 5184000})
    )

    result = runner.invoke(app, ["auth", "meta"], input="EAA-short\n")

    assert result.exit_code == 0, result.output
    assert "instagram_manage_insights" in result.output
    assert "EAA-short" not in result.output
    assert "EAA-long" not in result.output
    assert route.calls.last.request.url.params["fb_exchange_token"] == "EAA-short"
    path = token_path(secrets_dir, Platform.IG_TRIAL)
    saved = load_token(path)
    assert saved is not None
    assert saved.access_token == "EAA-long"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "META_IG_USER_ID" in result.output
