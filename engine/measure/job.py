"""`ace measure due` (DESIGN.md §7.6): match hand-posted videos to posts, expire unmatched posts,
then pull due 24 h / 72 h metric checkpoints. Also the manual link behind `ace posts link`."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from engine.config import Settings
from engine.db.enums import MatchMethod, MetricCheckpoint, MetricSource, Platform, PostStatus
from engine.db.models import MetricRow, PostRow, RenderRow
from engine.handoff.links import parse_post_url
from engine.measure.matching import match_posts
from engine.measure.models import AwaitingPost, MetricSnapshot, PlatformVideo
from engine.measure.readers import PlatformReader

JOB = "measure"
logger = logging.getLogger(__name__)


class LinkError(ValueError):
    """A manual link can't be made; the message says why."""


@dataclass
class MeasureSummary:
    matched: int = 0
    resolved: int = 0
    not_posted: int = 0
    metrics_inserted: int = 0
    errors: list[str] = field(default_factory=list)

    def record_error(self, message: str, row_id: int | None = None) -> None:
        logger.warning(message, extra={"job": JOB, "row_id": row_id})
        self.errors.append(message)


def run_measure(
    session: Session, readers: Sequence[PlatformReader], settings: Settings, now: datetime
) -> MeasureSummary:
    """One pass per platform reader. Flushes but doesn't commit; the caller commits."""
    summary = MeasureSummary()
    for reader in readers:
        _match_platform(session, reader, settings, now, summary)
        _pull_due_metrics(session, reader, settings, now, summary)
    session.flush()
    return summary


def _match_platform(
    session: Session,
    reader: PlatformReader,
    settings: Settings,
    now: datetime,
    summary: MeasureSummary,
) -> None:
    awaiting = _posts_with_renders(
        session, reader.platform, PostRow.status == PostStatus.AWAITING_POST
    )
    unresolved = _posts_with_renders(
        session,
        reader.platform,
        PostRow.status == PostStatus.MATCHED,
        PostRow.posted_at.is_(None),
    )
    if not awaiting and not unresolved:
        return

    oldest_download = min(_downloaded_at(post, render) for post, render in awaiting + unresolved)
    since = oldest_download - timedelta(hours=settings.match.fetch_slack_hours)
    try:
        candidates = reader.recent_videos(since)
    except Exception as error:
        summary.record_error(f"{reader.platform}: listing videos failed: {error}")
        return

    for post, _ in unresolved:
        _resolve_manual_link(post, candidates, summary)
    session.flush()

    linked_ids = set(
        session.scalars(
            select(PostRow.external_id).where(
                PostRow.platform == reader.platform, PostRow.external_id.is_not(None)
            )
        )
    )
    unlinked = [video for video in candidates if video.external_id not in linked_ids]
    posts_by_id = {post.id: post for post, _ in awaiting}
    matchable = [_awaiting_post(post, render) for post, render in awaiting if render.duration_s]
    for match in match_posts(matchable, unlinked, settings.match):
        post = posts_by_id[match.post_id]
        post.status = PostStatus.MATCHED
        post.match_method = MatchMethod.AUTO
        post.match_score = match.score
        post.external_id = match.external_id
        post.permalink = match.permalink
        post.posted_at = match.posted_at
        summary.matched += 1

    window = timedelta(hours=settings.match.window_hours)
    for post, render in awaiting:
        if post.status == PostStatus.AWAITING_POST and now - _downloaded_at(post, render) >= window:
            post.status = PostStatus.NOT_POSTED
            summary.not_posted += 1


def _resolve_manual_link(
    post: PostRow, candidates: Sequence[PlatformVideo], summary: MeasureSummary
) -> None:
    """A manual link stores the IG reel shortcode (or TikTok id) and no posting time; swap in the
    platform's own id and the posting time, which metric checkpoints need."""
    for video in candidates:
        if _is_linked_video(post, video):
            post.external_id = video.external_id
            post.permalink = video.permalink or post.permalink
            post.posted_at = video.posted_at
            summary.resolved += 1
            return
    summary.record_error(
        f"post {post.id}: linked video {post.external_id} not found on {post.platform} yet",
        row_id=post.id,
    )


def _is_linked_video(post: PostRow, video: PlatformVideo) -> bool:
    if video.external_id == post.external_id:
        return True
    if post.platform != Platform.IG_TRIAL or video.permalink is None:
        return False
    try:
        return parse_post_url(Platform.IG_TRIAL, video.permalink).external_id == post.external_id
    except ValueError:
        return False


def _pull_due_metrics(
    session: Session,
    reader: PlatformReader,
    settings: Settings,
    now: datetime,
    summary: MeasureSummary,
) -> None:
    checkpoints = {
        MetricCheckpoint(f"{hours}h"): timedelta(hours=hours)
        for hours in settings.measure.checkpoint_hours
    }
    matched = session.scalars(
        select(PostRow)
        .where(
            PostRow.platform == reader.platform,
            PostRow.status == PostStatus.MATCHED,
            PostRow.posted_at.is_not(None),
            PostRow.external_id.is_not(None),
        )
        .order_by(PostRow.id)
    ).all()
    for post in matched:
        assert post.posted_at is not None and post.external_id is not None
        posted_at = post.posted_at
        existing = set(
            session.scalars(select(MetricRow.checkpoint).where(MetricRow.post_id == post.id))
        )
        due = [
            checkpoint
            for checkpoint, age in checkpoints.items()
            if checkpoint not in existing and posted_at + age <= now
        ]
        if not due:
            continue
        try:
            snapshot = reader.fetch_metrics(post.external_id)
        except Exception as error:
            summary.record_error(f"post {post.id}: fetching metrics failed: {error}", post.id)
            continue
        session.add_all(_metric_row(post.id, checkpoint, snapshot) for checkpoint in due)
        summary.metrics_inserted += len(due)


def _metric_row(post_id: int, checkpoint: MetricCheckpoint, snapshot: MetricSnapshot) -> MetricRow:
    return MetricRow(
        post_id=post_id,
        checkpoint=checkpoint,
        source=MetricSource.API,
        fetched_at=snapshot.fetched_at,
        views=snapshot.views,
        reach=snapshot.reach,
        likes=snapshot.likes,
        comments=snapshot.comments,
        shares=snapshot.shares,
        saves=snapshot.saves,
        avg_watch_s=snapshot.avg_watch_s,
        full_watch_rate=snapshot.full_watch_rate,
        skip_rate=snapshot.skip_rate,
        raw_json=snapshot.raw,
    )


def _posts_with_renders(
    session: Session, platform: Platform, *conditions: ColumnElement[bool]
) -> list[tuple[PostRow, RenderRow]]:
    rows = session.execute(
        select(PostRow, RenderRow)
        .join(RenderRow, PostRow.render_id == RenderRow.id)
        .where(PostRow.platform == platform, *conditions)
        .order_by(PostRow.id)
    ).all()
    return [(post, render) for post, render in rows]


def _downloaded_at(post: PostRow, render: RenderRow) -> datetime:
    return render.downloaded_at or post.created_at


def _awaiting_post(post: PostRow, render: RenderRow) -> AwaitingPost:
    assert render.duration_s is not None
    return AwaitingPost(
        post_id=post.id,
        platform=post.platform,
        downloaded_at=_downloaded_at(post, render),
        duration_s=render.duration_s,
        caption=str(render.captions_json.get(str(post.platform), "")),
    )


def link_post(session: Session, render_id: int, platform: Platform, url: str) -> PostRow:
    """Manually match a render's post to a pasted link, as the hand-off page's Link post form
    does. The posting time (and the Instagram media id) is filled in by the next measure run.
    Flushes but doesn't commit."""
    try:
        link = parse_post_url(platform, url)
    except ValueError as error:
        raise LinkError(str(error)) from None
    post = session.scalar(
        select(PostRow).where(PostRow.render_id == render_id, PostRow.platform == platform)
    )
    if post is None:
        raise LinkError(
            f"render {render_id} has no {platform} post: download it from the hand-off page first"
        )
    already_linked = session.scalar(
        select(PostRow.id).where(
            PostRow.platform == platform,
            PostRow.id != post.id,
            or_(PostRow.external_id == link.external_id, PostRow.permalink == link.permalink),
        )
    )
    if already_linked is not None:
        raise LinkError(f"that video is already linked to post {already_linked}")

    post.status = PostStatus.MATCHED
    post.match_method = MatchMethod.MANUAL
    post.match_score = None
    post.external_id = link.external_id
    post.permalink = link.permalink
    post.posted_at = None
    session.flush()
    return post
