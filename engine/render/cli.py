import logging
import random
from datetime import UTC, datetime
from typing import Annotated

import httpx
import typer
from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from engine.config import Settings, load_settings
from engine.db.enums import RenderStatus
from engine.db.models import RenderRow
from engine.db.session import create_db_engine, make_session_factory
from engine.notify.base import Notifier
from engine.notify.ntfy import ntfy_notifier_from_settings
from engine.recipes.batch import plan_batch
from engine.recipes.models import RenderPlan
from engine.render.batch_job import run_render_batch
from engine.storage.media_store import LocalMediaStore

app = typer.Typer(help="Assemble and render videos.", no_args_is_help=True)

logger = logging.getLogger(__name__)


@app.command()
def batch(
    n: Annotated[int | None, typer.Option(help="Renders to make (default: cadence).")] = None,
    recipe: Annotated[str | None, typer.Option(help="Only this recipe id.")] = None,
    snippet: Annotated[int | None, typer.Option(help="Only this snippet id.")] = None,
    dry_run: Annotated[bool, typer.Option(help="Print timelines; render nothing.")] = False,
) -> None:
    """Render the day's batch and send a hand-off notification per finished render."""
    settings = _only_recipe(load_settings(), recipe)
    count = settings.cadence.renders_per_day if n is None else n
    rng = random.Random()
    now = datetime.now(UTC)
    engine = _open_database(settings)
    try:
        with make_session_factory(engine)() as session:
            if dry_run:
                _print_plans(plan_batch(session, settings, rng, now, count, snippet_id=snippet))
                return
            with httpx.Client(timeout=settings.notify.timeout_s) as client:
                summary = run_render_batch(
                    session,
                    settings,
                    LocalMediaStore(settings.media_dir),
                    _notifier_or_none(settings, client),
                    rng,
                    now,
                    count,
                    snippet_id=snippet,
                )
    except ValueError as error:
        typer.echo(str(error))
        raise typer.Exit(code=1) from None
    finally:
        engine.dispose()
    typer.echo(
        f"planned={summary.planned} ready={summary.ready} "
        f"failed={summary.failed} notified={summary.notified}"
    )


@app.command()
def one(
    recipe: Annotated[str, typer.Option(help="Recipe id.")],
    snippet: Annotated[int, typer.Option(help="Snippet id.")],
) -> None:
    """Render a single recipe on a single snippet (dev loop)."""
    settings = _only_recipe(load_settings(), recipe)
    engine = _open_database(settings)
    try:
        with make_session_factory(engine)() as session:
            summary = run_render_batch(
                session,
                settings,
                LocalMediaStore(settings.media_dir),
                None,
                random.Random(),
                datetime.now(UTC),
                1,
                snippet_id=snippet,
            )
            if summary.planned == 0:
                typer.echo("nothing to render: see the log for why the pair was skipped")
                raise typer.Exit(code=1)
            render = _latest_render(session)
    finally:
        engine.dispose()
    if render.status is RenderStatus.READY:
        typer.echo(f"render {render.id} ready: {render.file_path}")
    else:
        typer.echo(f"render {render.id} {render.status}: {render.error}")
        raise typer.Exit(code=1)


def _only_recipe(settings: Settings, recipe_id: str | None) -> Settings:
    if recipe_id is None:
        return settings
    chosen = tuple(recipe for recipe in settings.recipes.fixed if recipe.id == recipe_id)
    if not chosen:
        typer.echo(f"unknown recipe {recipe_id}: not one of recipes.fixed")
        raise typer.Exit(code=1)
    return settings.model_copy(
        update={"recipes": settings.recipes.model_copy(update={"fixed": chosen})}
    )


def _open_database(settings: Settings) -> Engine:
    if settings.database_url is None and not settings.db_path.exists():
        typer.echo("no database yet: run `ace init` first")
        raise typer.Exit(code=1)
    engine = create_db_engine(settings.db_url)
    if not inspect(engine).has_table("renders"):
        engine.dispose()
        typer.echo("database not migrated: run `ace init` first")
        raise typer.Exit(code=1)
    return engine


def _notifier_or_none(settings: Settings, client: httpx.Client) -> Notifier | None:
    try:
        return ntfy_notifier_from_settings(settings, client)
    except ValueError as error:
        message = f"notifications skipped: {error}"
        logger.warning(message, extra={"job": "render_batch"})
        typer.echo(message)
        return None


def _print_plans(plans: list[RenderPlan]) -> None:
    if not plans:
        typer.echo("nothing to render")
    for number, plan in enumerate(plans, start=1):
        timeline = plan.timeline
        cuts = [segment.output_start_s for segment in timeline.segments[1:]]
        typer.echo(
            f"plan {number}: recipe={plan.recipe_id} snippet={plan.snippet_id} "
            f"targets={','.join(plan.targets)} duration={timeline.duration_s:.2f}s"
        )
        typer.echo("  cuts: " + " ".join(f"{cut:.2f}" for cut in cuts))
        typer.echo("  shots: " + " ".join(str(shot_id) for shot_id in timeline.shot_ids))


def _latest_render(session: Session) -> RenderRow:
    return session.scalars(select(RenderRow).order_by(RenderRow.id.desc()).limit(1)).one()
