import typer

from engine.cli_common import not_implemented

app = typer.Typer(help="Pull new files from the Google Drive inboxes.", no_args_is_help=True)


@app.command()
def footage() -> None:
    """Poll the footage inbox, download, split into shots, tag, and move to Processed."""
    not_implemented()


@app.command()
def references() -> None:
    """Analyze new style reference videos (Phase 3)."""
    not_implemented()


@app.command()
def exports() -> None:
    """Import TikTok Studio analytics exports (Phase 2)."""
    not_implemented()
