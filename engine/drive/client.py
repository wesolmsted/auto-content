from pathlib import Path
from typing import Protocol

from engine.drive.models import DriveFile


class DriveClient(Protocol):
    def list_videos(self, folder_id: str) -> list[DriveFile]:
        """Video files (`mimeType contains 'video/'`) directly in `folder_id`, not trashed."""
        ...

    def download(self, file_id: str, dest: Path) -> Path:
        """Download to `dest` (resumable, retried with backoff) and return `dest`."""
        ...

    def move(self, file_id: str, to_folder_id: str) -> None:
        """Move the file out of its current parent folders into `to_folder_id`."""
        ...
