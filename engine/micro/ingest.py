import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import DriveSettings, MicroSettings
from engine.db.enums import MicroSetStatus, MicroSourceStatus
from engine.db.models import MicroSetRow, MicroSourceRow
from engine.drive.client import DriveClient
from engine.drive.models import DriveFile
from engine.ffmpeg import probe
from engine.ingest.poller import footage_folders
from engine.micro.models import MicroIngestSummary, Sidecar
from engine.micro.naming import SIDECAR_SUFFIX, is_micro_folder_name
from engine.micro.sidecar import SidecarError, parse_sidecar
from engine.storage.media_store import MediaKind, MediaStore

JOB_NAME = "micro_ingest"

logger = logging.getLogger(__name__)


@dataclass
class _Tally:
    sets: int = 0
    new_sets: int = 0
    waiting_sets: int = 0
    removed_sets: int = 0
    new_videos: int = 0
    ready_videos: int = 0
    duplicate_videos: int = 0
    removed_videos: int = 0
    failed_videos: int = 0


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
    inbox_folder_id, _ = footage_folders(drive_settings)
    folders = [
        folder
        for folder in drive.list_folders(inbox_folder_id)
        if is_micro_folder_name(folder.name)
    ]
    tally = _Tally(sets=len(folders))
    live_sets = _sync_sets(session, folders, tally)

    listed_videos: list[tuple[MicroSetRow, DriveFile]] = []
    for micro_set in live_sets:
        files = drive.list_files(micro_set.drive_folder_id)
        _sync_sidecar(drive, store, micro_set, files)
        session.commit()
        tally.waiting_sets += micro_set.status == MicroSetStatus.WAITING
        listed_videos += [
            (micro_set, drive_file)
            for drive_file in files
            if drive_file.mime_type.startswith("video/")
        ]

    _remove_missing_videos(session, {video.id for _, video in listed_videos}, tally)
    for micro_set, video in listed_videos:
        _sync_video(session, micro_set, video, tally)
    _download_new_videos(session, drive, store, micro_settings, ffprobe, tally)
    return MicroIngestSummary(**asdict(tally))


def _sync_sets(session: Session, folders: list[DriveFile], tally: _Tally) -> list[MicroSetRow]:
    """Insert unseen folders as `waiting`, reactivate restored ones and mark vanished ones
    `removed`. Returns the listed sets in listing order."""
    known = {row.drive_folder_id: row for row in session.scalars(select(MicroSetRow))}
    live_sets = []
    for folder in folders:
        micro_set = known.get(folder.id)
        if micro_set is None:
            micro_set = MicroSetRow(
                drive_folder_id=folder.id, name=folder.name, status=MicroSetStatus.WAITING
            )
            session.add(micro_set)
            tally.new_sets += 1
        else:
            micro_set.name = folder.name
            if micro_set.status == MicroSetStatus.REMOVED:
                micro_set.status = (
                    MicroSetStatus.ACTIVE if micro_set.song_title else MicroSetStatus.WAITING
                )
        session.commit()
        live_sets.append(micro_set)

    listed_ids = {folder.id for folder in folders}
    for micro_set in known.values():
        if (
            micro_set.drive_folder_id not in listed_ids
            and micro_set.status != MicroSetStatus.REMOVED
        ):
            micro_set.status = MicroSetStatus.REMOVED
            session.commit()
            tally.removed_sets += 1
    return live_sets


def _sync_sidecar(
    drive: DriveClient, store: MediaStore, micro_set: MicroSetRow, files: list[DriveFile]
) -> None:
    """Re-read the set's `.txt` when its id or md5 changed. A problem goes in `error`; the set
    keeps its status and its last good texts."""
    sidecars = sorted(
        (drive_file for drive_file in files if drive_file.name.lower().endswith(SIDECAR_SUFFIX)),
        key=lambda drive_file: drive_file.name,
    )
    if not sidecars:
        micro_set.error = "no .txt file in the folder"
        return
    if len(sidecars) > 1:
        names = ", ".join(sidecar.name for sidecar in sidecars)
        micro_set.error = f"more than one .txt file: {names}"
        return
    sidecar_file = sidecars[0]
    unchanged = (
        sidecar_file.md5 is not None
        and sidecar_file.id == micro_set.sidecar_drive_file_id
        and sidecar_file.md5 == micro_set.sidecar_md5
    )
    if unchanged:
        micro_set.error = None
        return
    try:
        sidecar = _download_sidecar(drive, store, micro_set, sidecar_file)
    except Exception as error:
        logger.warning(
            "reading micro set sidecar failed: %s",
            error,
            extra={"job": JOB_NAME, "set_id": micro_set.id},
        )
        micro_set.error = f"{type(error).__name__}: {error}"
        return
    micro_set.song_title = sidecar.song_title
    micro_set.caption = sidecar.caption
    micro_set.text_cutoff_s = sidecar.text_cutoff_s
    micro_set.text_options_json = list(sidecar.text_options)
    micro_set.sidecar_drive_file_id = sidecar_file.id
    micro_set.sidecar_md5 = sidecar_file.md5
    micro_set.status = MicroSetStatus.ACTIVE
    micro_set.error = None


def _download_sidecar(
    drive: DriveClient, store: MediaStore, micro_set: MicroSetRow, sidecar_file: DriveFile
) -> Sidecar:
    dest = store.path_for(MediaKind.SCRATCH, f"micro_set_{micro_set.id}{SIDECAR_SUFFIX}")
    try:
        drive.download(sidecar_file.id, dest)
        try:
            text = dest.read_bytes().decode("utf-8")
        except UnicodeDecodeError as error:
            raise SidecarError("sidecar is not UTF-8 text") from error
        return parse_sidecar(text)
    finally:
        dest.unlink(missing_ok=True)


def _remove_missing_videos(session: Session, listed_ids: set[str], tally: _Tally) -> None:
    """Videos of live sets that are no longer in their folder: `removed`, local copy deleted.
    Videos of removed sets are left as they are, in case the folder comes back."""
    query = (
        select(MicroSourceRow)
        .join(MicroSetRow)
        .where(MicroSetRow.status != MicroSetStatus.REMOVED)
        .where(MicroSourceRow.status != MicroSourceStatus.REMOVED)
        .order_by(MicroSourceRow.id)
    )
    for source in list(session.scalars(query)):
        if source.drive_file_id in listed_ids:
            continue
        _clear_media(source)
        source.status = MicroSourceStatus.REMOVED
        source.error = None
        source.attempts = 0
        session.commit()
        tally.removed_videos += 1


def _sync_video(session: Session, micro_set: MicroSetRow, video: DriveFile, tally: _Tally) -> None:
    """Record an unseen video, or restart one that reappeared or whose md5 changed."""
    source = session.scalar(select(MicroSourceRow).where(MicroSourceRow.drive_file_id == video.id))
    if source is None:
        source = MicroSourceRow(
            set_id=micro_set.id, drive_file_id=video.id, md5=video.md5, name=video.name
        )
        source.status = _entry_status(session, video.md5, exclude_id=None)
        session.add(source)
    else:
        source.set_id = micro_set.id
        source.name = video.name
        if source.status != MicroSourceStatus.REMOVED and source.md5 == video.md5:
            session.commit()
            return
        _clear_media(source)
        source.md5 = video.md5
        source.duration_s = None
        source.has_audio = None
        source.error = None
        source.attempts = 0
        source.status = _entry_status(session, video.md5, exclude_id=source.id)
    session.commit()
    if source.status == MicroSourceStatus.DUPLICATE:
        tally.duplicate_videos += 1
    else:
        tally.new_videos += 1


def _entry_status(session: Session, md5: str | None, exclude_id: int | None) -> MicroSourceStatus:
    """`duplicate` when another non-removed micro source has the same md5, else `new`."""
    if not md5:
        return MicroSourceStatus.NEW
    query = (
        select(MicroSourceRow.id)
        .where(MicroSourceRow.md5 == md5)
        .where(MicroSourceRow.status != MicroSourceStatus.REMOVED)
    )
    if exclude_id is not None:
        query = query.where(MicroSourceRow.id != exclude_id)
    if session.scalar(query.limit(1)) is None:
        return MicroSourceStatus.NEW
    return MicroSourceStatus.DUPLICATE


def _download_new_videos(
    session: Session,
    drive: DriveClient,
    store: MediaStore,
    settings: MicroSettings,
    ffprobe: str,
    tally: _Tally,
) -> None:
    query = (
        select(MicroSourceRow)
        .join(MicroSetRow)
        .where(MicroSetRow.status != MicroSetStatus.REMOVED)
        .where(MicroSourceRow.status == MicroSourceStatus.NEW)
        .order_by(MicroSourceRow.id)
    )
    for source in list(session.scalars(query)):
        suffix = Path(source.name).suffix.lower()
        dest = store.path_for(MediaKind.MICRO, f"micro_source_{source.id}{suffix}")
        try:
            drive.download(source.drive_file_id, dest)
            info = probe(dest, ffprobe)
        except Exception as error:
            dest.unlink(missing_ok=True)
            tally.failed_videos += _record_failure(source, error, settings.max_attempts)
            session.commit()
            continue
        source.local_path = str(dest)
        source.duration_s = info.duration_s
        source.has_audio = info.has_audio
        source.attempts = 0
        if info.duration_s < settings.min_source_s:
            source.status = MicroSourceStatus.FAILED
            source.error = (
                f"video is {info.duration_s:.1f} s, shorter than micro.min_source_s "
                f"({settings.min_source_s:.1f} s)"
            )
            tally.failed_videos += 1
        else:
            source.status = MicroSourceStatus.READY
            source.error = None
            tally.ready_videos += 1
        session.commit()


def _clear_media(source: MicroSourceRow) -> None:
    if source.local_path:
        Path(source.local_path).unlink(missing_ok=True)
    source.local_path = None


def _record_failure(source: MicroSourceRow, error: Exception, max_attempts: int) -> bool:
    """Record a failed download or probe; returns True if the video is now `failed`."""
    logger.warning(
        "micro video step failed: %s",
        error,
        exc_info=error,
        extra={"job": JOB_NAME, "row_id": source.id},
    )
    source.attempts += 1
    source.error = f"{type(error).__name__}: {error}"
    if source.attempts >= max_attempts:
        source.status = MicroSourceStatus.FAILED
        return True
    return False
