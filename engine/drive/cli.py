import typer

from engine.config import load_settings
from engine.drive.google import run_oauth_flow, save_credentials, token_path


def auth_google() -> None:
    """One-time Google Drive OAuth consent; stores the refresh token in the secrets dir."""
    settings = load_settings()
    if settings.google_oauth_client_json is None:
        typer.echo("set GOOGLE_OAUTH_CLIENT_JSON to the path of your OAuth client JSON file")
        raise typer.Exit(code=1)
    credentials = run_oauth_flow(settings.google_oauth_client_json)
    path = token_path(settings.secrets_dir)
    save_credentials(credentials, path)
    typer.echo(f"Google token saved to {path}")
