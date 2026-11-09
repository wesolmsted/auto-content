from typing import Annotated

import typer

from engine.cli_common import not_implemented


def serve(
    host: Annotated[str | None, typer.Option(help="Bind address (default: handoff.host).")] = None,
    port: Annotated[int | None, typer.Option(help="Port (default: handoff.port).")] = None,
) -> None:
    """Run the hand-off web page."""
    not_implemented()
