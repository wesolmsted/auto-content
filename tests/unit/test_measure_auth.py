import stat
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from engine.config import load_settings
from engine.db.enums import Platform
from engine.measure.api import PlatformReadError
from engine.measure.auth import (
    StoredToken,
    exchange_meta_token,
    expiry_warning,
    load_token,
    save_token,
    token_path,
)

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
HTTP = load_settings(REPO_CONFIG_DIR, env_file=None).http
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
GRAPH = "https://graph.example.test/v23.0"


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with httpx.Client() as client:
        yield client


def test_tokens_are_saved_owner_only_and_load_back(tmp_path: Path) -> None:
    path = token_path(tmp_path / "secrets", Platform.IG_TRIAL)
    token = StoredToken(access_token="EAA-1", expires_at=NOW + timedelta(days=60))

    save_token(token, path)

    assert path.name == "meta_token.json"
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
