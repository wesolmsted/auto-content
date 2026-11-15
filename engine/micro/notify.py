"""Micro render expiry and notifications (DESIGN.md §7.10, Notify).

Quiet hours don't apply: the slots are the timing.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import Settings
from engine.db.enums import MicroRenderStatus
from engine.db.models import MicroRenderRow
from engine.notify.base import NotificationError, Notifier
from engine.notify.models import Notification

logger = logging.getLogger(__name__)

JOB = "micro_notify"


def supersede_expired_micro(session: Session, settings: Settings, now: datetime) -> int:
    """Move `ready` micro renders notified more than `micro.expire_hours` ago to `superseded`.
    One not notified yet never expires. Returns how many moved. Flushes; the caller commits."""
    cutoff = now - timedelta(hours=settings.micro.expire_hours)
    expired = session.scalars(
        select(MicroRenderRow).where(
            MicroRenderRow.status == MicroRenderStatus.READY, MicroRenderRow.notified_at < cutoff
        )
    ).all()
    for render in expired:
        render.status = MicroRenderStatus.SUPERSEDED
    session.flush()
    return len(expired)


def send_due_micro_notifications(
    session: Session, notifier: Notifier, settings: Settings, now: datetime
) -> int:
    """Notify every `ready` micro render whose slot has come and that isn't notified yet, and
    set `notified_at = now` on success. A failed send stays pending for the next run. Flushes
    but doesn't commit. Returns the number sent."""
    if not settings.handoff_base_url:
        raise ValueError("HANDOFF_BASE_URL is not set: it is needed for the micro link")
    base_url = settings.handoff_base_url.rstrip("/")

    due = session.scalars(
        select(MicroRenderRow)
        .where(
            MicroRenderRow.status == MicroRenderStatus.READY,
            MicroRenderRow.notified_at.is_(None),
            MicroRenderRow.scheduled_for <= now,
        )
        .order_by(MicroRenderRow.scheduled_for, MicroRenderRow.id)
    ).all()

    sent = 0
    for render in due:
        try:
            notifier.send(micro_notification(render, base_url))
        except NotificationError as error:
            logger.warning(
                "micro notification failed: %s", error, extra={"job": JOB, "row_id": render.id}
            )
            continue
        render.notified_at = now
        session.flush()
        sent += 1
        logger.info("micro notification sent", extra={"job": JOB, "row_id": render.id})
    return sent


def micro_notification(render: MicroRenderRow, base_url: str) -> Notification:
    return Notification(
        title=f"Micro ready: {render.caption}",
        body=f"{round(render.duration_s or 0)}s · {render.text}",
        click_url=f"{base_url}/micro/{render.id}",
        tags=("scissors",),
    )
