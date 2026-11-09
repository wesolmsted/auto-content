"""Pure post matching (DESIGN.md §7.6): link hand-posted platform videos back to their posts."""

import re
from collections.abc import Sequence
from datetime import timedelta

from engine.config import MatchSettings
from engine.measure.models import AwaitingPost, Match, PlatformVideo


def caption_tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def caption_similarity(provided: str, posted: str) -> float:
    """Share of the provided caption's tokens that appear in the posted caption. The owner may
    add words (hashtags, emoji) when posting, so extra posted tokens don't lower the score."""
    provided_tokens = caption_tokens(provided)
    if not provided_tokens:
        return 0.0
    return len(provided_tokens & caption_tokens(posted)) / len(provided_tokens)


def time_proximity(post: AwaitingPost, candidate: PlatformVideo, settings: MatchSettings) -> float:
    """1 when posted at (or within the slack before) download, falling to 0 at the window end."""
    delay_hours = max(0.0, (candidate.posted_at - post.downloaded_at) / timedelta(hours=1))
    return max(0.0, 1 - delay_hours / settings.window_hours)


def passes_filters(post: AwaitingPost, candidate: PlatformVideo, settings: MatchSettings) -> bool:
    earliest = post.downloaded_at - timedelta(hours=settings.fetch_slack_hours)
    duration_ok = (
        candidate.duration_s is None
        or abs(candidate.duration_s - post.duration_s) <= settings.duration_tolerance_s
    )
    return candidate.platform == post.platform and candidate.posted_at >= earliest and duration_ok


def match_score(post: AwaitingPost, candidate: PlatformVideo, settings: MatchSettings) -> float:
    return settings.caption_weight * caption_similarity(
        post.caption, candidate.caption
    ) + settings.time_weight * time_proximity(post, candidate, settings)


def match_posts(
    awaiting: Sequence[AwaitingPost],
    candidates: Sequence[PlatformVideo],
    settings: MatchSettings,
) -> list[Match]:
    """Greedy best-first assignment: each post and each candidate is used at most once, and
    pairs failing the hard filters or scoring below `min_score` are never matched."""
    scored_pairs = sorted(
        (
            (match_score(post, candidate, settings), post, candidate)
            for post in awaiting
            for candidate in candidates
            if passes_filters(post, candidate, settings)
        ),
        key=lambda pair: (-pair[0], pair[1].post_id, pair[2].external_id),
    )
    matches: list[Match] = []
    used_posts: set[int] = set()
    used_candidates: set[str] = set()
    for score, post, candidate in scored_pairs:
        if score < settings.min_score:
            break
        if post.post_id in used_posts or candidate.external_id in used_candidates:
            continue
        used_posts.add(post.post_id)
        used_candidates.add(candidate.external_id)
        matches.append(
            Match(
                post_id=post.post_id,
                external_id=candidate.external_id,
                permalink=candidate.permalink,
                posted_at=candidate.posted_at,
                score=score,
            )
        )
    return matches
