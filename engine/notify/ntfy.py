import base64
import logging
import time
from collections.abc import Callable

import httpx

from engine.config import Settings
from engine.http import request_with_retries
from engine.notify.base import NotificationError
from engine.notify.models import Notification

logger = logging.getLogger(__name__)

# httpx logs every request URL at INFO, and the ntfy URL contains the secret topic.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class NtfyNotifier:
    """`Notifier` that POSTs to `<server>/<topic>` on ntfy, with the `Click` header set from
    `notification.click_url`. Never log the topic or token."""

    def __init__(
        self,
        server: str,
        topic: str,
        token: str | None,
        client: httpx.Client,
        *,
        max_retries: int,
        backoff_base_s: float,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.server = server.rstrip("/")
        self.topic = topic
        self.token = token
        self.client = client
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.sleep = sleep

    def send(self, notification: Notification) -> None:
        # The httpx errors carry the URL, which contains the topic, so they are not chained.
        failure: str | None = None
        try:
            request_with_retries(
                self.client,
                "POST",
                f"{self.server}/{self.topic}",
                max_retries=self.max_retries,
                backoff_base_s=self.backoff_base_s,
                sleep=self.sleep,
                content=notification.body.encode(),
                headers=self._headers(notification),
            )
        except httpx.HTTPStatusError as error:
            failure = f"ntfy returned HTTP {error.response.status_code}"
        except httpx.TransportError as error:
            failure = f"ntfy unreachable ({type(error).__name__})"
        if failure:
            raise NotificationError(failure)
        logger.info("notification sent", extra={"title": notification.title})

    def _headers(self, notification: Notification) -> dict[str, str]:
        headers = {"Title": _header_value(notification.title)}
        if notification.click_url:
            headers["Click"] = notification.click_url
        if notification.tags:
            headers["Tags"] = ",".join(notification.tags)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def _header_value(text: str) -> str:
    """HTTP headers are ASCII; ntfy decodes RFC 2047 encoded words for anything else."""
    if text.isascii():
        return text
    return f"=?UTF-8?B?{base64.b64encode(text.encode()).decode()}?="


def ntfy_notifier_from_settings(settings: Settings, client: httpx.Client) -> NtfyNotifier:
    """Build the notifier from `NTFY_SERVER`, `NTFY_TOPIC`, `NTFY_TOKEN` and retry settings.
    Raises ValueError naming any required variable that isn't set."""
    missing = [
        name
        for name, value in (
            ("NTFY_SERVER", settings.ntfy_server),
            ("NTFY_TOPIC", settings.ntfy_topic),
        )
        if not value
    ]
    if missing or settings.ntfy_server is None or settings.ntfy_topic is None:
        raise ValueError(f"ntfy is not configured: set {', '.join(missing)}")
    return NtfyNotifier(
        settings.ntfy_server,
        settings.ntfy_topic.get_secret_value(),
        settings.ntfy_token.get_secret_value() if settings.ntfy_token else None,
        client,
        max_retries=settings.notify.max_retries,
        backoff_base_s=settings.http.backoff_base_s,
    )
