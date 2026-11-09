from typing import NoReturn

import typer


def not_implemented() -> NoReturn:
    """Placeholder body for commands whose slice hasn't been built yet."""
    typer.echo("not implemented yet")
    raise typer.Exit(code=1)
