from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import load_settings
from engine.db.enums import (
    MatchMethod,
    MetricCheckpoint,
    MetricSource,
    Platform,
    PostStatus,
    RenderStatus,
)
from engine.db.models import MetricRow, PostRow
from engine.measure.job import LinkError, link_post, run_measure
from engine.measure.models import MetricSnapshot, PlatformVideo
from tests.fakes.measure import FakePlatformReader
from tests.integration.test_notify_handoff import add_render

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SETTINGS = load_settings(REPO_CONFIG_DIR, env_file=None)
DOWNLOADED = datetime(2026, 11, 8, 16, 0, tzinfo=UTC)
NOW = DOWNLOADED + timedelta(hours=2)


def add_post(
    session: Session,
    caption: str = "Night Drive",
    platform: Platform = Platform.IG_TRIAL,
    downloaded_at: datetime = DOWNLOADED,
    duration_s: float = 7.0,
    status: PostStatus = PostStatus.AWAITING_POST,
    external_id: str | None = None,
    posted_at: datetime | None = None,
) -> PostRow:
    render = add_render(
        session,
        song_title=f"{caption} {platform} {downloaded_at.isoformat()}",
        status=RenderStatus.APPROVED,
        targets=(platform,),
        duration_s=duration_s,
    )
    render.downloaded_at = downloaded_at
    render.download_count = 1
    render.captions_json = {str(platform): caption}
    post = PostRow(
        render_id=render.id,
        platform=platform,
        status=status,
        external_id=external_id,
        posted_at=posted_at,
        match_method=MatchMethod.AUTO if status == PostStatus.MATCHED else None,
    )
    session.add(post)
    session.flush()
    return post


def ig_video(
    external_id: str,
    posted_at: datetime,
    caption: str = "Night Drive #fyp",
    platform: Platform = Platform.IG_TRIAL,
    duration_s: float | None = None,
) -> PlatformVideo:
    return PlatformVideo(
        platform=platform,
        external_id=external_id,
        permalink=f"https://www.instagram.com/reel/code{external_id}/",
        posted_at=posted_at,
        caption=caption,
        duration_s=duration_s,
    )


def snapshot(views: int, fetched_at: datetime = NOW) -> MetricSnapshot:
    return MetricSnapshot(
        fetched_at=fetched_at, views=views, reach=views // 2, skip_rate=0.4, raw={"views": views}
    )


def metric_rows(session: Session) -> list[MetricRow]:
    return list(session.scalars(select(MetricRow).order_by(MetricRow.post_id, MetricRow.id)))


def test_matches_an_awaiting_post_to_the_video_posted_after_its_download(
    db_session: Session,
) -> None:
    post = add_post(db_session)
    posted = DOWNLOADED + timedelta(minutes=20)
    reader = FakePlatformReader(
        Platform.IG_TRIAL,
        [
            ig_video("too-early", DOWNLOADED - timedelta(hours=3), caption="Night Drive"),
            ig_video("reel-1", posted),
            ig_video("unrelated", DOWNLOADED + timedelta(minutes=50), caption="Beach day"),
        ],
    )

    summary = run_measure(db_session, [reader], SETTINGS, NOW)

    assert (summary.matched, summary.not_posted, summary.errors) == (1, 0, [])
    assert reader.listed_since == [DOWNLOADED - timedelta(hours=SETTINGS.match.fetch_slack_hours)]
    assert post.status == PostStatus.MATCHED
    assert post.match_method == MatchMethod.AUTO
    assert post.external_id == "reel-1"
    assert post.permalink == "https://www.instagram.com/reel/codereel-1/"
    assert post.posted_at == posted
    assert post.match_score == pytest.approx(0.7 + 0.3 * (1 - (20 / 60) / 72))


def test_fetches_since_the_oldest_download_among_awaiting_posts(db_session: Session) -> None:
    add_post(db_session, caption="One", downloaded_at=DOWNLOADED)
    add_post(db_session, caption="Two", downloaded_at=DOWNLOADED - timedelta(hours=10))
    reader = FakePlatformReader(Platform.IG_TRIAL)

    run_measure(db_session, [reader], SETTINGS, NOW)

    assert reader.listed_since == [DOWNLOADED - timedelta(hours=11)]


def test_a_video_already_linked_to_another_post_is_not_matched_again(db_session: Session) -> None:
    add_post(
        db_session,
        status=PostStatus.MATCHED,
        external_id="reel-1",
        posted_at=DOWNLOADED + timedelta(minutes=5),
    )
    waiting = add_post(db_session, downloaded_at=DOWNLOADED + timedelta(seconds=1))
    reader = FakePlatformReader(
        Platform.IG_TRIAL,
        [
            ig_video("reel-1", DOWNLOADED + timedelta(minutes=5)),
            ig_video("reel-2", DOWNLOADED + timedelta(minutes=40)),
        ],
    )

    run_measure(db_session, [reader], SETTINGS, NOW)

    assert waiting.external_id == "reel-2"


def test_an_unmatched_post_becomes_not_posted_only_after_the_window(db_session: Session) -> None:
    window = timedelta(hours=SETTINGS.match.window_hours)
    expired = add_post(db_session, caption="Old", downloaded_at=NOW - window)
    recent = add_post(db_session, caption="New", downloaded_at=NOW - window + timedelta(minutes=1))

    summary = run_measure(db_session, [FakePlatformReader(Platform.IG_TRIAL)], SETTINGS, NOW)

    assert summary.not_posted == 1
    assert expired.status == PostStatus.NOT_POSTED
    assert recent.status == PostStatus.AWAITING_POST


def test_posts_on_a_platform_without_a_reader_are_left_alone(db_session: Session) -> None:
    window = timedelta(hours=SETTINGS.match.window_hours)
    tiktok = add_post(db_session, platform=Platform.TIKTOK, downloaded_at=NOW - 2 * window)

    run_measure(db_session, [FakePlatformReader(Platform.IG_TRIAL)], SETTINGS, NOW)

    assert tiktok.status == PostStatus.AWAITING_POST


def test_a_listing_failure_is_recorded_and_keeps_posts_awaiting(db_session: Session) -> None:
    window = timedelta(hours=SETTINGS.match.window_hours)
    old = add_post(db_session, downloaded_at=NOW - 2 * window)
    matched = add_post(
        db_session,
        caption="Matched",
        status=PostStatus.MATCHED,
        external_id="reel-9",
        posted_at=NOW - timedelta(hours=25),
    )
    reader = FakePlatformReader(Platform.IG_TRIAL, metrics={"reel-9": snapshot(500)})
    reader.fail_listing = True

    summary = run_measure(db_session, [reader], SETTINGS, NOW)

    assert old.status == PostStatus.AWAITING_POST
    assert len(summary.errors) == 1
    assert "ig_trial" in summary.errors[0]
    assert "fake reader told to fail listing" in summary.errors[0]
    assert [(row.post_id, row.checkpoint) for row in metric_rows(db_session)] == [
        (matched.id, MetricCheckpoint.H24)
    ]


def test_inserts_the_24h_checkpoint_once_due_and_is_idempotent(db_session: Session) -> None:
    posted = NOW - timedelta(hours=24)
    post = add_post(db_session, status=PostStatus.MATCHED, external_id="reel-1", posted_at=posted)
    not_yet = add_post(
        db_session,
        caption="Later",
        status=PostStatus.MATCHED,
        external_id="reel-2",
        posted_at=NOW - timedelta(hours=23, minutes=59),
    )
    reader = FakePlatformReader(
        Platform.IG_TRIAL, metrics={"reel-1": snapshot(1200), "reel-2": snapshot(5)}
    )

    first = run_measure(db_session, [reader], SETTINGS, NOW)
    second = run_measure(db_session, [reader], SETTINGS, NOW + timedelta(seconds=30))

    assert (first.metrics_inserted, second.metrics_inserted) == (1, 0)
    assert reader.fetched == ["reel-1"]
    [row] = metric_rows(db_session)
    assert row.post_id == post.id
    assert row.checkpoint == MetricCheckpoint.H24
    assert row.source == MetricSource.API
    assert row.fetched_at == NOW
    assert (row.views, row.reach, row.skip_rate) == (1200, 600, 0.4)
    assert (row.likes, row.comments, row.avg_watch_s, row.full_watch_rate) == (
        None,
        None,
        None,
        None,
    )
    assert row.raw_json == {"views": 1200}
    assert not_yet.id not in {r.post_id for r in metric_rows(db_session)}


def test_a_post_past_72h_with_no_rows_gets_both_checkpoints_from_one_fetch(
    db_session: Session,
) -> None:
    add_post(
        db_session,
        status=PostStatus.MATCHED,
        external_id="reel-1",
        posted_at=NOW - timedelta(hours=80),
    )
    reader = FakePlatformReader(Platform.IG_TRIAL, metrics={"reel-1": snapshot(900)})

    summary = run_measure(db_session, [reader], SETTINGS, NOW)

    assert summary.metrics_inserted == 2
    assert reader.fetched == ["reel-1"]
    assert [row.checkpoint for row in metric_rows(db_session)] == [
        MetricCheckpoint.H24,
        MetricCheckpoint.H72,
    ]


def test_only_the_missing_checkpoint_is_fetched(db_session: Session) -> None:
    post = add_post(
        db_session,
        status=PostStatus.MATCHED,
        external_id="reel-1",
        posted_at=NOW - timedelta(hours=72),
    )
    db_session.add(
        MetricRow(
            post_id=post.id,
            checkpoint=MetricCheckpoint.H24,
            source=MetricSource.API,
            fetched_at=NOW - timedelta(hours=48),
            views=100,
        )
    )
    db_session.flush()
    reader = FakePlatformReader(Platform.IG_TRIAL, metrics={"reel-1": snapshot(900)})

    run_measure(db_session, [reader], SETTINGS, NOW)

    assert [(row.checkpoint, row.views) for row in metric_rows(db_session)] == [
        (MetricCheckpoint.H24, 100),
        (MetricCheckpoint.H72, 900),
    ]


def test_a_failed_metric_fetch_is_recorded_and_the_job_continues(db_session: Session) -> None:
    failing = add_post(
        db_session,
        caption="Failing",
        status=PostStatus.MATCHED,
        external_id="reel-bad",
        posted_at=NOW - timedelta(hours=30),
    )
    working = add_post(
        db_session,
        caption="Working",
        status=PostStatus.MATCHED,
        external_id="reel-ok",
        posted_at=NOW - timedelta(hours=30),
    )
    reader = FakePlatformReader(
        Platform.IG_TRIAL, metrics={"reel-bad": snapshot(1), "reel-ok": snapshot(2)}
    )
    reader.fail_metrics = {"reel-bad"}

    summary = run_measure(db_session, [reader], SETTINGS, NOW)

    assert summary.metrics_inserted == 1
    assert [row.post_id for row in metric_rows(db_session)] == [working.id]
    assert len(summary.errors) == 1
    assert f"post {failing.id}" in summary.errors[0]

    reader.fail_metrics = set()
    retry = run_measure(db_session, [reader], SETTINGS, NOW + timedelta(hours=1))
    assert retry.metrics_inserted == 1


def test_a_post_matched_in_this_run_gets_metrics_when_already_due(db_session: Session) -> None:
    now = DOWNLOADED + timedelta(hours=30)
    add_post(db_session)
    reader = FakePlatformReader(
        Platform.IG_TRIAL,
        [ig_video("reel-1", DOWNLOADED + timedelta(minutes=10))],
        metrics={"reel-1": snapshot(10, fetched_at=now)},
    )

    summary = run_measure(db_session, [reader], SETTINGS, now)

    assert (summary.matched, summary.metrics_inserted) == (1, 1)


def test_link_post_stores_the_reel_code_and_canonical_link_like_the_handoff_page(
    db_session: Session,
) -> None:
    post = add_post(db_session, status=PostStatus.NOT_POSTED)

    linked = link_post(
        db_session, post.render_id, Platform.IG_TRIAL, "https://instagram.com/reel/C9zz/?igsh=a"
    )

    assert linked is post
    assert post.status == PostStatus.MATCHED
    assert post.match_method == MatchMethod.MANUAL
    assert post.match_score is None
    assert post.external_id == "C9zz"
    assert post.permalink == "https://www.instagram.com/reel/C9zz/"
    assert post.posted_at is None


def test_link_post_for_tiktok_stores_the_video_id(db_session: Session) -> None:
    post = add_post(db_session, platform=Platform.TIKTOK)
    url = "https://www.tiktok.com/@owner/video/7300000000000000001?is_from_webapp=1"

    link_post(db_session, post.render_id, Platform.TIKTOK, url)

    assert (post.status, post.match_method) == (PostStatus.MATCHED, MatchMethod.MANUAL)
    assert post.external_id == "7300000000000000001"
    assert post.permalink == "https://www.tiktok.com/@owner/video/7300000000000000001"
    assert post.posted_at is None


def test_relinking_a_matched_post_replaces_the_auto_match(db_session: Session) -> None:
    post = add_post(db_session, status=PostStatus.MATCHED, external_id="1790", posted_at=DOWNLOADED)
    post.match_score = 0.8
    link_post(db_session, post.render_id, Platform.IG_TRIAL, "https://instagram.com/reel/Right/")
    assert (post.external_id, post.posted_at, post.match_score) == ("Right", None, None)
    assert post.match_method == MatchMethod.MANUAL


def test_link_post_without_a_post_row_says_to_download_first(db_session: Session) -> None:
    render = add_render(db_session)
    with pytest.raises(LinkError, match="download"):
        link_post(
            db_session,
            render.id,
            Platform.TIKTOK,
            "https://www.tiktok.com/@owner/video/7300000000000000001",
        )


@pytest.mark.parametrize(
    ("existing_id", "existing_permalink"),
    [
        ("7301", None),
        ("7301000", "https://www.tiktok.com/@owner/video/7301"),
    ],
)
def test_link_post_refuses_a_video_already_linked_to_another_post(
    db_session: Session, existing_id: str, existing_permalink: str | None
) -> None:
    other = add_post(
        db_session, platform=Platform.TIKTOK, status=PostStatus.MATCHED, external_id=existing_id
    )
    other.permalink = existing_permalink
    post = add_post(db_session, caption="Other", platform=Platform.TIKTOK)
    with pytest.raises(LinkError, match="already linked"):
        link_post(
            db_session, post.render_id, Platform.TIKTOK, "https://www.tiktok.com/@owner/video/7301"
        )
    assert post.status == PostStatus.AWAITING_POST


def test_link_post_rejects_an_unreadable_link(db_session: Session) -> None:
    post = add_post(db_session, platform=Platform.TIKTOK)
    with pytest.raises(LinkError, match="link"):
        link_post(db_session, post.render_id, Platform.TIKTOK, "https://vm.tiktok.com/ZMabc/")


def add_manual_post(
    session: Session, external_id: str, permalink: str, platform: Platform = Platform.IG_TRIAL
) -> PostRow:
    """A post as the hand-off page's Link post form leaves it: matched, no posting time."""
    post = add_post(
        session, caption=f"manual {external_id}", platform=platform, status=PostStatus.MATCHED
    )
    post.match_method = MatchMethod.MANUAL
    post.external_id = external_id
    post.permalink = permalink
    return post


def test_a_manual_instagram_link_is_resolved_to_the_media_id_and_posting_time(
    db_session: Session,
) -> None:
    post = add_manual_post(db_session, "code1790", "https://www.instagram.com/reel/code1790/")
    posted = DOWNLOADED + timedelta(hours=1)
    reader = FakePlatformReader(
        Platform.IG_TRIAL,
        [ig_video("1791", posted), ig_video("1790", posted)],
        metrics={"1790": snapshot(10)},
    )

    summary = run_measure(db_session, [reader], SETTINGS, DOWNLOADED + timedelta(hours=26))

    assert summary.errors == []
    assert (post.external_id, post.posted_at) == ("1790", posted)
    assert post.permalink == "https://www.instagram.com/reel/code1790/"
    assert (post.status, post.match_method) == (PostStatus.MATCHED, MatchMethod.MANUAL)
    assert reader.listed_since == [DOWNLOADED - timedelta(hours=SETTINGS.match.fetch_slack_hours)]
    assert reader.fetched == ["1790"]
    assert [row.checkpoint for row in metric_rows(db_session)] == [MetricCheckpoint.H24]


def test_a_manual_tiktok_link_gets_its_posting_time(db_session: Session) -> None:
    post = add_manual_post(
        db_session,
        "7300000000000000001",
        "https://www.tiktok.com/@owner/video/7300000000000000001",
        platform=Platform.TIKTOK,
    )
    posted = DOWNLOADED + timedelta(hours=2)
    reader = FakePlatformReader(
        Platform.TIKTOK,
        [ig_video("7300000000000000001", posted, platform=Platform.TIKTOK, duration_s=7.0)],
    )

    run_measure(db_session, [reader], SETTINGS, NOW)

    assert (post.external_id, post.posted_at) == ("7300000000000000001", posted)


def test_a_resolved_manual_video_is_not_auto_matched_to_another_post(db_session: Session) -> None:
    add_manual_post(db_session, "code1790", "https://www.instagram.com/reel/code1790/")
    waiting = add_post(db_session, downloaded_at=DOWNLOADED + timedelta(seconds=1))
    reader = FakePlatformReader(
        Platform.IG_TRIAL, [ig_video("1790", DOWNLOADED + timedelta(minutes=5))]
    )

    run_measure(db_session, [reader], SETTINGS, NOW)

    assert waiting.status == PostStatus.AWAITING_POST


def test_a_manual_link_not_found_yet_is_reported_and_retried(db_session: Session) -> None:
    post = add_manual_post(db_session, "codeX", "https://www.instagram.com/reel/codeX/")
    reader = FakePlatformReader(Platform.IG_TRIAL, [ig_video("1790", NOW)])

    summary = run_measure(db_session, [reader], SETTINGS, NOW)

    assert post.posted_at is None
    assert len(summary.errors) == 1
    assert f"post {post.id}" in summary.errors[0]
    assert "codeX" in summary.errors[0]

    reader.videos.append(ig_video("1800", NOW, caption="x"))
    reader.videos[-1] = reader.videos[-1].model_copy(
        update={"permalink": "https://www.instagram.com/reel/codeX/"}
    )
    run_measure(db_session, [reader], SETTINGS, NOW + timedelta(hours=1))
    assert (post.external_id, post.posted_at) == ("1800", NOW)
