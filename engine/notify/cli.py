import typer

from engine.cli_common import not_implemented

app = typer.Typer(help="Phone notifications.", no_args_is_help=True)


@app.command()
def test() -> None:
    """Send a test notification to the phone."""
    not_implemented()
