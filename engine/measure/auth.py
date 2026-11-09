"""Read-only platform tokens: storage in the secrets dir, expiry checks and the minimal flows
behind `ace auth meta` and `ace auth tiktok`."""

import hashlib
import os
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict

from engine.config import HttpSettings
from engine.db.enums import Platform
from engine.measure.api import request_json

META_SCOPES = (
    "instagram_basic",
    "instagram_manage_insights",
    "pages_show_list",
    "pages_read_engagement",
)
TIKTOK_SCOPES = ("user.info.basic", "video.list")
TIKTOK_AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_FILE_NAMES = {Platform.IG_TRIAL: "meta_token.json", Platform.TIKTOK: "tiktok_token.json"}
AUTH_COMMANDS = {Platform.IG_TRIAL: "ace auth meta", Platform.TIKTOK: "ace auth tiktok"}


class AuthError(Exception):
    """An authorization step failed; the message says what to do."""


class StoredToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    expires_at: datetime | None = None
    refresh_token: str | None = None
    refresh_expires_at: datetime | None = None

    @property
    def reauthorize_by(self) -> datetime | None:
        """When the owner must run the auth flow again: the refresh token's expiry if there is
        one (TikTok access tokens last a day and are refreshed automatically), else the
        access token's."""
        return self.refresh_expires_at if self.refresh_token else self.expires_at


def token_path(secrets_dir: Path, platform: Platform) -> Path:
    return secrets_dir / TOKEN_FILE_NAMES[platform]


def save_token(token: StoredToken, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(token.model_dump_json())
    path.chmod(0o600)


def load_token(path: Path) -> StoredToken | None:
    if not path.exists():
        return None
    return StoredToken.model_validate_json(path.read_text())


def expiry_warning(
    platform: Platform, token: StoredToken, now: datetime, warn_days: int
) -> str | None:
    deadline = token.reauthorize_by
    if deadline is None or deadline - now > timedelta(days=warn_days):
        return None
    command = AUTH_COMMANDS[platform]
    if deadline <= now:
        return f"{platform} token expired on {deadline.date().isoformat()}: run `{command}`"
    return f"{platform} token expires on {deadline.date().isoformat()}: run `{command}` before then"


def exchange_meta_token(
    client: httpx.Client,
    graph_url: str,
    app_id: str,
    app_secret: str,
    short_lived_token: str,
    http: HttpSettings,
    now: datetime,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> StoredToken:
    """Swap a short-lived user token (from the Graph API Explorer) for a ~60-day one."""
    body = request_json(
        client,
        "GET",
        f"{graph_url.rstrip('/')}/oauth/access_token",
        service="Meta",
        http=http,
        sleep=sleep,
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_lived_token,
        },
    )
    return StoredToken(
        access_token=body["access_token"], expires_at=_expiry(now, body.get("expires_in"))
    )


def new_pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def pkce_challenge(verifier: str) -> str:
    """TikTok's desktop PKCE expects the hex SHA-256 digest, not base64url as in RFC 7636."""
    return hashlib.sha256(verifier.encode()).hexdigest()


def tiktok_authorize_url(client_key: str, redirect_uri: str, state: str, challenge: str) -> str:
    query = urlencode(
        {
            "client_key": client_key,
            "scope": ",".join(TIKTOK_SCOPES),
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{TIKTOK_AUTHORIZE_URL}?{query}"


def code_from_redirect(redirected_url: str, expected_state: str) -> str:
    """The authorization code from the URL the browser was sent back to."""
    query = {key: values[0] for key, values in parse_qs(urlsplit(redirected_url).query).items()}
    if "error" in query:
        raise AuthError(
            f"TikTok authorization failed: {query.get('error_description', query['error'])}"
        )
    if query.get("state") != expected_state:
        raise AuthError("the redirect's state doesn't match this login attempt: start again")
    if "code" not in query:
        raise AuthError("the redirect URL has no code: paste the full URL from the address bar")
    return query["code"]


def exchange_tiktok_code(
    client: httpx.Client,
    api_url: str,
    client_key: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    verifier: str,
    http: HttpSettings,
    now: datetime,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> StoredToken:
    form = {
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    return _tiktok_token_request(client, api_url, form, http, now, sleep)


def refresh_tiktok_token(
    client: httpx.Client,
    api_url: str,
    client_key: str,
    client_secret: str,
    token: StoredToken,
    http: HttpSettings,
    now: datetime,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> StoredToken:
    if token.refresh_token is None:
        raise AuthError("the TikTok token has no refresh token: run `ace auth tiktok`")
    form = {
        "client_key": client_key,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": token.refresh_token,
    }
    return _tiktok_token_request(client, api_url, form, http, now, sleep)


def _tiktok_token_request(
    client: httpx.Client,
    api_url: str,
    form: dict[str, str],
    http: HttpSettings,
    now: datetime,
    sleep: Callable[[float], None],
) -> StoredToken:
    body = request_json(
        client,
        "POST",
        f"{api_url.rstrip('/')}/oauth/token/",
        service="TikTok",
        http=http,
        sleep=sleep,
        data=form,
    )
    if "access_token" not in body:
        error = body.get("error", "unknown_error")
        raise AuthError(
            f"TikTok token request failed: {error}: {body.get('error_description', '')}"
        )
    return StoredToken(
        access_token=body["access_token"],
        expires_at=_expiry(now, body.get("expires_in")),
        refresh_token=body.get("refresh_token"),
        refresh_expires_at=_expiry(now, body.get("refresh_expires_in")),
    )


def _expiry(now: datetime, seconds: Any) -> datetime | None:
    return now + timedelta(seconds=int(seconds)) if seconds is not None else None
