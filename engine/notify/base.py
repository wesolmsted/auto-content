from typing import Protocol

from engine.notify.models import Notification


class NotificationError(Exception):
    """A notification could not be delivered. The caller records it and retries on a later run."""


class Notifier(Protocol):
    def send(self, notification: Notification) -> None:
        """Deliver one notification or raise NotificationError."""
        ...
