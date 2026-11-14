"""Micro render state changes made from the hand-off page (DESIGN.md §7.10).

Each function flushes; the caller commits. Micro renders have no posts, so a download only
moves `ready → downloaded` and counts.
"""

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from engine.db.enums import MicroRenderStatus
from engine.db.models import MicroRenderRow
from engine.handoff.approval import HandoffConflictError, HandoffNotFoundError

MICRO_DOWNLOADABLE = {MicroRenderStatus.READY, MicroRenderStatus.DOWNLOADED}


def get_micro_render(session: Session, render_id: int) -> MicroRenderRow:
    render = session.get(MicroRenderRow, render_id)
    if render is None:
        raise HandoffNotFoundError(f"no micro render {render_id}")
    return render


def check_micro_downloadable(render: MicroRenderRow) -> None:
    if render.status not in MICRO_DOWNLOADABLE:
        raise HandoffConflictError(
            f"micro render {render.id} is {render.status}; it can't be saved"
        )


def record_micro_download(session: Session, render: MicroRenderRow, now: datetime) -> None:
    check_micro_downloadable(render)
    render.download_count += 1
    if render.status == MicroRenderStatus.READY:
        render.status = MicroRenderStatus.DOWNLOADED
        render.downloaded_at = now
    session.flush()


def due_micro_renders(session: Session, now: datetime) -> list[MicroRenderRow]:
    """`ready` micro renders whose slot has come (or that were already notified), newest first."""
    query = (
        select(MicroRenderRow)
        .where(
            MicroRenderRow.status == MicroRenderStatus.READY,
            or_(MicroRenderRow.scheduled_for <= now, MicroRenderRow.notified_at.is_not(None)),
        )
        .order_by(MicroRenderRow.scheduled_for.desc(), MicroRenderRow.id.desc())
    )
    return list(session.scalars(query).all())
