from pathlib import Path

from google.oauth2.credentials import Credentials

from engine.config import DriveSettings
from engine.drive.models import DriveFile


class GoogleDriveClient:
    """`DriveClient` backed by the Google Drive v3 API with the owner's OAuth credentials."""

    def __init__(self, credentials: Credentials, settings: DriveSettings) -> None:
        self.credentials = credentials
        self.settings = settings

    def list_videos(self, folder_id: str) -> list[DriveFile]:
        raise NotImplementedError

    def download(self, file_id: str, dest: Path) -> Path:
        raise NotImplementedError

    def move(self, file_id: str, to_folder_id: str) -> None:
        raise NotImplementedError
