from collections.abc import Callable

from engine.notify.base import NotificationError
from engine.notify.models import Notification


class FakeNotifier:
    """In-memory `Notifier`. Every call is recorded in `attempted`; successful ones also in
    `sent`. Sends for which `fail_if(notification)` is true raise NotificationError."""

    def __init__(self, fail_if: Callable[[Notification], bool] | None = None) -> None:
        self.fail_if = fail_if
        self.attempted: list[Notification] = []
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> None:
        self.attempted.append(notification)
        if self.fail_if is not None and self.fail_if(notification):
            raise NotificationError("fake notifier told to fail")
        self.sent.append(notification)
