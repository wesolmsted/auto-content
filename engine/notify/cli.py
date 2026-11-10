from datetime import UTC, datetime

import httpx
import typer

from engine.config import Settings, load_settings
from engine.db.session import create_db_engine, make_session_factory, session_scope
from engine.handoff.expiry import supersede_expired
from engine.notify.base import NotificationError
from engine.notify.handoff import send_pending_handoff_notifications
from engine.notify.models import Notification
from engine.notify.ntfy import NtfyNotifier, ntfy_notifier_from_settings

app = typer.Typer(help="Phone notifications.", no_args_is_help=True)


@app.command()
def test() -> None:
    """Send a test notification to the phone."""
    settings = load_settings()
    notification = Notification(
        title="Auto Content Engine test",
        body="Notifications are working.",
        click_url=settings.handoff_base_url,
        tags=("white_check_mark",),
    )
    with httpx.Client(timeout=settings.notify.timeout_s) as client:
        notifier = _notifier_or_exit(settings, client)
        try:
            notifier.send(notification)
        except NotificationError as error:
            typer.echo(f"notification failed: {error}")
            raise typer.Exit(code=1) from None
    typer.echo("test notification sent")


@app.command()
def pending() -> None:
    """Supersede expired renders, then send pending hand-off notifications (held in quiet hours)."""
    settings = load_settings()
    with httpx.Client(timeout=settings.notify.timeout_s) as client:
        notifier = _notifier_or_exit(settings, client)
        engine = create_db_engine(settings.db_url)
        try:
            with session_scope(make_session_factory(engine)) as session:
                now = datetime.now(UTC)
                superseded = supersede_expired(session, settings, now)
                sent = send_pending_handoff_notifications(session, notifier, settings, now)
        except ValueError as error:
            typer.echo(str(error))
            raise typer.Exit(code=1) from None
        finally:
            engine.dispose()
    typer.echo(f"superseded {superseded} expired render(s)")
    typer.echo(f"sent {sent} hand-off notification(s)")


def _notifier_or_exit(settings: Settings, client: httpx.Client) -> NtfyNotifier:
    try:
        return ntfy_notifier_from_settings(settings, client)
    except ValueError as error:
        typer.echo(str(error))
        raise typer.Exit(code=1) from None
