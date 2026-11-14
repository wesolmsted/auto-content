import typer

from engine.cli_common import not_implemented

app = typer.Typer(help="Micro edits of finished reels (DESIGN.md §7.10).", no_args_is_help=True)


@app.command()
def ingest() -> None:
    """Record new `_micro` videos and their sidecars from the footage inbox."""
    not_implemented()


@app.command()
def render() -> None:
    """Render a micro variant for each of today's slots that doesn't have one."""
    not_implemented()


@app.command()
def notify() -> None:
    """Supersede expired micro renders, then notify the ones whose slot has come."""
    not_implemented()


@app.command()
def run() -> None:
    """Ingest, render and notify, in that order."""
    not_implemented()


@app.command("list")
def list_sources() -> None:
    """Micro sources and their render counts."""
    not_implemented()


@app.command()
def retire(source_id: int) -> None:
    """Stop using a micro source."""
    not_implemented()
