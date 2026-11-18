import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import DriveSettings, MicroSettings, load_settings
from engine.db.enums import MicroSetStatus, MicroSourceStatus
from engine.db.models import MicroSetRow, MicroSourceRow
from engine.micro.ingest import poll_micro_sets
from engine.micro.models import MicroIngestSummary
from engine.storage.media_store import LocalMediaStore, MediaKind
from tests.fakes.drive import FakeDriveClient

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
INBOX = "inbox"
SET_NAME = "You Dont Dance_micro"
SIDECAR = b"song: You Dont Dance\nPOV: you finally hit the drop\nwait for it\n"
SIDECAR_TEXTS = ["POV: you finally hit the drop", "wait for it"]


@pytest.fixture
def drive_settings() -> DriveSettings:
    return load_settings(REPO_CONFIG_DIR, env_file=None).drive.model_copy(
        update={"footage_inbox_folder_id": INBOX, "footage_processed_folder_id": "processed"}
    )


@pytest.fixture
def micro_settings() -> MicroSettings:
    return load_settings(REPO_CONFIG_DIR, env_file=None).micro.model_copy(
        update={"max_attempts": 3, "min_source_s": 3.0}
    )


@pytest.fixture
def store(tmp_path: Path) -> LocalMediaStore:
    return LocalMediaStore(tmp_path / "media")


@pytest.fixture
def drive() -> FakeDriveClient:
    return FakeDriveClient()


@pytest.fixture
def clip(landscape_motion_clip: Path) -> bytes:
    """6 s, with audio."""
    return landscape_motion_clip.read_bytes()


@pytest.fixture
def silent_clip(portrait_scenes_clip: Path) -> bytes:
    """10 s, no audio."""
    return portrait_scenes_clip.read_bytes()


class Poller:
    def __init__(
        self,
        session: Session,
        drive: FakeDriveClient,
        store: LocalMediaStore,
        drive_settings: DriveSettings,
        micro_settings: MicroSettings,
    ) -> None:
        self.session = session
        self.drive = drive
        self.store = store
        self.drive_settings = drive_settings
        self.micro_settings = micro_settings

    def __call__(self) -> MicroIngestSummary:
        return poll_micro_sets(
            self.session, self.drive, self.store, self.drive_settings, self.micro_settings
        )

    def sets(self) -> dict[str, MicroSetRow]:
        self.session.expire_all()
        return {row.drive_folder_id: row for row in self.session.scalars(select(MicroSetRow))}

    def sources(self) -> dict[str, MicroSourceRow]:
        self.session.expire_all()
        return {row.drive_file_id: row for row in self.session.scalars(select(MicroSourceRow))}


@pytest.fixture
def poll(
    db_session: Session,
    drive: FakeDriveClient,
    store: LocalMediaStore,
    drive_settings: DriveSettings,
    micro_settings: MicroSettings,
) -> Poller:
    return Poller(db_session, drive, store, drive_settings, micro_settings)


def add_set(
    drive: FakeDriveClient,
    folder_id: str = "set1",
    name: str = SET_NAME,
    sidecar: bytes | None = SIDECAR,
) -> None:
    drive.add_folder(INBOX, folder_id, name)
    if sidecar is not None:
        add_txt(drive, folder_id, f"{folder_id}_txt", sidecar)


def add_txt(
    drive: FakeDriveClient, folder_id: str, file_id: str, content: bytes, name: str = "texts.txt"
) -> None:
    drive.add_file(folder_id, file_id, content, name=name, mime_type="text/plain")


def add_video(
    drive: FakeDriveClient, folder_id: str, file_id: str, content: bytes, name: str = "take.mov"
) -> None:
    drive.add_file(folder_id, file_id, content, name=name, mime_type="video/quicktime")


def md5(content: bytes) -> str:
    return hashlib.md5(content).hexdigest()


def test_a_micro_folder_becomes_an_active_set_with_ready_videos(
    poll: Poller,
    drive: FakeDriveClient,
    store: LocalMediaStore,
    clip: bytes,
    silent_clip: bytes,
) -> None:
    add_set(drive)
    add_video(drive, "set1", "v1", clip, name="Take1.MOV")
    drive.add_file("set1", "v2", silent_clip, name="take2.mp4")
    add_set(drive, "holiday", "holiday clips", sidecar=SIDECAR)
    add_video(drive, "holiday", "h1", clip)
    add_video(drive, INBOX, "loose_video", clip, name="loose_micro.mov")
    add_txt(drive, INBOX, "loose_txt", SIDECAR)

    summary = poll()

    assert summary == MicroIngestSummary(sets=1, new_sets=1, new_videos=2, ready_videos=2)
    sets = poll.sets()
    assert list(sets) == ["set1"]
    micro_set = sets["set1"]
    assert micro_set.name == SET_NAME
    assert micro_set.status == MicroSetStatus.ACTIVE
    assert micro_set.song_title == "You Dont Dance"
    assert micro_set.text_options_json == SIDECAR_TEXTS
    assert micro_set.next_text_index == 0
    assert micro_set.sidecar_drive_file_id == "set1_txt"
    assert micro_set.sidecar_md5 == md5(SIDECAR)
    assert micro_set.error is None

    sources = poll.sources()
    assert list(sources) == ["v1", "v2"]
    first, second = sources["v1"], sources["v2"]
    assert first.set_id == micro_set.id
    assert first.name == "Take1.MOV"
    assert first.md5 == md5(clip)
    assert first.status == MicroSourceStatus.READY
    assert first.attempts == 0
    assert first.error is None
    assert first.has_audio is True
    assert first.duration_s == pytest.approx(6.0, abs=0.1)
    first_path = store.path_for(MediaKind.MICRO, f"micro_source_{first.id}.mov")
    assert first.local_path == str(first_path)
    assert first_path.read_bytes() == clip
    assert second.status == MicroSourceStatus.READY
    assert second.has_audio is False
    assert second.duration_s == pytest.approx(10.0, abs=0.1)
    assert second.local_path == str(
        store.path_for(MediaKind.MICRO, f"micro_source_{second.id}.mp4")
    )

    assert drive.downloads == ["set1_txt", "v1", "v2"]
    assert drive.moves == []


def test_a_second_run_changes_nothing(poll: Poller, drive: FakeDriveClient, clip: bytes) -> None:
    add_set(drive)
    add_video(drive, "set1", "v1", clip)
    poll()
    downloads = list(drive.downloads)

    assert poll() == MicroIngestSummary(sets=1)
    assert drive.downloads == downloads
    assert poll.sets()["set1"].status == MicroSetStatus.ACTIVE
    assert poll.sources()["v1"].status == MicroSourceStatus.READY


def test_a_txt_added_later_activates_the_waiting_set(
    poll: Poller, drive: FakeDriveClient, clip: bytes
) -> None:
    add_set(drive, sidecar=None)
    add_video(drive, "set1", "v1", clip)

    assert poll() == MicroIngestSummary(
        sets=1, new_sets=1, waiting_sets=1, new_videos=1, ready_videos=1
    )
    micro_set = poll.sets()["set1"]
    assert micro_set.status == MicroSetStatus.WAITING
    assert micro_set.error == "no .txt file in the folder"
    assert micro_set.song_title is None
    assert poll() == MicroIngestSummary(sets=1, waiting_sets=1)

    add_txt(drive, "set1", "t1", SIDECAR)

    assert poll() == MicroIngestSummary(sets=1)
    micro_set = poll.sets()["set1"]
    assert micro_set.status == MicroSetStatus.ACTIVE
    assert micro_set.error is None
    assert micro_set.song_title == "You Dont Dance"
    assert micro_set.sidecar_drive_file_id == "t1"


def test_an_edited_txt_updates_the_texts_and_keeps_the_text_index(
    poll: Poller, drive: FakeDriveClient
) -> None:
    add_set(drive)
    poll()
    micro_set = poll.sets()["set1"]
    micro_set.next_text_index = 5
    poll.session.commit()
    edited = b"song: Midnight City\none\ntwo\nthree\n"
    drive.replace_content("set1_txt", edited)

    assert poll() == MicroIngestSummary(sets=1)
    micro_set = poll.sets()["set1"]
    assert micro_set.status == MicroSetStatus.ACTIVE
    assert micro_set.song_title == "Midnight City"
    assert micro_set.text_options_json == ["one", "two", "three"]
    assert micro_set.next_text_index == 5
    assert micro_set.sidecar_md5 == md5(edited)

    drive.remove("set1_txt")
    add_txt(drive, "set1", "t2", SIDECAR, name="new.txt")

    poll()
    micro_set = poll.sets()["set1"]
    assert micro_set.sidecar_drive_file_id == "t2"
    assert micro_set.text_options_json == SIDECAR_TEXTS
    assert drive.downloads == ["set1_txt", "set1_txt", "t2"]


def test_caption_and_cutoff_are_stored_and_cleared_when_the_txt_drops_them(
    poll: Poller, drive: FakeDriveClient
) -> None:
    add_set(drive, sidecar=b"song: you don't dance\ncaption: new drop\ncutoff: 0:02.4\nhi\n")

    poll()
    micro_set = poll.sets()["set1"]
    assert micro_set.status == MicroSetStatus.ACTIVE
    assert micro_set.caption == "new drop"
    assert micro_set.text_cutoff_s == 2.4
    assert micro_set.text_options_json == ["hi"]

    drive.replace_content("set1_txt", SIDECAR)

    poll()
    micro_set = poll.sets()["set1"]
    assert micro_set.caption is None
    assert micro_set.text_cutoff_s is None
    assert micro_set.text_options_json == SIDECAR_TEXTS


def test_two_txt_files_are_an_error_until_one_is_removed(
    poll: Poller, drive: FakeDriveClient
) -> None:
    add_set(drive)
    add_set(drive, "set2", "second_micro")
    add_txt(drive, "set2", "set2_more", SIDECAR, name="more.txt")
    poll()
    add_txt(drive, "set1", "extra", b"song: Other\nother text\n", name="Extra.TXT")

    assert poll() == MicroIngestSummary(sets=2, waiting_sets=1)
    sets = poll.sets()
    assert sets["set1"].status == MicroSetStatus.ACTIVE
    assert sets["set1"].error == "more than one .txt file: Extra.TXT, texts.txt"
    assert sets["set1"].song_title == "You Dont Dance"
    assert sets["set1"].text_options_json == SIDECAR_TEXTS
    assert sets["set2"].status == MicroSetStatus.WAITING
    assert sets["set2"].error == "more than one .txt file: more.txt, texts.txt"
    assert sets["set2"].song_title is None

    drive.remove("extra")

    assert poll() == MicroIngestSummary(sets=2, waiting_sets=1)
    micro_set = poll.sets()["set1"]
    assert micro_set.status == MicroSetStatus.ACTIVE
    assert micro_set.error is None
    assert drive.downloads == ["set1_txt"]


def test_an_invalid_txt_on_an_active_set_keeps_the_last_good_texts(
    poll: Poller, drive: FakeDriveClient
) -> None:
    add_set(drive)
    poll()
    drive.replace_content("set1_txt", b"POV: no song line\n")

    assert poll() == MicroIngestSummary(sets=1)
    micro_set = poll.sets()["set1"]
    assert micro_set.status == MicroSetStatus.ACTIVE
    assert micro_set.error == (
        "SidecarError: first non-empty line must be `song: <name>`, got 'POV: no song line'"
    )
    assert micro_set.song_title == "You Dont Dance"
    assert micro_set.text_options_json == SIDECAR_TEXTS
    assert micro_set.sidecar_md5 == md5(SIDECAR)

    fixed = b"\xef\xbb\xbfsong: Fixed\r\nhello\r\n"
    drive.replace_content("set1_txt", fixed)

    poll()
    micro_set = poll.sets()["set1"]
    assert micro_set.error is None
    assert micro_set.song_title == "Fixed"
    assert micro_set.text_options_json == ["hello"]
    assert micro_set.sidecar_md5 == md5(fixed)


def test_an_invalid_or_non_utf8_txt_keeps_a_new_set_waiting(
    poll: Poller, drive: FakeDriveClient
) -> None:
    add_set(drive, sidecar=b"song: A\n")
    add_set(drive, "set2", "two_micro", sidecar=b"song: \xff\nhi\n")

    assert poll() == MicroIngestSummary(sets=2, new_sets=2, waiting_sets=2)
    sets = poll.sets()
    assert sets["set1"].status == MicroSetStatus.WAITING
    assert sets["set1"].error == "SidecarError: sidecar has no text options after the song line"
    assert sets["set1"].sidecar_md5 is None
    assert sets["set2"].error == "SidecarError: sidecar is not UTF-8 text"


def test_a_video_added_later_joins_the_set(
    poll: Poller, drive: FakeDriveClient, clip: bytes, silent_clip: bytes
) -> None:
    add_set(drive)
    add_video(drive, "set1", "v1", clip)
    poll()
    add_video(drive, "set1", "v2", silent_clip)

    assert poll() == MicroIngestSummary(sets=1, new_videos=1, ready_videos=1)
    sources = poll.sources()
    assert sources["v2"].status == MicroSourceStatus.READY
    assert sources["v2"].set_id == sources["v1"].set_id


def test_a_deleted_video_is_removed_with_its_local_file_and_can_come_back(
    poll: Poller, drive: FakeDriveClient, clip: bytes
) -> None:
    add_set(drive)
    add_video(drive, "set1", "v1", clip)
    poll()
    local = Path(poll.sources()["v1"].local_path or "")
    assert local.is_file()
    drive.remove("v1")

    assert poll() == MicroIngestSummary(sets=1, removed_videos=1)
    source = poll.sources()["v1"]
    assert source.status == MicroSourceStatus.REMOVED
    assert source.local_path is None
    assert not local.exists()
    assert poll() == MicroIngestSummary(sets=1)

    add_video(drive, "set1", "v1", clip)

    assert poll() == MicroIngestSummary(sets=1, new_videos=1, ready_videos=1)
    source = poll.sources()["v1"]
    assert source.status == MicroSourceStatus.READY
    assert source.local_path == str(local)
    assert local.read_bytes() == clip


def test_a_video_reuploaded_with_a_new_md5_is_downloaded_again(
    poll: Poller, drive: FakeDriveClient, clip: bytes, silent_clip: bytes
) -> None:
    add_set(drive)
    add_video(drive, "set1", "v1", clip)
    poll()
    first_id = poll.sources()["v1"].id
    drive.replace_content("v1", silent_clip)

    assert poll() == MicroIngestSummary(sets=1, new_videos=1, ready_videos=1)
    source = poll.sources()["v1"]
    assert source.id == first_id
    assert source.status == MicroSourceStatus.READY
    assert source.md5 == md5(silent_clip)
    assert source.has_audio is False
    assert source.duration_s == pytest.approx(10.0, abs=0.1)
    assert Path(source.local_path or "").read_bytes() == silent_clip
    assert drive.downloads == ["set1_txt", "v1", "v1"]


def test_the_same_video_in_another_set_is_a_duplicate_and_never_downloaded(
    poll: Poller, drive: FakeDriveClient, clip: bytes
) -> None:
    add_set(drive)
    add_video(drive, "set1", "v1", clip)
    add_set(drive, "set2", "other_MICRO")
    add_video(drive, "set2", "copy", clip, name="copy.mov")

    assert poll() == MicroIngestSummary(
        sets=2, new_sets=2, new_videos=1, ready_videos=1, duplicate_videos=1
    )
    sources = poll.sources()
    assert sources["v1"].status == MicroSourceStatus.READY
    assert sources["copy"].status == MicroSourceStatus.DUPLICATE
    assert sources["copy"].local_path is None
    assert "copy" not in drive.downloads
    assert poll() == MicroIngestSummary(sets=2)


def test_a_removed_folder_stops_the_set_and_restoring_it_reactivates_it(
    poll: Poller, drive: FakeDriveClient, clip: bytes
) -> None:
    add_set(drive)
    add_video(drive, "set1", "v1", clip)
    poll()
    downloads = list(drive.downloads)
    drive.remove("set1")

    assert poll() == MicroIngestSummary(removed_sets=1)
    micro_set = poll.sets()["set1"]
    assert micro_set.status == MicroSetStatus.REMOVED
    assert micro_set.song_title == "You Dont Dance"
    assert poll.sources()["v1"].status == MicroSourceStatus.READY
    assert poll() == MicroIngestSummary()

    drive.add_folder(INBOX, "set1", SET_NAME)

    assert poll() == MicroIngestSummary(sets=1)
    assert poll.sets()["set1"].status == MicroSetStatus.ACTIVE
    assert poll.sources()["v1"].status == MicroSourceStatus.READY
    assert drive.downloads == downloads


def test_a_video_shorter_than_the_minimum_fails_without_retry(
    poll: Poller, drive: FakeDriveClient, clip: bytes
) -> None:
    poll.micro_settings = poll.micro_settings.model_copy(update={"min_source_s": 7.0})
    add_set(drive)
    add_video(drive, "set1", "v1", clip)

    assert poll() == MicroIngestSummary(sets=1, new_sets=1, new_videos=1, failed_videos=1)
    source = poll.sources()["v1"]
    assert source.status == MicroSourceStatus.FAILED
    assert source.error == "video is 6.0 s, shorter than micro.min_source_s (7.0 s)"
    assert source.attempts == 0
    assert source.duration_s == pytest.approx(6.0, abs=0.1)

    assert poll() == MicroIngestSummary(sets=1)
    assert drive.downloads == ["set1_txt", "v1"]


def test_a_failed_download_is_retried_and_counts_attempts(
    poll: Poller, drive: FakeDriveClient, clip: bytes
) -> None:
    add_set(drive)
    add_video(drive, "set1", "v1", clip)
    drive.fail_downloads["v1"] = 1

    assert poll() == MicroIngestSummary(sets=1, new_sets=1, new_videos=1)
    source = poll.sources()["v1"]
    assert source.status == MicroSourceStatus.NEW
    assert source.attempts == 1
    assert source.error == "FakeDriveError: download of v1 failed"

    assert poll() == MicroIngestSummary(sets=1, ready_videos=1)
    source = poll.sources()["v1"]
    assert source.status == MicroSourceStatus.READY
    assert source.attempts == 0
    assert source.error is None


def test_downloads_that_keep_failing_reach_failed_at_max_attempts(
    poll: Poller, drive: FakeDriveClient, clip: bytes
) -> None:
    add_set(drive)
    add_video(drive, "set1", "v1", clip)
    drive.fail_downloads["v1"] = 5

    summaries = [poll() for _ in range(4)]

    assert summaries == [
        MicroIngestSummary(sets=1, new_sets=1, new_videos=1),
        MicroIngestSummary(sets=1),
        MicroIngestSummary(sets=1, failed_videos=1),
        MicroIngestSummary(sets=1),
    ]
    source = poll.sources()["v1"]
    assert source.status == MicroSourceStatus.FAILED
    assert source.attempts == 3
    assert source.error == "FakeDriveError: download of v1 failed"
    assert drive.fail_downloads["v1"] == 2


def test_a_file_that_cannot_be_probed_counts_attempts(poll: Poller, drive: FakeDriveClient) -> None:
    add_set(drive)
    add_video(drive, "set1", "v1", b"not a video")

    summaries = [poll() for _ in range(3)]

    assert summaries[-1] == MicroIngestSummary(sets=1, failed_videos=1)
    source = poll.sources()["v1"]
    assert source.status == MicroSourceStatus.FAILED
    assert source.attempts == 3
    assert (source.error or "").startswith("FfmpegError: ffprobe exited with 1")
    assert source.duration_s is None
    assert source.local_path is None


def test_a_video_that_cannot_be_probed_leaves_no_file_in_the_micro_folder(
    poll: Poller, drive: FakeDriveClient, store: LocalMediaStore
) -> None:
    add_set(drive)
    add_video(drive, "set1", "v1", b"not a video")

    poll()

    assert list(store.path_for(MediaKind.MICRO, "x").parent.iterdir()) == []
