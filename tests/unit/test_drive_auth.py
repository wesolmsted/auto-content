import json
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from google.oauth2.credentials import Credentials

from engine.drive.google import (
    DRIVE_SCOPES,
    GoogleAuthError,
    load_credentials,
    save_credentials,
    token_path,
)


def make_credentials(token: str = "access-1", expiry: datetime | None = None) -> Credentials:
    credentials = Credentials(
        token=token,
        refresh_token="refresh-1",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=DRIVE_SCOPES,
    )
    # google-auth compares expiry against a naive UTC now.
    credentials.expiry = expiry.replace(tzinfo=None) if expiry else None
    return credentials


@dataclass
class FakeTokenResponse:
    status: int
    data: bytes
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeTokenEndpoint:
    """Stands in for `google.auth.transport.Request`: answers token refresh requests."""

    access_token: str
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, url: str, method: str = "GET", **kwargs: Any) -> FakeTokenResponse:
        self.calls.append({"url": url, "method": method, **kwargs})
        body = {"access_token": self.access_token, "expires_in": 3600}
        return FakeTokenResponse(200, json.dumps(body).encode())


@pytest.fixture(autouse=True)
def no_network_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("tests must not refresh tokens over the network")

    monkeypatch.setattr("engine.drive.google.Request", refuse)


def in_one_hour() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_token_path_is_under_the_secrets_dir(tmp_path: Path) -> None:
    assert token_path(tmp_path / "secrets") == tmp_path / "secrets" / "google_token.json"


def test_saved_token_is_private_and_loads_back(tmp_path: Path) -> None:
    path = tmp_path / "secrets" / "google_token.json"

    save_credentials(make_credentials(expiry=in_one_hour()), path)

    assert mode_of(path) == 0o600
    assert json.loads(path.read_text())["refresh_token"] == "refresh-1"
    loaded = load_credentials(path)
    assert loaded.token == "access-1"
    assert loaded.refresh_token == "refresh-1"
    assert loaded.scopes == DRIVE_SCOPES


def test_saving_over_a_world_readable_file_makes_it_private(tmp_path: Path) -> None:
    path = tmp_path / "google_token.json"
    path.write_text("{}")
    path.chmod(0o644)

    save_credentials(make_credentials(), path)

    assert mode_of(path) == 0o600
    assert json.loads(path.read_text())["refresh_token"] == "refresh-1"


def test_expired_credentials_are_refreshed_and_saved(tmp_path: Path) -> None:
    path = tmp_path / "google_token.json"
    save_credentials(make_credentials(expiry=datetime.now(UTC) - timedelta(hours=1)), path)
    endpoint = FakeTokenEndpoint(access_token="access-2")

    loaded = load_credentials(path, request=endpoint)

    assert loaded.token == "access-2"
    assert loaded.valid
    assert [call["url"] for call in endpoint.calls] == ["https://oauth2.googleapis.com/token"]
    assert json.loads(path.read_text())["token"] == "access-2"
    assert mode_of(path) == 0o600


def test_valid_credentials_are_not_refreshed(tmp_path: Path) -> None:
    path = tmp_path / "google_token.json"
    save_credentials(make_credentials(expiry=in_one_hour()), path)
    endpoint = FakeTokenEndpoint(access_token="access-2")

    assert load_credentials(path, request=endpoint).token == "access-1"
    assert endpoint.calls == []


def test_missing_token_says_to_run_auth_google(tmp_path: Path) -> None:
    with pytest.raises(GoogleAuthError, match="ace auth google"):
        load_credentials(tmp_path / "google_token.json")
