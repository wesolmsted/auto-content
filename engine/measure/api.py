"""Shared request handling for the read-only platform clients."""

import time
from collections.abc import Callable
from typing import Any

import httpx

from engine.config import HttpSettings
from engine.http import request_with_retries


class PlatformReadError(Exception):
    """A platform API call failed. The message never contains the request URL or token."""


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    service: str,
    http: HttpSettings,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> dict[str, Any]:
    """Send a request with the shared retry policy and return the JSON body.

    httpx errors carry the URL, which holds the access token for Meta, so they are replaced by a
    PlatformReadError with the platform's own error message instead of being chained."""
    try:
        response = request_with_retries(
            client,
            method,
            url,
            max_retries=http.max_retries,
            backoff_base_s=http.backoff_base_s,
            sleep=sleep,
            **kwargs,
        )
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        detail = api_error_message(error.response)
        message = f"{service} returned HTTP {status}" + (f": {detail}" if detail else "")
    except httpx.TransportError as error:
        message = f"{service} unreachable ({type(error).__name__})"
    else:
        body: dict[str, Any] = response.json()
        return body
    raise PlatformReadError(message)


def api_error_message(response: httpx.Response) -> str | None:
    """`error.message` from a Meta or TikTok error body, if there is one."""
    try:
        error = response.json().get("error")
    except ValueError:
        return None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return None
