from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import DriveSettings, load_settings
from engine.db.enums import FootageStatus
from engine.db.models import FootageFileRow
from engine.ingest.models import PollSummary
from engine.ingest.poller import poll_footage
from engine.storage.media_store import LocalMediaStore, MediaKind
from tests.fakes.drive import FakeDriveClient

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
INBOX = "inbox"
PROCESSED = "processed"
MAX_ATTEMPTS = 3


@pytest.fixture
def settings() -> DriveSettings:
    return load_settings(REPO_CONFIG_DIR, env_file=None).drive.model_copy(
        update={"footage_inbox_folder_id": INBOX, "footage_processed_folder_id": PROCESSED}
    )


@pytest.fixture
def store(tmp_path: Path) -> LocalMediaStore:
    return LocalMediaStore(tmp_path / "media")


@pytest.fixture
def drive() -> FakeDriveClient:
    return FakeDriveClient()


def poll(
    session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> PollSummary:
    summary = poll_footage(session, drive, store, settings, max_attempts=MAX_ATTEMPTS)
    session.commit()
    return summary


def rows(session: Session) -> dict[str, FootageFileRow]:
    return {row.drive_file_id: row for row in session.scalars(select(FootageFileRow))}


def test_new_files_are_recorded_and_downloaded_to_scratch(
    db_session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> None:
    drive.add_file(INBOX, "f1", b"first", name="Beach.MOV")
    drive.add_file(INBOX, "f2", b"second")
    drive.add_file("elsewhere", "f3", b"third")

    summary = poll(db_session, drive, store, settings)

    assert summary == PollSummary(listed=2, new=2, downloaded=2)
    by_id = rows(db_session)
    assert set(by_id) == {"f1", "f2"}
    first = by_id["f1"]
    assert first.status == FootageStatus.DOWNLOADED
    assert first.name == "Beach.MOV"
    assert first.md5 == drive.list_videos(INBOX)[0].md5
    assert first.size_bytes == 5
    assert first.local_path is not None
    local = Path(first.local_path)
    assert local.parent == store.path_for(MediaKind.SCRATCH, "x").parent
    assert local.suffix == ".mov"
    assert local.read_bytes() == b"first"
    assert Path(by_id["f2"].local_path or "").read_bytes() == b"second"
    assert drive.file_ids_in(INBOX) == ["f1", "f2"]


def test_a_second_run_does_nothing(
    db_session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> None:
    drive.add_file(INBOX, "f1", b"first")
    poll(db_session, drive, store, settings)
    before = {key: (row.status, row.local_path) for key, row in rows(db_session).items()}

    summary = poll(db_session, drive, store, settings)

    assert summary == PollSummary(listed=1)
    assert drive.downloads == ["f1"]
    assert {key: (row.status, row.local_path) for key, row in rows(db_session).items()} == before


def test_a_file_whose_md5_is_already_known_is_a_duplicate_moved_without_download(
    db_session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> None:
    drive.add_file(INBOX, "original", b"same bytes")
    poll(db_session, drive, store, settings)
    drive.add_file(INBOX, "copy", b"same bytes")

    summary = poll(db_session, drive, store, settings)

    assert summary == PollSummary(listed=2, new=0, duplicates=1)
    copy = rows(db_session)["copy"]
    assert copy.status == FootageStatus.DUPLICATE
    assert copy.local_path is None
    assert drive.downloads == ["original"]
    assert drive.file_ids_in(PROCESSED) == ["copy"]
    assert drive.file_ids_in(INBOX) == ["original"]


def test_duplicates_within_one_listing_keep_only_the_first(
    db_session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> None:
    drive.add_file(INBOX, "a", b"same")
    drive.add_file(INBOX, "b", b"same")

    summary = poll(db_session, drive, store, settings)

    assert summary == PollSummary(listed=2, new=1, duplicates=1, downloaded=1)
    assert rows(db_session)["a"].status == FootageStatus.DOWNLOADED
    assert rows(db_session)["b"].status == FootageStatus.DUPLICATE
    assert drive.file_ids_in(PROCESSED) == ["b"]


def test_files_without_md5_are_never_duplicates(
    db_session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> None:
    drive.add_file(INBOX, "a", b"one", md5=None)
    drive.add_file(INBOX, "b", b"two", md5=None)

    summary = poll(db_session, drive, store, settings)

    assert summary.duplicates == 0
    assert {row.status for row in rows(db_session).values()} == {FootageStatus.DOWNLOADED}


def test_a_duplicate_whose_move_failed_is_moved_on_the_next_run(
    db_session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> None:
    drive.add_file(INBOX, "a", b"same")
    drive.add_file(INBOX, "b", b"same")
    drive.fail_moves["b"] = 1

    poll(db_session, drive, store, settings)
    assert rows(db_session)["b"].status == FootageStatus.DUPLICATE
    assert "move of b failed" in (rows(db_session)["b"].error or "")
    assert drive.file_ids_in(PROCESSED) == []

    poll(db_session, drive, store, settings)
    assert drive.file_ids_in(PROCESSED) == ["b"]
    assert rows(db_session)["b"].error is None


def test_a_failed_download_is_recorded_and_retried_next_run(
    db_session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> None:
    drive.add_file(INBOX, "f1", b"bytes")
    drive.add_file(INBOX, "f2", b"other")
    drive.fail_downloads["f1"] = 1

    summary = poll(db_session, drive, store, settings)

    assert summary == PollSummary(listed=2, new=2, downloaded=1)
    row = rows(db_session)["f1"]
    assert row.status == FootageStatus.NEW
    assert row.attempts == 1
    assert "download of f1 failed" in (row.error or "")
    assert rows(db_session)["f2"].status == FootageStatus.DOWNLOADED

    summary = poll(db_session, drive, store, settings)

    assert summary == PollSummary(listed=2, downloaded=1)
    row = rows(db_session)["f1"]
    assert row.status == FootageStatus.DOWNLOADED
    assert row.error is None
    assert row.attempts == 0
    assert Path(row.local_path or "").read_bytes() == b"bytes"


def test_a_download_failing_max_attempts_times_becomes_failed_and_is_not_retried(
    db_session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> None:
    drive.add_file(INBOX, "f1", b"bytes")
    drive.fail_downloads["f1"] = MAX_ATTEMPTS + 5

    summaries = [poll(db_session, drive, store, settings) for _ in range(MAX_ATTEMPTS)]

    assert [summary.failed for summary in summaries] == [0] * (MAX_ATTEMPTS - 1) + [1]
    row = rows(db_session)["f1"]
    assert row.status == FootageStatus.FAILED
    assert row.attempts == MAX_ATTEMPTS
    remaining_failures = drive.fail_downloads["f1"]

    assert poll(db_session, drive, store, settings) == PollSummary(listed=1)
    assert drive.fail_downloads["f1"] == remaining_failures
    assert rows(db_session)["f1"].attempts == MAX_ATTEMPTS


def test_tagged_files_are_moved_to_processed_marked_done_and_their_download_deleted(
    db_session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> None:
    drive.add_file(INBOX, "f1", b"bytes")
    poll(db_session, drive, store, settings)
    row = rows(db_session)["f1"]
    local = Path(row.local_path or "")
    row.status = FootageStatus.TAGGED
    db_session.commit()

    summary = poll(db_session, drive, store, settings)

    assert summary == PollSummary(listed=0, completed=1)
    row = rows(db_session)["f1"]
    assert row.status == FootageStatus.DONE
    assert row.local_path is None
    assert not local.exists()
    assert drive.file_ids_in(PROCESSED) == ["f1"]
    assert drive.file_ids_in(INBOX) == []


def test_a_failed_move_of_a_tagged_file_keeps_it_tagged_with_the_error(
    db_session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> None:
    drive.add_file(INBOX, "f1", b"bytes")
    poll(db_session, drive, store, settings)
    rows(db_session)["f1"].status = FootageStatus.TAGGED
    db_session.commit()
    drive.fail_moves["f1"] = 1

    summary = poll(db_session, drive, store, settings)

    assert summary.completed == 0
    row = rows(db_session)["f1"]
    assert row.status == FootageStatus.TAGGED
    assert row.attempts == 1
    assert "move of f1 failed" in (row.error or "")
    assert Path(row.local_path or "").exists()


def test_missing_folder_ids_are_a_config_error(
    db_session: Session, drive: FakeDriveClient, store: LocalMediaStore, settings: DriveSettings
) -> None:
    unset = settings.model_copy(update={"footage_processed_folder_id": None})
    with pytest.raises(ValueError, match="footage_processed_folder_id"):
        poll_footage(db_session, drive, store, unset, max_attempts=MAX_ATTEMPTS)
