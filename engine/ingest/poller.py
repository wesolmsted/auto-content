import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import DriveSettings
from engine.db.enums import FootageStatus
from engine.db.models import FootageFileRow
from engine.drive.client import DriveClient
from engine.drive.models import DriveFile
from engine.ingest.models import PollSummary
from engine.storage.media_store import MediaKind, MediaStore

JOB_NAME = "ingest_footage"

logger = logging.getLogger(__name__)


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

    Commits after every state change, so no write transaction stays open during a Drive call
    and a crash keeps the progress of finished files.
    """
    inbox_folder_id, processed_folder_id = footage_folders(settings)
    completed, failed_moves = complete_tagged_footage(
        session, drive, processed_folder_id, max_attempts
    )
    listed = drive.list_videos(inbox_folder_id)
    new, duplicates = _record_listing(session, drive, listed, processed_folder_id)
    downloaded, failed_downloads = _download_new(session, drive, store, max_attempts)
    return PollSummary(
        listed=len(listed),
        new=new,
        duplicates=duplicates,
        downloaded=downloaded,
        completed=completed,
        failed=failed_moves + failed_downloads,
    )


def footage_folders(settings: DriveSettings) -> tuple[str, str]:
    """The footage inbox and processed folder ids; raises ValueError if either is unset."""
    inbox, processed = settings.footage_inbox_folder_id, settings.footage_processed_folder_id
    if not inbox:
        raise ValueError("set drive.footage_inbox_folder_id in config/local.toml")
    if not processed:
        raise ValueError("set drive.footage_processed_folder_id in config/local.toml")
    return inbox, processed


def complete_tagged_footage(
    session: Session, drive: DriveClient, processed_folder_id: str, max_attempts: int
) -> tuple[int, int]:
    """Move every `tagged` file to processed, mark it `done` and delete its local download.

    Commits after each file. Returns (completed, newly failed).
    """
    completed = failed = 0
    for row in _rows_with_status(session, FootageStatus.TAGGED):
        try:
            drive.move(row.drive_file_id, processed_folder_id)
        except Exception as error:
            failed += record_failure(row, error, max_attempts)
        else:
            if row.local_path:
                Path(row.local_path).unlink(missing_ok=True)
            row.local_path = None
            advance(row, FootageStatus.DONE)
            completed += 1
        session.commit()
    return completed, failed


def advance(row: FootageFileRow, status: FootageStatus) -> None:
    """Move a row to its next state; `attempts` counts tries at the current step only."""
    row.status = status
    row.error = None
    row.attempts = 0


def record_failure(row: FootageFileRow, error: Exception, max_attempts: int) -> bool:
    """Record a failed step on the row; returns True if the row is now `failed`."""
    logger.warning(
        "footage file step failed: %s",
        error,
        exc_info=error,
        extra={"job": JOB_NAME, "row_id": row.id},
    )
    row.attempts += 1
    row.error = f"{type(error).__name__}: {error}"
    if row.attempts >= max_attempts:
        row.status = FootageStatus.FAILED
        return True
    return False


def _record_listing(
    session: Session, drive: DriveClient, listed: list[DriveFile], processed_folder_id: str
) -> tuple[int, int]:
    new = duplicates = 0
    for drive_file in listed:
        row = session.scalar(
            select(FootageFileRow).where(FootageFileRow.drive_file_id == drive_file.id)
        )
        if row is None:
            row = _insert(session, drive_file)
            session.commit()
            if row.status == FootageStatus.DUPLICATE:
                duplicates += 1
            else:
                new += 1
        if row.status == FootageStatus.DUPLICATE:
            _move_duplicate(drive, row, processed_folder_id)
            session.commit()
    return new, duplicates


def _insert(session: Session, drive_file: DriveFile) -> FootageFileRow:
    is_duplicate = bool(drive_file.md5) and (
        session.scalar(
            select(FootageFileRow.id).where(FootageFileRow.md5 == drive_file.md5).limit(1)
        )
        is not None
    )
    row = FootageFileRow(
        drive_file_id=drive_file.id,
        md5=drive_file.md5,
        name=drive_file.name,
        size_bytes=drive_file.size_bytes,
        status=FootageStatus.DUPLICATE if is_duplicate else FootageStatus.NEW,
    )
    session.add(row)
    session.flush()
    return row


def _move_duplicate(drive: DriveClient, row: FootageFileRow, processed_folder_id: str) -> None:
    """Duplicates still in the inbox are moved again on every run until the move succeeds."""
    try:
        drive.move(row.drive_file_id, processed_folder_id)
    except Exception as error:
        logger.warning(
            "moving duplicate failed: %s", error, extra={"job": JOB_NAME, "row_id": row.id}
        )
        row.error = f"{type(error).__name__}: {error}"
    else:
        row.error = None


def _download_new(
    session: Session, drive: DriveClient, store: MediaStore, max_attempts: int
) -> tuple[int, int]:
    downloaded = failed = 0
    for row in _rows_with_status(session, FootageStatus.NEW):
        dest = store.path_for(MediaKind.SCRATCH, f"footage_{row.id}{Path(row.name).suffix.lower()}")
        try:
            drive.download(row.drive_file_id, dest)
        except Exception as error:
            failed += record_failure(row, error, max_attempts)
        else:
            row.local_path = str(dest)
            advance(row, FootageStatus.DOWNLOADED)
            downloaded += 1
        session.commit()
    return downloaded, failed


def _rows_with_status(session: Session, status: FootageStatus) -> list[FootageFileRow]:
    query = select(FootageFileRow).where(FootageFileRow.status == status)
    return list(session.scalars(query.order_by(FootageFileRow.id)))
