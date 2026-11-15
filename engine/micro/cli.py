import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import typer
from sqlalchemy import Engine, func, inspect, select
from sqlalchemy.orm import Session

from engine.config import Settings, load_settings
from engine.db.models import MicroRenderRow, MicroSetRow, MicroSourceRow
from engine.db.session import create_db_engine, make_session_factory, session_scope
from engine.drive.google import GoogleAuthError
from engine.ingest.cli import make_drive_client
from engine.micro.edits import output_duration_s, sample_edit
from engine.micro.ingest import poll_micro_sets
from engine.micro.job import run_micro_renders
from engine.micro.notify import send_due_micro_notifications, supersede_expired_micro
from engine.micro.render import render_micro
from engine.notify.ntfy import ntfy_notifier_from_settings
from engine.storage.media_store import LocalMediaStore

app = typer.Typer(help="Micro edits of finished reels (DESIGN.md §7.10).", no_args_is_help=True)

logger = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(UTC)


@app.command()
def ingest() -> None:
    """Sync the `_micro` folders in the footage inbox: sets, texts and videos."""
    _ingest(load_settings())


@app.command()
def render() -> None:
    """Render a micro variant for each of today's slots that doesn't have one.

    Exits 1 if any render failed.
    """
    _render(load_settings())


@app.command()
def notify() -> None:
    """Supersede expired micro renders, then notify the ones whose slot has come."""
    _notify(load_settings())


@app.command()
def run() -> None:
    """Ingest, render and notify, in that order. A failed step doesn't stop the next ones;
    exits 1 at the end if any step failed."""
    settings = load_settings()
    steps: list[tuple[str, Callable[[Settings], None]]] = [
        ("ingest", _ingest),
        ("render", _render),
        ("notify", _notify),
    ]
    any_failed = False
    for name, step in steps:
        try:
            step(settings)
        except typer.Exit as exit_:
            if exit_.exit_code != 0:
                typer.echo(f"micro {name} failed")
                any_failed = True
        except Exception as error:
            logger.exception("micro %s failed", name, extra={"job": "micro_run"})
            typer.echo(f"micro {name} failed: {type(error).__name__}: {error}")
            any_failed = True
    if any_failed:
        raise typer.Exit(code=1)


@app.command("list")
def list_sources() -> None:
    """Micro sets, their videos and render counts."""
    engine = _open_database(load_settings())
    try:
        with make_session_factory(engine)() as session:
            lines = _set_lines(session)
    finally:
        engine.dispose()
    typer.echo("\n".join(lines) if lines else "no micro sets")


def _ingest(settings: Settings) -> None:
    if not settings.drive.footage_inbox_folder_id:
        typer.echo("set drive.footage_inbox_folder_id in config/local.toml")
        raise typer.Exit(code=1)
    engine = _open_database(settings)
    try:
        try:
            drive = make_drive_client(settings)
        except (ValueError, GoogleAuthError) as error:
            typer.echo(str(error))
            raise typer.Exit(code=1) from error
        with make_session_factory(engine)() as session:
            summary = poll_micro_sets(
                session,
                drive,
                LocalMediaStore(settings.media_dir),
                settings.drive,
                settings.micro,
                ffprobe=settings.tools.ffprobe,
            )
    finally:
        engine.dispose()
    typer.echo(" ".join(f"{name}={count}" for name, count in summary.model_dump().items()))


def _render(settings: Settings) -> None:
    engine = _open_database(settings)
    try:
        with make_session_factory(engine)() as session:
            summary = run_micro_renders(
                session,
                LocalMediaStore(settings.media_dir),
                settings,
                now_utc(),
                random.Random(),
                renderer=render_micro,
                edit_sampler=sample_edit,
                duration_of=output_duration_s,
            )
    finally:
        engine.dispose()
    typer.echo(
        f"slots={summary.slots} ready={summary.ready} "
        f"failed={summary.failed} no_source={summary.no_source}"
    )
    if summary.failed:
        raise typer.Exit(code=1)


def _notify(settings: Settings) -> None:
    with httpx.Client(timeout=settings.notify.timeout_s) as client:
        try:
            notifier = ntfy_notifier_from_settings(settings, client)
        except ValueError as error:
            typer.echo(str(error))
            raise typer.Exit(code=1) from None
        engine = _open_database(settings)
        try:
            with session_scope(make_session_factory(engine)) as session:
                now = now_utc()
                superseded = supersede_expired_micro(session, settings, now)
                sent = send_due_micro_notifications(session, notifier, settings, now)
        except ValueError as error:
            typer.echo(str(error))
            raise typer.Exit(code=1) from None
        finally:
            engine.dispose()
    typer.echo(f"superseded {superseded} expired micro render(s)")
    typer.echo(f"sent {sent} micro notification(s)")


def _open_database(settings: Settings) -> Engine:
    if settings.database_url is None and not settings.db_path.exists():
        typer.echo("no database yet: run `ace init` first")
        raise typer.Exit(code=1)
    engine = create_db_engine(settings.db_url)
    if not inspect(engine).has_table("micro_sets"):
        engine.dispose()
        typer.echo("database not migrated: run `ace init` first")
        raise typer.Exit(code=1)
    return engine


def _set_lines(session: Session) -> list[str]:
    render_counts = {
        source_id: count
        for source_id, count in session.execute(
            select(MicroRenderRow.source_id, func.count()).group_by(MicroRenderRow.source_id)
        )
    }
    lines = []
    for micro_set in session.scalars(select(MicroSetRow).order_by(MicroSetRow.id)):
        texts = micro_set.text_options_json
        song = f'"{micro_set.song_title}"' if micro_set.song_title else "-"
        next_text = f'"{texts[micro_set.next_text_index % len(texts)]}"' if texts else "-"
        line = (
            f"set {micro_set.id} {micro_set.name} status={micro_set.status} song={song} "
            f"texts={len(texts)} next_text={next_text}"
        )
        if micro_set.error:
            line += f' error="{micro_set.error}"'
        lines.append(line)
        videos = session.scalars(
            select(MicroSourceRow)
            .where(MicroSourceRow.set_id == micro_set.id)
            .order_by(MicroSourceRow.id)
        ).all()
        lines.extend(
            f"  video {video.id} {video.name} status={video.status} "
            f"renders={render_counts.get(video.id, 0)}"
            for video in videos
        )
        if not videos:
            lines.append("  no videos")
    return lines
