from sqlalchemy.orm import Session

from engine.config import DriveSettings, MicroSettings
from engine.drive.client import DriveClient
from engine.micro.models import MicroIngestSummary
from engine.storage.media_store import MediaStore


def poll_micro_sources(
    session: Session,
    drive: DriveClient,
    store: MediaStore,
    drive_settings: DriveSettings,
    micro_settings: MicroSettings,
    *,
    ffprobe: str = "ffprobe",
) -> MicroIngestSummary:
    """`ace micro ingest` (DESIGN.md §7.10): record new `_micro` videos in the footage inbox,
    read their sidecars, download and probe them, and move both files to processed. Idempotent;
    commits after each state change."""
    raise NotImplementedError
