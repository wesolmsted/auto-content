from datetime import datetime
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
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
