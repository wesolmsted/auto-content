from pathlib import Path
from typing import Annotated

import typer

from engine.cli_common import not_implemented
from engine.db.enums import SnippetType

app = typer.Typer(help="Songs and the snippets cut from them.", no_args_is_help=True)


@app.command()
def add(
    audio: Annotated[Path, typer.Argument(help="Song audio file.")],
    start: Annotated[float, typer.Option(help="Snippet start in the song, seconds.")],
    end: Annotated[float, typer.Option(help="Snippet end in the song, seconds.")],
    snippet_type: Annotated[SnippetType, typer.Option("--type", help="Snippet type.")],
    drop: Annotated[float | None, typer.Option(help="Override the detected drop time.")] = None,
    label: Annotated[str | None, typer.Option(help="Short label for the snippet.")] = None,
) -> None:
    """Analyze a song (once) and add a snippet of it."""
    not_implemented()


@app.command("list")
def list_snippets() -> None:
    """List songs and their snippets."""
    not_implemented()
