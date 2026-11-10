"""The footage job must not hold a SQLite write lock during slow work.

Each slow step (Drive download, shot building, Drive move) writes a song row through a second
connection with a short busy timeout, as `ace snippets add` would from another process.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from engine.config import IngestSettings, load_settings
from engine.db.enums import FootageStatus, Orientation
from engine.db.models import FootageFileRow, SongRow
from engine.db.session import create_db_engine, make_session_factory
from engine.ingest.job import run_footage_ingest
from engine.ingest.models import Shot, ShotTags
from engine.storage.media_store import LocalMediaStore, MediaKind, MediaStore
from engine.vision.tagger import VisionTagger
from tests.fakes.drive import FakeDriveClient
from tests.fakes.vision import DEFAULT_TAGS

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
INBOX = "inbox"
PROCESSED = "processed"


class UnusedTagger:
    def tag_shot(self, frames: list[Path]) -> ShotTags:
        raise AssertionError("the fake builder never tags")


@dataclass
class OtherProcess:
    """A second connection to the same database file that gives up on a lock after 0.2 s."""

    engine: Engine
    outcomes: list[str] = field(default_factory=list)

    def insert_song(self, during: str) -> None:
        try:
            with make_session_factory(self.engine)() as session:
                session.add(SongRow(title=during, audio_path=f"{during}.wav"))
                session.commit()
        except OperationalError as error:
            self.outcomes.append(f"{during}: {error.orig}")
        else:
            self.outcomes.append(f"{during}: ok")


class WritingDriveClient(FakeDriveClient):
    def __init__(self, other: OtherProcess) -> None:
        super().__init__()
        self.other = other

    def download(self, file_id: str, dest: Path) -> Path:
        self.other.insert_song(f"download {file_id}")
        return super().download(file_id, dest)

    def move(self, file_id: str, to_folder_id: str) -> None:
        self.other.insert_song(f"move {file_id}")
        super().move(file_id, to_folder_id)


@dataclass
class WritingShotBuilder:
    other: OtherProcess

    def __call__(
        self,
        video_path: Path,
        footage_file_id: int,
        tagger: VisionTagger,
        store: MediaStore,
        settings: IngestSettings,
    ) -> list[Shot]:
        content = video_path.read_bytes().decode()
        self.other.insert_song(f"build {content}")
        return [
            Shot(
                footage_file_id=footage_file_id,
                start_s=0.0,
                end_s=1.5,
                width=1080,
                height=1920,
                fps=30.0,
                orientation=Orientation.PORTRAIT,
                motion_score=0.25,
                quality_score=0.7,
                tags=DEFAULT_TAGS,
                proxy_path=store.path_for(MediaKind.SHOTS, f"{content}.mp4"),
                thumbnail_path=store.path_for(MediaKind.SHOTS, f"{content}.jpg"),
            )
        ]


@pytest.fixture
def other(db_url: str) -> Iterator[OtherProcess]:
    engine = create_engine(db_url, connect_args={"timeout": 0.2})
    yield OtherProcess(engine)
    engine.dispose()


@pytest.fixture
def session(db_url: str) -> Iterator[Session]:
    engine = create_db_engine(db_url)
    with make_session_factory(engine)() as session:
        yield session
    engine.dispose()


def run_job(session: Session, drive: FakeDriveClient, other: OtherProcess, tmp_path: Path) -> None:
    settings = load_settings(REPO_CONFIG_DIR, env_file=None)
    drive_settings = settings.drive.model_copy(
        update={"footage_inbox_folder_id": INBOX, "footage_processed_folder_id": PROCESSED}
    )
    run_footage_ingest(
        session,
        drive,
        LocalMediaStore(tmp_path / "media"),
        UnusedTagger(),
        drive_settings,
        settings.ingest,
        build=WritingShotBuilder(other),
    )


def test_other_processes_can_write_during_downloads_builds_and_moves(
    session: Session, other: OtherProcess, tmp_path: Path
) -> None:
    drive = WritingDriveClient(other)
    drive.add_file(INBOX, "f1", b"first")
    drive.add_file(INBOX, "f2", b"second")

    run_job(session, drive, other, tmp_path)

    assert other.outcomes == [
        "download f1: ok",
        "download f2: ok",
        "build first: ok",
        "build second: ok",
        "move f1: ok",
        "move f2: ok",
    ]
    statuses = session.execute(select(FootageFileRow.drive_file_id, FootageFileRow.status))
    assert sorted(statuses) == [("f1", FootageStatus.DONE), ("f2", FootageStatus.DONE)]


def test_other_processes_can_write_while_a_duplicate_and_a_previously_tagged_file_move(
    session: Session, other: OtherProcess, tmp_path: Path
) -> None:
    drive = WritingDriveClient(other)
    drive.add_file(INBOX, "f1", b"first")
    drive.fail_moves["f1"] = 1
    run_job(session, drive, other, tmp_path)
    drive.add_file(INBOX, "copy", b"first")
    other.outcomes.clear()

    run_job(session, drive, other, tmp_path)

    assert other.outcomes == ["move f1: ok", "move copy: ok"]
    statuses = session.execute(select(FootageFileRow.drive_file_id, FootageFileRow.status))
    assert sorted(statuses) == [("copy", FootageStatus.DUPLICATE), ("f1", FootageStatus.DONE)]


def test_each_finished_download_is_committed_before_the_next_one_starts(
    session: Session, other: OtherProcess, tmp_path: Path
) -> None:
    class CrashOnSecondDownload(FakeDriveClient):
        def download(self, file_id: str, dest: Path) -> Path:
            if file_id == "f2":
                raise KeyboardInterrupt
            return super().download(file_id, dest)

    drive = CrashOnSecondDownload()
    drive.add_file(INBOX, "f1", b"first")
    drive.add_file(INBOX, "f2", b"second")

    with pytest.raises(KeyboardInterrupt):
        run_job(session, drive, other, tmp_path)
    session.rollback()

    with make_session_factory(other.engine)() as fresh:
        statuses = fresh.execute(select(FootageFileRow.drive_file_id, FootageFileRow.status))
        assert sorted(statuses) == [("f1", FootageStatus.DOWNLOADED), ("f2", FootageStatus.NEW)]
