from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.config import load_settings
from engine.db.enums import Platform
from engine.measure.matching import caption_similarity, match_posts, parse_post_url
from engine.measure.models import AwaitingPost, Match, PlatformVideo

REPO_ROOT = Path(__file__).resolve().parents[2]
MATCH_SETTINGS = load_settings(REPO_ROOT / "config", env_file=None).match
DOWNLOADED = datetime(2026, 11, 8, 16, 0, tzinfo=UTC)
WORDS = ["night", "drive", "neon", "city", "summer", "drop", "#fyp", "vibes"]


def awaiting(
    post_id: int = 1,
    caption: str = "Night Drive",
    downloaded_at: datetime = DOWNLOADED,
    duration_s: float = 7.0,
    platform: Platform = Platform.IG_TRIAL,
) -> AwaitingPost:
    return AwaitingPost(
        post_id=post_id,
        platform=platform,
        downloaded_at=downloaded_at,
        duration_s=duration_s,
        caption=caption,
    )


def video(
    external_id: str = "v1",
    caption: str = "Night Drive",
    posted_at: datetime = DOWNLOADED + timedelta(hours=1),
    duration_s: float | None = 7.0,
    platform: Platform = Platform.IG_TRIAL,
) -> PlatformVideo:
    return PlatformVideo(
        platform=platform,
        external_id=external_id,
        permalink=f"https://example.test/{external_id}",
        posted_at=posted_at,
        caption=caption,
        duration_s=duration_s,
    )


def expected_score(similarity: float, delay_hours: float) -> float:
    proximity = max(0.0, 1 - delay_hours / MATCH_SETTINGS.window_hours)
    return MATCH_SETTINGS.caption_weight * similarity + MATCH_SETTINGS.time_weight * proximity


@pytest.mark.parametrize(
    ("provided", "posted", "similarity"),
    [
        ("Night Drive", "night drive 🌙 #fyp", 1.0),
        ("Night Drive", "drive", 0.5),
        ("Night Drive", "NIGHT, drive!", 1.0),
        ("Night Drive", "something else", 0.0),
        ("", "night drive", 0.0),
        ("Night Night Drive", "night", 0.5),
    ],
)
def test_caption_similarity_is_the_share_of_provided_tokens_found(
    provided: str, posted: str, similarity: float
) -> None:
    assert caption_similarity(provided, posted) == pytest.approx(similarity)


def test_matches_a_video_posted_after_download_with_caption_and_time_score() -> None:
    [match] = match_posts([awaiting()], [video()], MATCH_SETTINGS)
    assert match.score == pytest.approx(expected_score(1.0, 1.0))
    assert match == Match(
        post_id=1,
        external_id="v1",
        permalink="https://example.test/v1",
        posted_at=DOWNLOADED + timedelta(hours=1),
        score=match.score,
    )


def test_a_video_posted_within_the_slack_before_download_counts_as_no_delay() -> None:
    early = video(posted_at=DOWNLOADED - timedelta(minutes=30))
    [match] = match_posts([awaiting()], [early], MATCH_SETTINGS)
    assert match.score == pytest.approx(expected_score(1.0, 0.0))


def test_a_video_posted_before_download_minus_slack_is_filtered_out() -> None:
    too_early = video(posted_at=DOWNLOADED - timedelta(hours=1, minutes=1))
    assert match_posts([awaiting()], [too_early], MATCH_SETTINGS) == []


@pytest.mark.parametrize(
    ("candidate_duration", "matched"),
    [(7.5, True), (6.5, True), (7.6, False), (6.4, False), (None, True)],
)
def test_duration_filter_uses_the_tolerance_and_is_skipped_when_unknown(
    candidate_duration: float | None, matched: bool
) -> None:
    matches = match_posts([awaiting()], [video(duration_s=candidate_duration)], MATCH_SETTINGS)
    assert (len(matches) == 1) is matched


def test_a_video_on_another_platform_is_never_matched() -> None:
    assert match_posts([awaiting()], [video(platform=Platform.TIKTOK)], MATCH_SETTINGS) == []


def test_pairs_scoring_below_min_score_stay_unmatched() -> None:
    weak = video(caption="totally different", posted_at=DOWNLOADED + timedelta(hours=60))
    assert expected_score(0.0, 60.0) < MATCH_SETTINGS.min_score
    assert match_posts([awaiting()], [weak], MATCH_SETTINGS) == []


def test_with_default_weights_a_caption_less_video_matches_only_with_no_delay() -> None:
    at_download = video("now", caption="", posted_at=DOWNLOADED)
    five_minutes_later = video("later", caption="", posted_at=DOWNLOADED + timedelta(minutes=5))

    [match] = match_posts([awaiting()], [at_download], MATCH_SETTINGS)
    assert match.score == pytest.approx(MATCH_SETTINGS.min_score)
    assert match_posts([awaiting()], [five_minutes_later], MATCH_SETTINGS) == []


def test_greedy_assignment_gives_each_post_its_best_unique_video() -> None:
    posts = [
        awaiting(post_id=1, caption="Night Drive"),
        awaiting(post_id=2, caption="Summer Neon"),
    ]
    candidates = [
        video("summer", caption="summer neon", posted_at=DOWNLOADED + timedelta(hours=3)),
        video("night", caption="night drive", posted_at=DOWNLOADED + timedelta(hours=2)),
    ]
    matches = match_posts(posts, candidates, MATCH_SETTINGS)
    assert {(m.post_id, m.external_id) for m in matches} == {(1, "night"), (2, "summer")}


def test_the_best_pair_wins_and_the_loser_takes_what_is_left() -> None:
    posts = [awaiting(post_id=1), awaiting(post_id=2)]
    candidates = [
        video("late", posted_at=DOWNLOADED + timedelta(hours=10)),
        video("soon", posted_at=DOWNLOADED + timedelta(hours=1)),
    ]
    matches = sorted(match_posts(posts, candidates, MATCH_SETTINGS), key=lambda m: m.post_id)
    assert [(m.post_id, m.external_id) for m in matches] == [(1, "soon"), (2, "late")]


def test_empty_inputs_give_no_matches() -> None:
    assert match_posts([], [video()], MATCH_SETTINGS) == []
    assert match_posts([awaiting()], [], MATCH_SETTINGS) == []


@pytest.mark.parametrize(
    ("platform", "url", "external_ref"),
    [
        (Platform.IG_TRIAL, "https://www.instagram.com/reel/DAbc-12_x/?igsh=xyz", "DAbc-12_x"),
        (Platform.IG_TRIAL, "https://instagram.com/reel/C9zzTop/", "C9zzTop"),
        (Platform.IG_TRIAL, "https://www.instagram.com/p/C9zzTop/", "C9zzTop"),
        (
            Platform.TIKTOK,
            "https://www.tiktok.com/@some.user/video/7300000000000000001?is_from_webapp=1",
            "7300000000000000001",
        ),
        (Platform.TIKTOK, "tiktok.com/@u/video/7300000000000000002", "7300000000000000002"),
    ],
)
def test_parse_post_url_extracts_the_reel_code_or_video_id(
    platform: Platform, url: str, external_ref: str
) -> None:
    assert parse_post_url(platform, url) == external_ref


@pytest.mark.parametrize(
    ("platform", "url"),
    [
        (Platform.IG_TRIAL, "https://www.tiktok.com/@u/video/7300000000000000001"),
        (Platform.IG_TRIAL, "https://www.instagram.com/some.user/"),
        (Platform.TIKTOK, "https://www.instagram.com/reel/DAbc/"),
        (Platform.TIKTOK, "https://vm.tiktok.com/ZMabc123/"),
        (Platform.TIKTOK, "not a url"),
    ],
)
def test_parse_post_url_rejects_links_it_cannot_read(platform: Platform, url: str) -> None:
    with pytest.raises(ValueError, match="link"):
        parse_post_url(platform, url)


captions = st.lists(st.sampled_from(WORDS), max_size=4).map(" ".join)
post_captions = st.lists(st.sampled_from(WORDS), min_size=1, max_size=4).map(" ".join)


@st.composite
def awaiting_posts(draw: st.DrawFn) -> list[AwaitingPost]:
    # Downloads at least 2 h apart, so a video posted at one download is outside the others' slack.
    download_slots = draw(st.lists(st.integers(0, 100), unique=True, max_size=6))
    return [
        awaiting(
            post_id=index + 1,
            caption=draw(post_captions),
            downloaded_at=DOWNLOADED + timedelta(hours=2 * slot),
            duration_s=draw(st.sampled_from([7.0, 12.0, 20.0])),
        )
        for index, slot in enumerate(download_slots)
    ]


@st.composite
def candidate_videos(draw: st.DrawFn) -> list[PlatformVideo]:
    count = draw(st.integers(0, 8))
    return [
        video(
            external_id=f"v{index}",
            caption=draw(captions),
            # Half-minute offsets never coincide with a download time (whole hours).
            posted_at=DOWNLOADED + timedelta(minutes=draw(st.integers(-120, 300 * 60)) + 0.5),
            duration_s=draw(st.one_of(st.none(), st.floats(5.0, 22.0))),
        )
        for index in range(count)
    ]


def passes_filters(post: AwaitingPost, candidate: PlatformVideo) -> bool:
    earliest = post.downloaded_at - timedelta(hours=MATCH_SETTINGS.fetch_slack_hours)
    duration_ok = (
        candidate.duration_s is None
        or abs(candidate.duration_s - post.duration_s) <= MATCH_SETTINGS.duration_tolerance_s
    )
    return candidate.posted_at >= earliest and duration_ok


@settings(max_examples=300)
@given(awaiting_posts(), candidate_videos())
def test_matches_are_unique_respect_filters_and_min_score(
    posts: list[AwaitingPost], candidates: list[PlatformVideo]
) -> None:
    matches = match_posts(posts, candidates, MATCH_SETTINGS)
    posts_by_id = {post.post_id: post for post in posts}
    candidates_by_id = {candidate.external_id: candidate for candidate in candidates}

    assert len({m.post_id for m in matches}) == len(matches)
    assert len({m.external_id for m in matches}) == len(matches)
    for match in matches:
        post, candidate = posts_by_id[match.post_id], candidates_by_id[match.external_id]
        assert passes_filters(post, candidate)
        assert match.score >= MATCH_SETTINGS.min_score
        assert match.posted_at == candidate.posted_at


@settings(max_examples=300)
@given(awaiting_posts().filter(bool), candidate_videos(), st.data())
def test_a_perfect_caption_and_time_match_beats_weaker_candidates(
    posts: list[AwaitingPost], candidates: list[PlatformVideo], data: st.DataObject
) -> None:
    target = data.draw(st.sampled_from(posts))
    perfect = video(
        external_id="perfect",
        caption=target.caption.upper() + " #fyp",
        posted_at=target.downloaded_at,
        duration_s=target.duration_s,
    )
    # A candidate in the target's slack window counts as no delay and could tie the perfect one.
    slack_start = target.downloaded_at - timedelta(hours=MATCH_SETTINGS.fetch_slack_hours)
    weaker = [c for c in candidates if not slack_start <= c.posted_at <= target.downloaded_at]
    shuffled = data.draw(st.permutations([*weaker, perfect]))

    matches = match_posts(posts, shuffled, MATCH_SETTINGS)

    assert {m.post_id: m.external_id for m in matches}[target.post_id] == "perfect"
