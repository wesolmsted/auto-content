"""The `ace` command. Each module owns its commands in `engine/<module>/cli.py`; this file only
mounts them and implements `init` and `status`."""

import shutil

import typer
from sqlalchemy import inspect

from engine.config import Settings, load_settings
from engine.db.migrate import upgrade_to_head
from engine.db.repository import table_counts
from engine.db.session import create_db_engine, make_session_factory
from engine.drive import cli as drive_cli
from engine.handoff import cli as handoff_cli
from engine.ingest import cli as ingest_cli
from engine.learn import cli as learn_cli
from engine.log import configure_logging
from engine.measure import cli as measure_cli
from engine.notify import cli as notify_cli
from engine.render import cli as render_cli
from engine.snippets import cli as snippets_cli
from engine.storage.media_store import LocalMediaStore

app = typer.Typer(help="Auto Content Engine.", no_args_is_help=True)

auth_app = typer.Typer(help="One-time authorization flows.", no_args_is_help=True)
auth_app.command("google")(drive_cli.auth_google)
auth_app.command("meta")(measure_cli.auth_meta)
auth_app.command("tiktok")(measure_cli.auth_tiktok)

app.add_typer(auth_app, name="auth")
app.add_typer(ingest_cli.app, name="ingest")
app.add_typer(snippets_cli.app, name="snippets")
app.add_typer(render_cli.app, name="render")
app.add_typer(notify_cli.app, name="notify")
app.command("serve")(handoff_cli.serve)
app.add_typer(measure_cli.app, name="measure")
app.add_typer(measure_cli.posts_app, name="posts")
app.add_typer(learn_cli.app, name="learn")
app.add_typer(learn_cli.report_app, name="report")


@app.callback()
def main() -> None:
    configure_logging(load_settings().log_level)


@app.command()
def init() -> None:
    """Create the data directories, apply database migrations and check for ffmpeg/ffprobe."""
    settings = load_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.secrets_dir.mkdir(parents=True, exist_ok=True)
    settings.secrets_dir.chmod(0o700)
    LocalMediaStore(settings.media_dir).ensure_dirs()
    typer.echo(f"data dir ready: {settings.data_dir.resolve()}")

    upgrade_to_head(settings.db_url)
    typer.echo("database migrated")

    missing_tools = [
        tool for tool in (settings.tools.ffmpeg, settings.tools.ffprobe) if not shutil.which(tool)
    ]
    if missing_tools:
        typer.echo(f"missing on PATH: {', '.join(missing_tools)}")
        raise typer.Exit(code=1)
    typer.echo("ffmpeg and ffprobe found")


@app.command()
def status() -> None:
    """Row counts per table and status."""
    settings = load_settings()
    if not _database_initialized(settings):
        typer.echo("no database yet: run `ace init` first")
        raise typer.Exit(code=1)
    engine = create_db_engine(settings.db_url)
    with make_session_factory(engine)() as session:
        counts = table_counts(session)
    engine.dispose()

    width = max(len(table) for table in counts)
    for table, by_status in counts.items():
        summary = " ".join(f"{key}={count}" for key, count in by_status.items()) or "empty"
        typer.echo(f"{table:<{width}}  {summary}")


def _database_initialized(settings: Settings) -> bool:
    if settings.database_url is None and not settings.db_path.exists():
        return False
    engine = create_db_engine(settings.db_url)
    initialized = inspect(engine).has_table("alembic_version")
    engine.dispose()
    return initialized
