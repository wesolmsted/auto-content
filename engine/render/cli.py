from typing import Annotated

import typer

from engine.cli_common import not_implemented

app = typer.Typer(help="Assemble and render videos.", no_args_is_help=True)


@app.command()
def batch(
    n: Annotated[int | None, typer.Option(help="Renders to make (default: cadence).")] = None,
    recipe: Annotated[str | None, typer.Option(help="Only this recipe id.")] = None,
    snippet: Annotated[int | None, typer.Option(help="Only this snippet id.")] = None,
    dry_run: Annotated[bool, typer.Option(help="Print timelines; render nothing.")] = False,
) -> None:
    """Render the day's batch and send a hand-off notification per finished render."""
    not_implemented()


@app.command()
def one(
    recipe: Annotated[str, typer.Option(help="Recipe id.")],
    snippet: Annotated[int, typer.Option(help="Snippet id.")],
) -> None:
    """Render a single recipe on a single snippet (dev loop)."""
    not_implemented()
