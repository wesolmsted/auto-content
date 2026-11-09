from typing import Annotated

import typer
import uvicorn

from engine.config import load_settings
from engine.db.session import create_db_engine, make_session_factory
from engine.handoff.app import create_app
from engine.storage.media_store import LocalMediaStore


def serve(
    host: Annotated[str | None, typer.Option(help="Bind address (default: handoff.host).")] = None,
    port: Annotated[int | None, typer.Option(help="Port (default: handoff.port).")] = None,
) -> None:
    """Run the hand-off web page."""
    settings = load_settings()
    if settings.handoff_token is None or not settings.handoff_token.get_secret_value():
        typer.echo("HANDOFF_TOKEN is not set: add a long random token to .env")
        raise typer.Exit(code=1)
    engine = create_db_engine(settings.db_url)
    app = create_app(settings, make_session_factory(engine), LocalMediaStore(settings.media_dir))
    try:
        uvicorn.run(app, host=host or settings.handoff.host, port=port or settings.handoff.port)
    finally:
        engine.dispose()
