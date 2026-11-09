"""Shared retry policy for external HTTP clients (DESIGN.md §9: External HTTP)."""

import time
from collections.abc import Callable
from typing import Any

import httpx

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int,
    backoff_base_s: float,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request, retrying 429/5xx responses and transport errors with exponential backoff
    (`backoff_base_s * 2**attempt`). 4xx errors are not retried. Raises httpx.HTTPStatusError
    for a final error response, or the last transport error."""
    for attempt in range(max_retries + 1):
        is_last_attempt = attempt == max_retries
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError:
            if is_last_attempt:
                raise
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES or is_last_attempt:
                return response.raise_for_status()
        sleep(backoff_base_s * 2**attempt)
    raise AssertionError("unreachable")
