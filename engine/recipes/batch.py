"""Phase 1 batch planner: which renders to make (DESIGN.md §7.3). Reads the DB, writes nothing."""

import logging
import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import Settings
from engine.db.enums import Platform, RenderStatus, SnippetType
from engine.db.models import RenderRow, ShotRow, SnippetRow, SongRow
from engine.ingest.models import Shot
from engine.ingest.shot_rows import shot_from_row
from engine.recipes.assembly import assemble
from engine.recipes.models import Recipe, RenderPlan
from engine.recipes.selection import select_shots
from engine.recipes.slots import NotEnoughShotsError
from engine.snippets.analysis import beat_grid_for_snippet
from engine.snippets.models import BeatGrid, SongAnalysis

logger = logging.getLogger(__name__)

REPEAT_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class SnippetChoice:
    id: int
    snippet_type: SnippetType
    beat_grid: BeatGrid


def plan_batch(
    session: Session, settings: Settings, rng: random.Random, now: datetime, n: int
) -> list[RenderPlan]:
    """Up to `n` render plans pairing a fixed recipe with an active snippet of its type.

    A recipe already rendered `recipes.max_repeats_per_week` times in the last 7 days (failed
    renders aside, this batch included) is not picked. Shots planned earlier in the batch count
    as used `now`, so the cooldown keeps them out of later plans. Pairs the shot library can't
    fill are skipped; the batch is shorter when no pair is left. Every plan targets `ig_trial`;
    the first plans of the owner's day (`notify.timezone`) also target `tiktok` until
    `cadence.tiktok_renders_per_day` is reached, or all do with `cadence.tiktok_every_render`.
    Captions are left empty: the render step fills them from the config templates.
    """
    snippets = _active_snippets(session)
    library = _tagged_shots(session)
    repeats = _recent_render_counts(session, now)
    tiktok_left = settings.cadence.tiktok_renders_per_day - _tiktok_renders_today(
        session, settings.notify.timezone, now
    )

    plans: list[RenderPlan] = []
    for _ in range(n):
        pairs = [
            (recipe, snippet)
            for recipe in settings.recipes.fixed
            if repeats[recipe.id] < settings.recipes.max_repeats_per_week
            for snippet in snippets
            if snippet.snippet_type is recipe.snippet_type
        ]
        rng.shuffle(pairs)
        planned = _first_fillable(pairs, library, rng, settings, now)
        if planned is None:
            break
        recipe, snippet, shots = planned

        targets = [Platform.IG_TRIAL]
        if settings.cadence.tiktok_every_render or tiktok_left > 0:
            targets.append(Platform.TIKTOK)
            tiktok_left -= 1
        plans.append(
            RenderPlan(
                recipe_id=recipe.id,
                snippet_id=snippet.id,
                timeline=assemble(recipe, shots, snippet.beat_grid),
                targets=tuple(targets),
                captions={},
            )
        )
        repeats[recipe.id] += 1
        used_ids = {shot.id for shot in shots}
        library = [
            shot.model_copy(update={"last_used_at": now}) if shot.id in used_ids else shot
            for shot in library
        ]
    return plans


def _first_fillable(
    pairs: list[tuple[Recipe, SnippetChoice]],
    library: list[Shot],
    rng: random.Random,
    settings: Settings,
    now: datetime,
) -> tuple[Recipe, SnippetChoice, list[Shot]] | None:
    for recipe, snippet in pairs:
        try:
            shots = select_shots(recipe, library, snippet.beat_grid, rng, settings.recipes, now)
        except NotEnoughShotsError as error:
            logger.warning(
                "skipping recipe for snippet: %s",
                error,
                extra={"job": "render_batch", "row_id": snippet.id},
            )
            continue
        return recipe, snippet, shots
    return None


def _active_snippets(session: Session) -> list[SnippetChoice]:
    rows = session.execute(
        select(SnippetRow, SongRow)
        .join(SongRow, SnippetRow.song_id == SongRow.id)
        .where(SnippetRow.active, SongRow.bpm.is_not(None), SongRow.duration_s.is_not(None))
        .order_by(SnippetRow.id)
    ).all()
    return [
        SnippetChoice(
            id=snippet.id,
            snippet_type=snippet.snippet_type,
            beat_grid=_beat_grid(snippet, song),
        )
        for snippet, song in rows
    ]


def _beat_grid(snippet: SnippetRow, song: SongRow) -> BeatGrid:
    """A drop outside the snippet window plans as no drop."""
    analysis = SongAnalysis(
        duration_s=song.duration_s or 0.0,
        bpm=song.bpm or 0.0,
        beats=tuple(song.beats_json),
        downbeats=tuple(song.downbeats_json),
    )
    drop_s = snippet.drop_s
    if drop_s is not None and not snippet.start_s <= drop_s <= snippet.end_s:
        drop_s = None
    return beat_grid_for_snippet(analysis, snippet.start_s, snippet.end_s, drop_s)


def _tagged_shots(session: Session) -> list[Shot]:
    rows = session.scalars(
        select(ShotRow)
        .where(
            ShotRow.proxy_path.is_not(None),
            ShotRow.category.is_not(None),
            ShotRow.motion_score.is_not(None),
            ShotRow.quality_score.is_not(None),
        )
        .order_by(ShotRow.id)
    )
    return [shot_from_row(row) for row in rows]


def _recent_render_counts(session: Session, now: datetime) -> Counter[str]:
    recipe_ids = session.scalars(
        select(RenderRow.recipe_id).where(
            RenderRow.created_at > now - REPEAT_WINDOW,
            RenderRow.status != RenderStatus.FAILED,
        )
    )
    return Counter(recipe_ids)


def _tiktok_renders_today(session: Session, timezone: str, now: datetime) -> int:
    local_now = now.astimezone(ZoneInfo(timezone))
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    targets = session.scalars(
        select(RenderRow.targets_json).where(
            RenderRow.created_at >= day_start,
            RenderRow.status != RenderStatus.FAILED,
        )
    )
    return sum(Platform.TIKTOK in render_targets for render_targets in targets)
