from typing import Annotated

import typer

from engine.cli_common import not_implemented
from engine.db.enums import Platform

app = typer.Typer(help="Match posts to renders and pull metrics.", no_args_is_help=True)
posts_app = typer.Typer(help="Posts made by hand on the platforms.", no_args_is_help=True)


@app.command()
def due() -> None:
    """Match newly posted videos to renders, then pull due 24 h / 72 h metrics."""
    not_implemented()


@posts_app.command()
def link(
    render_id: Annotated[int, typer.Argument(help="Render id.")],
    platform: Annotated[Platform, typer.Argument(help="Platform the video was posted to.")],
    url: Annotated[str, typer.Argument(help="Post link (Share → Copy link).")],
) -> None:
    """Manually match a render to its post."""
    not_implemented()


def auth_meta() -> None:
    """One-time read-only Meta (Instagram) authorization."""
    not_implemented()


def auth_tiktok() -> None:
    """One-time read-only TikTok authorization."""
    not_implemented()
