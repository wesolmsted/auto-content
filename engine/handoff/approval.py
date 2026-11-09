"""Render and post state changes made from the hand-off page (DESIGN.md §5, §7.5).

Each function flushes; the caller commits, so a download's approval and its posts land together.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.db.enums import MatchMethod, Platform, PostStatus, RejectReason, RenderStatus
from engine.db.models import PostRow, RenderRow
from engine.handoff.links import parse_post_url

DOWNLOADABLE = {RenderStatus.READY, RenderStatus.APPROVED}
LINKABLE = {PostStatus.AWAITING_POST, PostStatus.NOT_POSTED}


class HandoffNotFoundError(LookupError):
    pass


class HandoffConflictError(Exception):
    pass


def get_render(session: Session, render_id: int) -> RenderRow:
    render = session.get(RenderRow, render_id)
    if render is None:
        raise HandoffNotFoundError(f"no render {render_id}")
    return render


def check_downloadable(render: RenderRow) -> None:
    if render.status not in DOWNLOADABLE:
        raise HandoffConflictError(f"render {render.id} is {render.status}; it can't be saved")


def record_download(session: Session, render: RenderRow, now: datetime) -> None:
    """Count a download. The first one approves the render and creates a post per target."""
    check_downloadable(render)
    render.download_count += 1
    if render.status == RenderStatus.READY:
        render.status = RenderStatus.APPROVED
        render.downloaded_at = now
        session.add_all(
            PostRow(render_id=render.id, platform=Platform(target), status=PostStatus.AWAITING_POST)
            for target in render.targets_json
        )
    session.flush()


def reject_render(
    session: Session, render: RenderRow, reason: RejectReason, note: str, now: datetime
) -> None:
    if render.status != RenderStatus.READY:
        raise HandoffConflictError(f"render {render.id} is {render.status}; it can't be rejected")
    render.status = RenderStatus.REJECTED
    render.reject_reason = reason
    render.reject_note = note.strip() or None
    render.reviewed_at = now
    session.flush()


def link_post(session: Session, render: RenderRow, platform: Platform, url: str) -> None:
    """Mark the render's post on `platform` matched by hand. Raises `PostUrlError` for a URL
    that can't be parsed."""
    post = session.scalars(
        select(PostRow).where(PostRow.render_id == render.id, PostRow.platform == platform)
    ).one_or_none()
    if post is None:
        raise HandoffNotFoundError(f"render {render.id} has no {platform} post")
    if post.status not in LINKABLE:
        raise HandoffConflictError(f"the {platform} post is already {post.status}")
    link = parse_post_url(platform, url)
    post.status = PostStatus.MATCHED
    post.match_method = MatchMethod.MANUAL
    post.external_id = link.external_id
    post.permalink = link.permalink
    session.flush()
