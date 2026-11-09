import typer

from engine.cli_common import not_implemented

app = typer.Typer(help="Score recipe variables (Phase 2).", no_args_is_help=True)
report_app = typer.Typer(help="Weekly report and shoot list (Phase 2).", no_args_is_help=True)


@app.command()
def update() -> None:
    """Recompute variable scores from new metrics and hand-off signals."""
    not_implemented()


@report_app.command()
def weekly() -> None:
    """Build the weekly report and shoot list."""
    not_implemented()
