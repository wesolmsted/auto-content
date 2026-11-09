from sqlalchemy.orm import Session

from engine.config import DriveSettings
from engine.drive.client import DriveClient
from engine.ingest.models import PollSummary
from engine.storage.media_store import MediaStore


def poll_footage(
    session: Session,
    drive: DriveClient,
    store: MediaStore,
    settings: DriveSettings,
    max_attempts: int,
) -> PollSummary:
    """The Drive side of `ace ingest footage` (DESIGN.md §7.1 steps 1, 2 and 7). Idempotent.

    1. Moves `tagged` footage files to the processed folder, marks them `done` and deletes
       their local download.
    2. Lists the inbox folder and inserts unseen `drive_file_id`s as `new`. A file whose md5
       matches an existing row is marked `duplicate` and moved to processed unprocessed.
    3. Downloads `new` files into `MediaKind.SCRATCH`, one at a time, setting `local_path` and
       status `downloaded`. Failures increment `attempts` and record `error`; a row reaching
       `max_attempts` becomes `failed`.

    The caller commits the session.
    """
    raise NotImplementedError
