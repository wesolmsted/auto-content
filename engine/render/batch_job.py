"""`ace render batch`: plan, render, record shot usage, notify (DESIGN.md §7.3 to §7.5)."""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import Settings
from engine.db.enums import RenderStatus
from engine.db.models import RecipeRow, RenderRow, ShotRow
from engine.notify.base import Notifier
from engine.notify.handoff import send_pending_handoff_notifications
from engine.recipes.batch import plan_batch
from engine.recipes.models import Recipe
from engine.render.job import execute_plan
from engine.storage.media_store import MediaStore


@dataclass(frozen=True)
class BatchSummary:
    planned: int
    ready: int
    failed: int
    notified: int


def run_render_batch(
    session: Session,
    settings: Settings,
    store: MediaStore,
    notifier: Notifier | None,
    rng: random.Random,
    now: datetime,
    n: int,
    *,
    snippet_id: int | None = None,
) -> BatchSummary:
    """Plan up to `n` renders and execute each one, then send the pending hand-off notifications.

    Each plan's `recipes` row is created if missing. A ready render adds one to its shots'
    `use_count` and sets `last_used_at = now`; a failed render is left `failed` and the batch
    continues. Notifications respect quiet hours, so a morning batch's are held. With no
    notifier, nothing is sent and the renders stay pending. Commits.
    """
    plans = plan_batch(session, settings, rng, now, n, snippet_id=snippet_id)
    recipes = {recipe.id: recipe for recipe in settings.recipes.fixed}

    ready = 0
    for plan in plans:
        _ensure_recipe_row(session, recipes[plan.recipe_id])
        render_id = execute_plan(session, plan, store, settings)
        if session.get_one(RenderRow, render_id).status is RenderStatus.READY:
            _record_shot_use(session, plan.timeline.shot_ids, now)
            ready += 1
        session.commit()

    notified = 0
    if notifier is not None:
        notified = send_pending_handoff_notifications(session, notifier, settings, now)
        session.commit()
    return BatchSummary(
        planned=len(plans), ready=ready, failed=len(plans) - ready, notified=notified
    )


def _ensure_recipe_row(session: Session, recipe: Recipe) -> None:
    if session.get(RecipeRow, recipe.id) is None:
        session.add(
            RecipeRow(id=recipe.id, params_json=recipe.canonical_params(), source=recipe.source)
        )


def _record_shot_use(session: Session, shot_ids: Sequence[int], now: datetime) -> None:
    for shot in session.scalars(select(ShotRow).where(ShotRow.id.in_(shot_ids))):
        shot.use_count += 1
        shot.last_used_at = now
