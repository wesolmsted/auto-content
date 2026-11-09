"""Hand-off notifications for ready renders (DESIGN.md §7.4, §7.5)."""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import Settings
from engine.db.enums import Platform, RenderStatus
from engine.db.models import RecipeRow, RenderRow, SnippetRow, SongRow
from engine.notify.base import NotificationError, Notifier
from engine.notify.models import Notification
from engine.notify.quiet_hours import is_quiet, next_send_time

logger = logging.getLogger(__name__)

JOB = "notify_handoff"
PLATFORM_NAMES = {Platform.IG_TRIAL: "IG Trial", Platform.TIKTOK: "TikTok"}


def send_pending_handoff_notifications(
    session: Session, notifier: Notifier, settings: Settings, now: datetime
) -> int:
    """Send a notification for every `ready` render with `notified_at` NULL and set
    `notified_at = now` on success. Inside quiet hours nothing is sent. A failed send leaves
    the render pending for the next run. Flushes but doesn't commit. Returns the number sent."""
    if is_quiet(now, settings.notify):
        send_after = next_send_time(now, settings.notify)
        logger.info("quiet hours, holding until %s", send_after.isoformat(), extra={"job": JOB})
        return 0
    if not settings.handoff_base_url:
        raise ValueError("HANDOFF_BASE_URL is not set: it is needed for the hand-off link")
    base_url = settings.handoff_base_url.rstrip("/")

    pending = session.execute(
        select(RenderRow, SongRow.title, RecipeRow.params_json)
        .join(SnippetRow, RenderRow.snippet_id == SnippetRow.id)
        .join(SongRow, SnippetRow.song_id == SongRow.id)
        .join(RecipeRow, RenderRow.recipe_id == RecipeRow.id)
        .where(RenderRow.status == RenderStatus.READY, RenderRow.notified_at.is_(None))
        .order_by(RenderRow.id)
    ).all()

    sent = 0
    for render, song_title, recipe_params in pending:
        notification = handoff_notification(render, song_title, recipe_params, base_url)
        try:
            notifier.send(notification)
        except NotificationError as error:
            logger.warning(
                "hand-off notification failed: %s", error, extra={"job": JOB, "row_id": render.id}
            )
            continue
        render.notified_at = now
        session.flush()
        sent += 1
        logger.info("hand-off notification sent", extra={"job": JOB, "row_id": render.id})
    return sent


def handoff_notification(
    render: RenderRow, song_title: str, recipe_params: dict[str, Any], base_url: str
) -> Notification:
    targets = [Platform(target) for target in render.targets_json]
    length_s = render.duration_s or recipe_params["length_s"]
    return Notification(
        title=f"Hand-off ready: {song_title} · {recipe_short_name(recipe_params)}",
        body=f"{round(length_s)}s for {' + '.join(PLATFORM_NAMES[t] for t in targets)}",
        click_url=f"{base_url}/handoff/{render.id}",
        tags=("clapper", *targets),
    )


def recipe_short_name(recipe_params: dict[str, Any]) -> str:
    """E.g. "every beat, high contrast": the cut rate and look, which tell recipes apart."""
    return ", ".join(str(recipe_params[key]).replace("_", " ") for key in ("cut_rate", "look"))
