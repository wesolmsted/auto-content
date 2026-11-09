import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

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
TIKTOK_API = "https://open.example.test/v2"
IG_USER = "17841400000000000"
SECRET_ENV = (
    "META_APP_ID",
    "META_APP_SECRET",
    "META_IG_USER_ID",
    "TIKTOK_CLIENT_KEY",
    "TIKTOK_CLIENT_SECRET",
)

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
    monkeypatch.setenv("ACE_MEASURE__TIKTOK_API_URL", TIKTOK_API)
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


def test_measure_due_without_tokens_skips_both_platforms(cli_env: Path) -> None:
    result = runner.invoke(app, ["measure", "due"])
    assert result.exit_code == 0, result.output
    assert "skipping ig_trial: no Meta token, run `ace auth meta`" in result.output
    assert "skipping tiktok: no TikTok token, run `ace auth tiktok`" in result.output
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


@respx.mock
def test_measure_due_refreshes_an_expired_tiktok_access_token(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "client-key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "client-secret")
    path = token_path(secrets_dir, Platform.TIKTOK)
    save_token(
        StoredToken(
            access_token="act.old",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
            refresh_token="rft.old",
            refresh_expires_at=datetime.now(UTC) + timedelta(days=300),
        ),
        path,
    )
    add_awaiting_post(Platform.TIKTOK)
    refresh = respx.post(f"{TIKTOK_API}/oauth/token/").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "act.new",
                "expires_in": 86400,
                "refresh_token": "rft.new",
                "refresh_expires_in": 31536000,
            },
        )
    )
    listing = respx.post(f"{TIKTOK_API}/video/list/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"videos": [], "has_more": False}, "error": {"code": "ok"}},
        )
    )

    result = runner.invoke(app, ["measure", "due"])

    assert result.exit_code == 0, result.output
    assert refresh.call_count == 1
    assert listing.calls.last.request.headers["Authorization"] == "Bearer act.new"
    saved = load_token(path)
    assert saved is not None
    assert (saved.access_token, saved.refresh_token) == ("act.new", "rft.new")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@respx.mock
def test_measure_due_skips_tiktok_when_the_refresh_fails(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "client-key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "client-secret")
    save_token(
        StoredToken(
            access_token="act.old",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
            refresh_token="rft.old",
        ),
        token_path(secrets_dir, Platform.TIKTOK),
    )
    respx.post(f"{TIKTOK_API}/oauth/token/").mock(
        return_value=httpx.Response(
            200, json={"error": "invalid_grant", "error_description": "expired"}
        )
    )

    result = runner.invoke(app, ["measure", "due"])

    assert result.exit_code == 0, result.output
    assert "skipping tiktok: token refresh failed" in result.output
    assert "invalid_grant" in result.output


def test_posts_link_matches_the_post_manually(cli_env: Path) -> None:
    post_id = add_awaiting_post(Platform.TIKTOK)
    render_id = read_post(post_id).render_id

    result = runner.invoke(
        app,
        ["posts", "link", str(render_id), "tiktok", "https://www.tiktok.com/@me/video/7301?x=1"],
    )

    assert result.exit_code == 0, result.output
    assert "https://www.tiktok.com/@me/video/7301" in result.output
    post = read_post(post_id)
    assert (post.status, post.match_method, post.external_id) == (
        PostStatus.MATCHED,
        MatchMethod.MANUAL,
        "7301",
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


def test_auth_tiktok_without_client_credentials_exits_1(cli_env: Path) -> None:
    result = runner.invoke(app, ["auth", "tiktok"])
    assert result.exit_code == 1
    assert "TIKTOK_CLIENT_KEY" in result.output


@respx.mock
def test_auth_tiktok_exchanges_the_code_from_the_pasted_redirect(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "client-key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr("secrets.token_urlsafe", lambda nbytes=None: "fixed-random")
    route = respx.post(f"{TIKTOK_API}/oauth/token/").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "act.1",
                "expires_in": 86400,
                "refresh_token": "rft.1",
                "refresh_expires_in": 31536000,
            },
        )
    )
    redirect = "http://localhost:8765/callback/?code=the-code&state=fixed-random"

    result = runner.invoke(app, ["auth", "tiktok"], input=redirect + "\n")

    assert result.exit_code == 0, result.output
    assert "https://www.tiktok.com/v2/auth/authorize/?" in result.output
    assert "scope=user.info.basic%2Cvideo.list" in result.output
    form = {k: v[0] for k, v in parse_qs(route.calls.last.request.content.decode()).items()}
    assert form["code"] == "the-code"
    assert form["code_verifier"] == "fixed-random"
    assert form["redirect_uri"] == "http://localhost:8765/callback/"
    saved = load_token(token_path(secrets_dir, Platform.TIKTOK))
    assert saved is not None
    assert saved.refresh_token == "rft.1"
    assert "act.1" not in result.output


def test_auth_tiktok_rejects_a_redirect_from_another_attempt(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "client-key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "client-secret")
    redirect = "http://localhost:8765/callback/?code=the-code&state=someone-else"

    result = runner.invoke(app, ["auth", "tiktok"], input=redirect + "\n")

    assert result.exit_code == 1
    assert "state" in result.output
    assert load_token(token_path(secrets_dir, Platform.TIKTOK)) is None
