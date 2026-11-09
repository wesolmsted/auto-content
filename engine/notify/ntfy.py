import httpx

from engine.notify.models import Notification


class NtfyNotifier:
    """`Notifier` that POSTs to `<server>/<topic>` on ntfy, with the `Click` header set from
    `notification.click_url`. Never log the topic or token."""

    def __init__(self, server: str, topic: str, token: str | None, client: httpx.Client) -> None:
        self.server = server
        self.topic = topic
        self.token = token
        self.client = client

    def send(self, notification: Notification) -> None:
        raise NotImplementedError
