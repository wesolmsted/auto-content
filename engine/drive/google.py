import os
from datetime import datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from engine.config import DriveSettings
from engine.drive.models import DriveFile

LIST_FIELDS = (
    "nextPageToken, files(id, name, mimeType, md5Checksum, size, createdTime, description)"
)
PAGE_SIZE = 100


class GoogleDriveClient:
    """`DriveClient` backed by the Google Drive v3 API with the owner's OAuth credentials.

    Every request is retried `settings.max_retries` times on 5xx/429 with the library's
    randomized exponential backoff. `service` replaces the built API resource in tests.
    """

    def __init__(
        self, credentials: Credentials, settings: DriveSettings, *, service: Any = None
    ) -> None:
        self.credentials = credentials
        self.settings = settings
        self.service = service or build(
            "drive", "v3", credentials=credentials, cache_discovery=False
        )

    def list_videos(self, folder_id: str) -> list[DriveFile]:
        query = f"'{folder_id}' in parents and mimeType contains 'video/' and trashed = false"
        videos: list[DriveFile] = []
        page_token: str | None = None
        while True:
            response = self._execute(
                self.service.files().list(
                    q=query, fields=LIST_FIELDS, pageSize=PAGE_SIZE, pageToken=page_token
                )
            )
            videos.extend(_to_drive_file(item) for item in response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return videos

    def download(self, file_id: str, dest: Path) -> Path:
        partial = dest.with_name(dest.name + ".part")
        request = self.service.files().get_media(fileId=file_id)
        try:
            with partial.open("wb") as file:
                downloader = MediaIoBaseDownload(
                    file, request, chunksize=self.settings.download_chunk_mb * 1024 * 1024
                )
                done = False
                while not done:
                    _, done = downloader.next_chunk(num_retries=self.settings.max_retries)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        partial.replace(dest)
        return dest

    def move(self, file_id: str, to_folder_id: str) -> None:
        current = self._execute(self.service.files().get(fileId=file_id, fields="parents"))
        self._execute(
            self.service.files().update(
                fileId=file_id,
                addParents=to_folder_id,
                removeParents=",".join(current.get("parents", [])),
                fields="id, parents",
            )
        )

    def _execute(self, request: Any) -> dict[str, Any]:
        response: dict[str, Any] = request.execute(num_retries=self.settings.max_retries)
        return response


def _to_drive_file(item: dict[str, Any]) -> DriveFile:
    created = item.get("createdTime")
    size = item.get("size")
    return DriveFile(
        id=item["id"],
        name=item["name"],
        mime_type=item["mimeType"],
        md5=item.get("md5Checksum"),
        size_bytes=int(size) if size is not None else None,
        created_at=datetime.fromisoformat(created) if created else None,
        description=item.get("description"),
    )


DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
TOKEN_FILE_NAME = "google_token.json"


class GoogleAuthError(Exception):
    """No usable Google credentials: the owner has to run `ace auth google`."""


def token_path(secrets_dir: Path) -> Path:
    return secrets_dir / TOKEN_FILE_NAME


def run_oauth_flow(client_json: Path) -> Credentials:
    """Installed-app consent in the browser; returns credentials with a refresh token."""
    flow = InstalledAppFlow.from_client_secrets_file(str(client_json), DRIVE_SCOPES)
    credentials: Credentials = flow.run_local_server(port=0)
    return credentials


def save_credentials(credentials: Credentials, path: Path) -> None:
    """Write the credentials JSON (including the refresh token) readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(credentials.to_json())  # type: ignore[no-untyped-call]
    path.chmod(0o600)


def load_credentials(path: Path, request: Any = None) -> Credentials:
    """Load saved credentials, refreshing (and re-saving) them if the access token expired.

    `request` is the HTTP transport for the refresh; tests pass a fake.
    """
    if not path.exists():
        raise GoogleAuthError(f"no Google token at {path}: run `ace auth google` first")
    credentials: Credentials = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
        str(path), DRIVE_SCOPES
    )
    if not credentials.valid:
        if not credentials.refresh_token:
            raise GoogleAuthError("Google token has no refresh token: run `ace auth google`")
        credentials.refresh(request or Request())  # type: ignore[no-untyped-call]
        save_credentials(credentials, path)
    return credentials
