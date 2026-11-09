from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import Settings
from engine.db.enums import RenderStatus
from engine.db.models import RenderRow


def supersede_expired(session: Session, settings: Settings, now: datetime) -> int:
    """Move `ready` renders created more than `handoff.expire_hours` ago to `superseded`.

    Returns how many moved. Flushes; the caller commits.
    """
    cutoff = now - timedelta(hours=settings.handoff.expire_hours)
    expired = session.scalars(
        select(RenderRow).where(
            RenderRow.status == RenderStatus.READY, RenderRow.created_at < cutoff
        )
    ).all()
    for render in expired:
        render.status = RenderStatus.SUPERSEDED
    session.flush()
    return len(expired)
