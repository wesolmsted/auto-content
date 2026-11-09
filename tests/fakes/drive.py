"""In-memory `DriveClient` for tests."""

import hashlib
from pathlib import Path

from engine.drive.models import DriveFile

COMPUTED_MD5 = "<md5 of content>"


class FakeDriveError(Exception):
    pass


class FakeDriveClient:
    """Folders of files held in memory. Downloads write the file's bytes to `dest`.

    `fail_downloads[file_id] = n` makes the next n downloads of that file raise FakeDriveError;
    `fail_moves` does the same for moves. `downloads` and `moves` record successful calls.
    """

    def __init__(self) -> None:
        self.folders: dict[str, list[DriveFile]] = {}
        self.contents: dict[str, bytes] = {}
        self.fail_downloads: dict[str, int] = {}
        self.fail_moves: dict[str, int] = {}
        self.downloads: list[str] = []
        self.moves: list[tuple[str, str]] = []

    def add_file(
        self,
        folder_id: str,
        file_id: str,
        content: bytes = b"video bytes",
        *,
        name: str | None = None,
        mime_type: str = "video/mp4",
        md5: str | None = COMPUTED_MD5,
    ) -> DriveFile:
        """Add a file; `md5` defaults to the real md5 of `content` (pass None for no md5)."""
        drive_file = DriveFile(
            id=file_id,
            name=name or f"{file_id}.mp4",
            mime_type=mime_type,
            md5=hashlib.md5(content).hexdigest() if md5 is COMPUTED_MD5 else md5,
            size_bytes=len(content),
        )
        self.folders.setdefault(folder_id, []).append(drive_file)
        self.contents[file_id] = content
        return drive_file

    def file_ids_in(self, folder_id: str) -> list[str]:
        return [drive_file.id for drive_file in self.folders.get(folder_id, [])]

    def list_videos(self, folder_id: str) -> list[DriveFile]:
        return [
            drive_file
            for drive_file in self.folders.get(folder_id, [])
            if drive_file.mime_type.startswith("video/")
        ]

    def download(self, file_id: str, dest: Path) -> Path:
        if self._take_failure(self.fail_downloads, file_id):
            raise FakeDriveError(f"download of {file_id} failed")
        dest.write_bytes(self.contents[file_id])
        self.downloads.append(file_id)
        return dest

    def move(self, file_id: str, to_folder_id: str) -> None:
        if self._take_failure(self.fail_moves, file_id):
            raise FakeDriveError(f"move of {file_id} failed")
        drive_file = self._pop(file_id)
        self.folders.setdefault(to_folder_id, []).append(drive_file)
        self.moves.append((file_id, to_folder_id))

    def _pop(self, file_id: str) -> DriveFile:
        for files in self.folders.values():
            for drive_file in files:
                if drive_file.id == file_id:
                    files.remove(drive_file)
                    return drive_file
        raise FakeDriveError(f"no such file: {file_id}")

    @staticmethod
    def _take_failure(failures: dict[str, int], file_id: str) -> bool:
        if failures.get(file_id, 0) > 0:
            failures[file_id] -= 1
            return True
        return False
