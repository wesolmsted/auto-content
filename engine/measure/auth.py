"""Read-only platform tokens: storage in the secrets dir, expiry checks and the minimal flow
behind `ace auth meta`."""

import os
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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
TOKEN_FILE_NAMES = {Platform.IG_TRIAL: "meta_token.json"}
AUTH_COMMANDS = {Platform.IG_TRIAL: "ace auth meta"}


class StoredToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    expires_at: datetime | None = None


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
    deadline = token.expires_at
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


def _expiry(now: datetime, seconds: Any) -> datetime | None:
    return now + timedelta(seconds=int(seconds)) if seconds is not None else None
