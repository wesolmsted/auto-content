import hashlib
import stat
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from engine.config import load_settings
from engine.db.enums import Platform
from engine.measure.api import PlatformReadError
from engine.measure.auth import (
    AuthError,
    StoredToken,
    code_from_redirect,
    exchange_meta_token,
    exchange_tiktok_code,
    expiry_warning,
    load_token,
    new_pkce_verifier,
    pkce_challenge,
    refresh_tiktok_token,
    save_token,
    tiktok_authorize_url,
    token_path,
)

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
HTTP = load_settings(REPO_CONFIG_DIR, env_file=None).http
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
GRAPH = "https://graph.example.test/v23.0"
TIKTOK_API = "https://open.example.test/v2"
REDIRECT = "http://localhost:8765/callback/"


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with httpx.Client() as client:
        yield client


def test_tokens_are_saved_owner_only_and_load_back(tmp_path: Path) -> None:
    path = token_path(tmp_path / "secrets", Platform.TIKTOK)
    token = StoredToken(
        access_token="act.1",
        expires_at=NOW + timedelta(hours=24),
        refresh_token="rft.1",
        refresh_expires_at=NOW + timedelta(days=365),
    )

    save_token(token, path)

    assert path.name == "tiktok_token.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_token(path) == token


def test_saving_over_a_loose_file_tightens_it_to_0600(tmp_path: Path) -> None:
    path = token_path(tmp_path, Platform.IG_TRIAL)
    path.write_text("{}")
    path.chmod(0o644)
    save_token(StoredToken(access_token="EAA-new"), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_token(path) == StoredToken(access_token="EAA-new")


def test_loading_a_missing_token_gives_none(tmp_path: Path) -> None:
    assert load_token(tmp_path / "meta_token.json") is None


@pytest.mark.parametrize(
    ("expires_in", "warned"),
    [
        (timedelta(days=30), False),
        (timedelta(days=7, seconds=1), False),
        (timedelta(days=7), True),
        (timedelta(days=1), True),
    ],
)
def test_meta_token_warns_within_the_warning_window(expires_in: timedelta, warned: bool) -> None:
    token = StoredToken(access_token="EAA", expires_at=NOW + expires_in)
    warning = expiry_warning(Platform.IG_TRIAL, token, NOW, warn_days=7)
    assert (warning is not None) is warned
    if warning is not None:
        assert "ace auth meta" in warning
        assert (NOW + expires_in).date().isoformat() in warning


def test_an_expired_token_says_it_expired() -> None:
    token = StoredToken(access_token="EAA", expires_at=NOW - timedelta(minutes=1))
    warning = expiry_warning(Platform.IG_TRIAL, token, NOW, warn_days=7)
    assert warning is not None
    assert "expired" in warning
    assert "ace auth meta" in warning


def test_a_token_without_expiry_never_warns() -> None:
    assert expiry_warning(Platform.IG_TRIAL, StoredToken(access_token="EAA"), NOW, 7) is None


def test_tiktok_warns_on_the_refresh_token_not_the_short_lived_access_token() -> None:
    fresh = StoredToken(
        access_token="act",
        expires_at=NOW - timedelta(hours=1),
        refresh_token="rft",
        refresh_expires_at=NOW + timedelta(days=200),
    )
    ending = fresh.model_copy(update={"refresh_expires_at": NOW + timedelta(days=3)})
    assert expiry_warning(Platform.TIKTOK, fresh, NOW, warn_days=7) is None
    warning = expiry_warning(Platform.TIKTOK, ending, NOW, warn_days=7)
    assert warning is not None
    assert "ace auth tiktok" in warning


@respx.mock
def test_exchange_meta_token_gets_a_long_lived_token(client: httpx.Client) -> None:
    route = respx.get(f"{GRAPH}/oauth/access_token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "EAA-long", "token_type": "bearer", "expires_in": 5183944}
        )
    )

    token = exchange_meta_token(client, GRAPH, "app-1", "app-secret", "EAA-short", HTTP, NOW)

    assert token == StoredToken(
        access_token="EAA-long", expires_at=NOW + timedelta(seconds=5183944)
    )
    params = route.calls.last.request.url.params
    assert dict(params) == {
        "grant_type": "fb_exchange_token",
        "client_id": "app-1",
        "client_secret": "app-secret",
        "fb_exchange_token": "EAA-short",
    }


@respx.mock
def test_exchange_meta_token_without_expires_in_has_no_expiry(client: httpx.Client) -> None:
    respx.get(f"{GRAPH}/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "EAA-long"})
    )
    token = exchange_meta_token(client, GRAPH, "app-1", "app-secret", "EAA-short", HTTP, NOW)
    assert token == StoredToken(access_token="EAA-long")


@respx.mock
def test_exchange_meta_token_failure_hides_the_secret(client: httpx.Client) -> None:
    respx.get(f"{GRAPH}/oauth/access_token").mock(
        return_value=httpx.Response(400, json={"error": {"message": "Invalid client_secret"}})
    )
    with pytest.raises(PlatformReadError, match="Invalid client_secret") as info:
        exchange_meta_token(client, GRAPH, "app-1", "app-secret", "EAA-short", HTTP, NOW)
    assert "app-secret" not in str(info.value)


def test_pkce_verifier_is_long_random_and_url_safe() -> None:
    first, second = new_pkce_verifier(), new_pkce_verifier()
    assert first != second
    assert 43 <= len(first) <= 128
    assert set(first) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def test_pkce_challenge_is_the_hex_sha256_of_the_verifier() -> None:
    assert pkce_challenge("abc") == hashlib.sha256(b"abc").hexdigest()
    assert pkce_challenge("abc").startswith("ba7816bf")


def test_tiktok_authorize_url_asks_for_read_scopes_only() -> None:
    url = tiktok_authorize_url("client-key", REDIRECT, "state-1", "challenge-1")
    parts = urlsplit(url)
    assert (
        f"{parts.scheme}://{parts.netloc}{parts.path}"
        == "https://www.tiktok.com/v2/auth/authorize/"
    )
    assert {key: values[0] for key, values in parse_qs(parts.query).items()} == {
        "client_key": "client-key",
        "scope": "user.info.basic,video.list",
        "response_type": "code",
        "redirect_uri": REDIRECT,
        "state": "state-1",
        "code_challenge": "challenge-1",
        "code_challenge_method": "S256",
    }


def test_code_from_redirect_returns_the_code_when_the_state_matches() -> None:
    redirected = f"{REDIRECT}?code=abc%2A123&scopes=user.info.basic%2Cvideo.list&state=state-1"
    assert code_from_redirect(redirected, "state-1") == "abc*123"


@pytest.mark.parametrize(
    ("redirected", "message"),
    [
        (f"{REDIRECT}?code=abc&state=other", "state"),
        (f"{REDIRECT}?error=access_denied&error_description=User+cancelled&state=s", "cancelled"),
        (f"{REDIRECT}?state=state-1", "no code"),
    ],
)
def test_code_from_redirect_rejects_bad_redirects(redirected: str, message: str) -> None:
    with pytest.raises(AuthError, match=message):
        code_from_redirect(redirected, "state-1")


TIKTOK_TOKEN_BODY = {
    "access_token": "act.new",
    "expires_in": 86400,
    "open_id": "open-1",
    "refresh_expires_in": 31536000,
    "refresh_token": "rft.new",
    "scope": "user.info.basic,video.list",
    "token_type": "Bearer",
}


@respx.mock
def test_exchange_tiktok_code_posts_the_form_and_stores_both_expiries(
    client: httpx.Client,
) -> None:
    route = respx.post(f"{TIKTOK_API}/oauth/token/").mock(
        return_value=httpx.Response(200, json=TIKTOK_TOKEN_BODY)
    )

    token = exchange_tiktok_code(
        client,
        TIKTOK_API,
        "client-key",
        "client-secret",
        "code-1",
        REDIRECT,
        "verifier-1",
        HTTP,
        NOW,
    )

    assert token == StoredToken(
        access_token="act.new",
        expires_at=NOW + timedelta(seconds=86400),
        refresh_token="rft.new",
        refresh_expires_at=NOW + timedelta(seconds=31536000),
    )
    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert {key: values[0] for key, values in parse_qs(request.content.decode()).items()} == {
        "client_key": "client-key",
        "client_secret": "client-secret",
        "code": "code-1",
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT,
        "code_verifier": "verifier-1",
    }


@respx.mock
def test_refresh_tiktok_token_uses_the_refresh_grant(client: httpx.Client) -> None:
    route = respx.post(f"{TIKTOK_API}/oauth/token/").mock(
        return_value=httpx.Response(200, json=TIKTOK_TOKEN_BODY)
    )
    old = StoredToken(access_token="act.old", refresh_token="rft.old")

    token = refresh_tiktok_token(client, TIKTOK_API, "client-key", "secret", old, HTTP, NOW)

    assert token.access_token == "act.new"
    assert token.refresh_token == "rft.new"
    form = {k: v[0] for k, v in parse_qs(route.calls.last.request.content.decode()).items()}
    assert form == {
        "client_key": "client-key",
        "client_secret": "secret",
        "grant_type": "refresh_token",
        "refresh_token": "rft.old",
    }


@respx.mock
def test_a_tiktok_token_error_body_raises_auth_error(client: httpx.Client) -> None:
    respx.post(f"{TIKTOK_API}/oauth/token/").mock(
        return_value=httpx.Response(
            200,
            json={
                "error": "invalid_grant",
                "error_description": "Refresh token is invalid or expired.",
                "log_id": "x",
            },
        )
    )
    old = StoredToken(access_token="act.old", refresh_token="rft.old")
    with pytest.raises(AuthError, match="invalid_grant: Refresh token is invalid"):
        refresh_tiktok_token(client, TIKTOK_API, "client-key", "secret", old, HTTP, NOW)


def test_refreshing_without_a_refresh_token_says_to_reauthorize(client: httpx.Client) -> None:
    with pytest.raises(AuthError, match="ace auth tiktok"):
        refresh_tiktok_token(
            client, TIKTOK_API, "key", "secret", StoredToken(access_token="act"), HTTP, NOW
        )
