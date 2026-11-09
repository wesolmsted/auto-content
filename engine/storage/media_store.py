"""Where media files live (DESIGN.md §6). The only code that decides media file locations."""

from enum import StrEnum
from pathlib import Path
from typing import Protocol


class MediaKind(StrEnum):
    SCRATCH = "scratch"
    SHOTS = "shots"
    RENDERS = "renders"


class MediaStore(Protocol):
    def path_for(self, kind: MediaKind, name: str) -> Path:
        """An absolute path for file `name` of `kind`; its directory exists, the file may not."""
        ...


class LocalMediaStore:
    """Media on local disk under `media_dir/<kind>/` (normally `Settings.media_dir`)."""

    def __init__(self, media_dir: Path) -> None:
        self.media_dir = media_dir

    def path_for(self, kind: MediaKind, name: str) -> Path:
        if name in {"", ".", ".."} or Path(name).name != name:
            raise ValueError(f"media name must be a plain file name, got {name!r}")
        directory = self.media_dir.resolve() / kind
        directory.mkdir(parents=True, exist_ok=True)
        return directory / name

    def ensure_dirs(self) -> None:
        for kind in MediaKind:
            (self.media_dir / kind).mkdir(parents=True, exist_ok=True)
