import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
import typer

from engine.config import Settings, load_settings
from engine.db.enums import Platform
from engine.db.session import create_db_engine, make_session_factory, session_scope
from engine.measure.api import PlatformReadError
from engine.measure.auth import (
    META_SCOPES,
    AuthError,
    StoredToken,
    code_from_redirect,
    exchange_meta_token,
    exchange_tiktok_code,
    expiry_warning,
    load_token,
    new_pkce_verifier,
    pkce_challenge,
    refresh_tiktok_token,
    save_token,
    tiktok_authorize_url,
    token_path,
)
from engine.measure.job import LinkError, link_post, run_measure
from engine.measure.meta import MetaReader
from engine.measure.readers import PlatformReader
from engine.measure.tiktok import TikTokReader

DEFAULT_TIKTOK_REDIRECT_URI = "http://localhost:8765/callback/"
TIKTOK_REFRESH_MARGIN = timedelta(minutes=5)

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
    readers: list[PlatformReader] = []
    for make in (_meta_reader, _tiktok_reader):
        reader = make(settings, client, now)
        if reader is not None:
            readers.append(reader)
    return readers


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


def _tiktok_reader(settings: Settings, client: httpx.Client, now: datetime) -> TikTokReader | None:
    path = token_path(settings.secrets_dir, Platform.TIKTOK)
    token = load_token(path)
    if token is None:
        typer.echo("skipping tiktok: no TikTok token, run `ace auth tiktok`")
        return None
    if token.expires_at is not None and token.expires_at - now <= TIKTOK_REFRESH_MARGIN:
        if settings.tiktok_client_key is None or settings.tiktok_client_secret is None:
            typer.echo("skipping tiktok: set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET")
            return None
        try:
            token = refresh_tiktok_token(
                client,
                settings.measure.tiktok_api_url,
                settings.tiktok_client_key,
                settings.tiktok_client_secret.get_secret_value(),
                token,
                settings.http,
                now,
            )
        except (AuthError, PlatformReadError) as error:
            typer.echo(f"skipping tiktok: token refresh failed: {error}")
            return None
        save_token(token, path)
    _warn_on_expiry(Platform.TIKTOK, token, settings, now)
    return TikTokReader(client, settings.measure.tiktok_api_url, token.access_token, settings.http)


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


def auth_tiktok(
    redirect_uri: Annotated[
        str, typer.Option(help="Redirect URI registered in the TikTok app's Login Kit settings.")
    ] = DEFAULT_TIKTOK_REDIRECT_URI,
) -> None:
    """One-time read-only TikTok authorization."""
    settings = load_settings()
    if settings.tiktok_client_key is None or settings.tiktok_client_secret is None:
        typer.echo("set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET first")
        raise typer.Exit(code=1)
    state = secrets.token_urlsafe(16)
    verifier = new_pkce_verifier()
    url = tiktok_authorize_url(
        settings.tiktok_client_key, redirect_uri, state, pkce_challenge(verifier)
    )
    typer.echo(f"Open this link, log in and allow access:\n{url}")
    redirected = typer.prompt("Paste the full URL the browser was sent to")
    now = datetime.now(UTC)
    with httpx.Client(timeout=settings.http.timeout_s) as client:
        try:
            token = exchange_tiktok_code(
                client,
                settings.measure.tiktok_api_url,
                settings.tiktok_client_key,
                settings.tiktok_client_secret.get_secret_value(),
                code_from_redirect(redirected.strip(), state),
                redirect_uri,
                verifier,
                settings.http,
                now,
            )
        except (AuthError, PlatformReadError) as error:
            typer.echo(f"TikTok authorization failed: {error}")
            raise typer.Exit(code=1) from None
    path = token_path(settings.secrets_dir, Platform.TIKTOK)
    save_token(token, path)
    renew_by = token.reauthorize_by.date().isoformat() if token.reauthorize_by else "unknown"
    typer.echo(f"TikTok token saved to {path} (re-authorize by {renew_by})")
