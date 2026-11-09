import anthropic
import typer
from sqlalchemy import inspect

from engine.cli_common import not_implemented
from engine.config import Settings, load_settings
from engine.db.session import create_db_engine, make_session_factory
from engine.drive.client import DriveClient
from engine.drive.google import GoogleAuthError, GoogleDriveClient, load_credentials, token_path
from engine.ingest.job import run_footage_ingest
from engine.ingest.poller import footage_folders
from engine.storage.media_store import LocalMediaStore
from engine.vision.claude import ClaudeVisionTagger
from engine.vision.tagger import VisionTagger

app = typer.Typer(help="Pull new files from the Google Drive inboxes.", no_args_is_help=True)


def make_drive_client(settings: Settings) -> DriveClient:
    credentials = load_credentials(token_path(settings.secrets_dir))
    return GoogleDriveClient(credentials, settings.drive)


def make_tagger(settings: Settings) -> VisionTagger:
    if settings.anthropic_api_key is None:
        raise ValueError("set ANTHROPIC_API_KEY for vision tagging")
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.vision.timeout_s,
        max_retries=settings.vision.max_retries,
    )
    return ClaudeVisionTagger(client, settings.vision)


@app.command()
def footage() -> None:
    """Poll the footage inbox, download, split into shots, tag, and move to Processed."""
    settings = load_settings()
    if settings.database_url is None and not settings.db_path.exists():
        typer.echo("no database yet: run `ace init` first")
        raise typer.Exit(code=1)
    engine = create_db_engine(settings.db_url)
    try:
        if not inspect(engine).has_table("footage_files"):
            typer.echo("database not migrated: run `ace init` first")
            raise typer.Exit(code=1)
        try:
            footage_folders(settings.drive)
            drive = make_drive_client(settings)
            tagger = make_tagger(settings)
        except (ValueError, GoogleAuthError) as error:
            typer.echo(str(error))
            raise typer.Exit(code=1) from error

        with make_session_factory(engine)() as session:
            summary = run_footage_ingest(
                session,
                drive,
                LocalMediaStore(settings.media_dir),
                tagger,
                settings.drive,
                settings.ingest,
            )
    finally:
        engine.dispose()

    poll = summary.poll
    typer.echo(
        f"listed={poll.listed} new={poll.new} duplicates={poll.duplicates} "
        f"downloaded={poll.downloaded} built={summary.built} shots={summary.shots} "
        f"done={poll.completed + summary.completed} failed={poll.failed + summary.failed}"
    )


@app.command()
def references() -> None:
    """Analyze new style reference videos (Phase 3)."""
    not_implemented()


@app.command()
def exports() -> None:
    """Import TikTok Studio analytics exports (Phase 2)."""
    not_implemented()
