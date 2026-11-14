from datetime import UTC, datetime
from typing import Annotated

import httpx
import typer

from engine.config import Settings, load_settings
from engine.db.enums import Platform
from engine.db.session import create_db_engine, make_session_factory, session_scope
from engine.measure.api import PlatformReadError
from engine.measure.auth import (
    META_SCOPES,
    StoredToken,
    exchange_meta_token,
    expiry_warning,
    load_token,
    save_token,
    token_path,
)
from engine.measure.job import LinkError, link_post, run_measure
from engine.measure.meta import MetaReader
from engine.measure.readers import PlatformReader

app = typer.Typer(help="Match posts to renders and pull metrics.", no_args_is_help=True)
posts_app = typer.Typer(help="Posts made by hand on the platforms.", no_args_is_help=True)


@app.command()
def due() -> None:
    """Match newly posted videos to renders, then pull due 24 h / 72 h metrics."""
    settings = load_settings()
    now = datetime.now(UTC)
    with httpx.Client(timeout=settings.http.timeout_s) as client:
        readers = make_readers(settings, client, now)
        engine = create_db_engine(settings.db_url)
        try:
            with session_scope(make_session_factory(engine)) as session:
                summary = run_measure(session, readers, settings, now)
        finally:
            engine.dispose()
    typer.echo(
        f"matched {summary.matched}, resolved {summary.resolved} manual link(s), "
        f"not posted {summary.not_posted}, metrics inserted {summary.metrics_inserted}"
    )
    for error in summary.errors:
        typer.echo(f"error: {error}")


def make_readers(settings: Settings, client: httpx.Client, now: datetime) -> list[PlatformReader]:
    """Readers for the platforms that have a token, printing why any platform is skipped."""
    reader = _meta_reader(settings, client, now)
    return [reader] if reader is not None else []


def _meta_reader(settings: Settings, client: httpx.Client, now: datetime) -> MetaReader | None:
    token = load_token(token_path(settings.secrets_dir, Platform.IG_TRIAL))
    if token is None:
        typer.echo("skipping ig_trial: no Meta token, run `ace auth meta`")
        return None
    if settings.meta_ig_user_id is None:
        typer.echo("skipping ig_trial: set META_IG_USER_ID")
        return None
    _warn_on_expiry(Platform.IG_TRIAL, token, settings, now)
    return MetaReader(
        client,
        settings.measure.meta_graph_url,
        settings.meta_ig_user_id,
        token.access_token,
        settings.http,
    )


def _warn_on_expiry(
    platform: Platform, token: StoredToken, settings: Settings, now: datetime
) -> None:
    warning = expiry_warning(platform, token, now, settings.measure.token_expiry_warn_days)
    if warning is not None:
        typer.echo(f"warning: {warning}")


@posts_app.command()
def link(
    render_id: Annotated[int, typer.Argument(help="Render id.")],
    platform: Annotated[Platform, typer.Argument(help="Platform the video was posted to.")],
    url: Annotated[str, typer.Argument(help="Post link (Share → Copy link).")],
) -> None:
    """Manually match a render to its post."""
    settings = load_settings()
    engine = create_db_engine(settings.db_url)
    try:
        with session_scope(make_session_factory(engine)) as session:
            post = link_post(session, render_id, platform, url)
            permalink = post.permalink
    except LinkError as error:
        typer.echo(f"error: {error}")
        raise typer.Exit(code=1) from None
    finally:
        engine.dispose()
    typer.echo(
        f"linked render {render_id} on {platform} to {permalink}; "
        "the next `ace measure due` fills in the posting time"
    )


def auth_meta() -> None:
    """One-time read-only Meta (Instagram) authorization."""
    settings = load_settings()
    if settings.meta_app_id is None or settings.meta_app_secret is None:
        typer.echo("set META_APP_ID and META_APP_SECRET first")
        raise typer.Exit(code=1)
    typer.echo(
        "In the Graph API Explorer, pick your app and generate a user token with only these "
        f"permissions: {', '.join(META_SCOPES)}"
    )
    short_lived = typer.prompt("Paste the short-lived user token", hide_input=True)
    now = datetime.now(UTC)
    with httpx.Client(timeout=settings.http.timeout_s) as client:
        try:
            token = exchange_meta_token(
                client,
                settings.measure.meta_graph_url,
                settings.meta_app_id,
                settings.meta_app_secret.get_secret_value(),
                short_lived.strip(),
                settings.http,
                now,
            )
        except PlatformReadError as error:
            typer.echo(f"token exchange failed: {error}")
            raise typer.Exit(code=1) from None
    path = token_path(settings.secrets_dir, Platform.IG_TRIAL)
    save_token(token, path)
    expires = token.expires_at.date().isoformat() if token.expires_at else "unknown"
    typer.echo(f"Meta token saved to {path} (expires {expires})")
    if settings.meta_ig_user_id is None:
        typer.echo("also set META_IG_USER_ID (your Instagram professional account id)")
