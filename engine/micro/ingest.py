from sqlalchemy.orm import Session

from engine.config import DriveSettings, MicroSettings
from engine.drive.client import DriveClient
from engine.micro.models import MicroIngestSummary
from engine.storage.media_store import MediaStore


def poll_micro_sets(
    session: Session,
    drive: DriveClient,
    store: MediaStore,
    drive_settings: DriveSettings,
    micro_settings: MicroSettings,
    *,
    ffprobe: str = "ffprobe",
) -> MicroIngestSummary:
    """`ace micro ingest` (DESIGN.md §7.10): sync the `_micro` folders in the footage inbox
    with `micro_sets` and `micro_sources`, re-reading changed sidecars and downloading and
    probing new videos. Moves nothing in Drive. Idempotent; commits after each state change."""
    raise NotImplementedError
