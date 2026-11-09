from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import DriveSettings, IngestSettings, load_settings
from engine.db.enums import FootageStatus, Orientation
from engine.db.models import FootageFileRow, ShotRow
from engine.ingest.job import FootageIngestSummary, run_footage_ingest
from engine.ingest.models import PollSummary, QualityIssue, Shot, ShotTags
from engine.storage.media_store import LocalMediaStore, MediaKind, MediaStore
from engine.vision.tagger import VisionTagError, VisionTagger
from tests.fakes.drive import FakeDriveClient
from tests.fakes.vision import FakeVisionTagger

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
INBOX = "inbox"
PROCESSED = "processed"

TAGS = ShotTags(
    category="nature",
    subject="landscape",
    setting="beach",
    lighting="golden_hour",
    mood="calm",
    has_face=False,
    quality=0.8,
    issues=(QualityIssue.SHAKY,),
)


class UnusedTagger:
    def tag_shot(self, frames: list[Path]) -> ShotTags:
        raise AssertionError("the fake builder never tags")


@dataclass
class BuildCall:
    video_bytes: bytes
    footage_file_id: int
    tagger: VisionTagger
    store: MediaStore
    settings: IngestSettings


@dataclass
class FakeShotBuilder:
    """Returns `shots_per_file[name]` shots per call; `failures[name]` raises that many times."""

    shots_per_file: dict[str, int] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    calls: list[BuildCall] = field(default_factory=list)

    def __call__(
        self,
        video_path: Path,
        footage_file_id: int,
        tagger: VisionTagger,
        store: MediaStore,
        settings: IngestSettings,
    ) -> list[Shot]:
        self.calls.append(
            BuildCall(video_path.read_bytes(), footage_file_id, tagger, store, settings)
        )
        content = video_path.read_bytes().decode()
        if self.failures.get(content, 0) > 0:
            self.failures[content] -= 1
            raise VisionTagError(f"tagging {content} failed")
        return [
            Shot(
                footage_file_id=footage_file_id,
                start_s=2.0 * index,
                end_s=2.0 * index + 1.5,
                width=1080,
                height=1920,
                fps=30.0,
                orientation=Orientation.PORTRAIT,
                motion_score=0.25,
                quality_score=0.7,
                tags=TAGS,
                proxy_path=store.path_for(MediaKind.SHOTS, f"{content}_{index}.mp4"),
                thumbnail_path=store.path_for(MediaKind.SHOTS, f"{content}_{index}.jpg"),
            )
            for index in range(self.shots_per_file.get(content, 1))
        ]


@dataclass
class Job:
    session: Session
    drive: FakeDriveClient
    store: LocalMediaStore
    drive_settings: DriveSettings
    ingest_settings: IngestSettings
    tagger: VisionTagger = field(default_factory=UnusedTagger)

    def run(self, build: FakeShotBuilder | None = None) -> FootageIngestSummary:
        if build is None:
            return run_footage_ingest(
                self.session,
                self.drive,
                self.store,
                self.tagger,
                self.drive_settings,
                self.ingest_settings,
            )
        return run_footage_ingest(
            self.session,
            self.drive,
            self.store,
            self.tagger,
            self.drive_settings,
            self.ingest_settings,
            build=build,
        )

    def footage(self) -> dict[str, FootageFileRow]:
        self.session.expire_all()
        rows = self.session.scalars(select(FootageFileRow))
        return {row.drive_file_id: row for row in rows}

    def shots(self) -> list[ShotRow]:
        return list(self.session.scalars(select(ShotRow).order_by(ShotRow.id)))


@pytest.fixture
def job(db_session: Session, tmp_path: Path) -> Job:
    settings = load_settings(REPO_CONFIG_DIR, env_file=None)
    drive_settings = settings.drive.model_copy(
        update={"footage_inbox_folder_id": INBOX, "footage_processed_folder_id": PROCESSED}
    )
    return Job(
        db_session,
        FakeDriveClient(),
        LocalMediaStore(tmp_path / "media"),
        drive_settings,
        settings.ingest,
    )


def scratch_files(store: LocalMediaStore) -> list[Path]:
    return sorted(store.path_for(MediaKind.SCRATCH, "x").parent.iterdir())


def test_footage_is_split_into_stored_shots_moved_to_processed_and_scratch_cleared(
    job: Job,
) -> None:
    job.drive.add_file(INBOX, "f1", b"first")
    job.drive.add_file(INBOX, "f2", b"second")
    build = FakeShotBuilder(shots_per_file={"first": 2, "second": 1})

    summary = job.run(build)

    assert summary == FootageIngestSummary(
        poll=PollSummary(listed=2, new=2, downloaded=2), built=2, shots=3, completed=2
    )
    footage = job.footage()
    assert {row.status for row in footage.values()} == {FootageStatus.DONE}
    assert all(row.local_path is None for row in footage.values())
    assert job.drive.file_ids_in(PROCESSED) == ["f1", "f2"]
    assert job.drive.file_ids_in(INBOX) == []
    assert scratch_files(job.store) == []

    assert [(call.video_bytes, call.footage_file_id) for call in build.calls] == [
        (b"first", footage["f1"].id),
        (b"second", footage["f2"].id),
    ]
    assert build.calls[0].tagger is job.tagger
    assert build.calls[0].store is job.store
    assert build.calls[0].settings == job.ingest_settings

    shots = job.shots()
    assert [(shot.footage_file_id, shot.start_s, shot.end_s) for shot in shots] == [
        (footage["f1"].id, 0.0, 1.5),
        (footage["f1"].id, 2.0, 3.5),
        (footage["f2"].id, 0.0, 1.5),
    ]
    second = shots[1]
    assert second.duration_s == 1.5
    assert (second.width, second.height, second.fps) == (1080, 1920, 30.0)
    assert second.orientation == Orientation.PORTRAIT
    assert (second.category, second.subject, second.setting) == ("nature", "landscape", "beach")
    assert (second.lighting, second.mood, second.has_face) == ("golden_hour", "calm", False)
    assert (second.motion_score, second.quality_score) == (0.25, 0.7)
    assert second.tags_json == TAGS.model_dump(mode="json")
    assert second.proxy_path == str(job.store.path_for(MediaKind.SHOTS, "first_1.mp4"))
    assert second.thumbnail_path == str(job.store.path_for(MediaKind.SHOTS, "first_1.jpg"))
    assert second.use_count == 0


def test_a_second_run_does_nothing(job: Job) -> None:
    job.drive.add_file(INBOX, "f1", b"first")
    build = FakeShotBuilder()
    job.run(build)

    summary = job.run(build)

    assert summary == FootageIngestSummary(poll=PollSummary())
    assert len(build.calls) == 1
    assert len(job.shots()) == 1


def test_a_builder_error_is_recorded_and_the_next_file_still_processed(job: Job) -> None:
    job.drive.add_file(INBOX, "f1", b"first")
    job.drive.add_file(INBOX, "f2", b"second")
    build = FakeShotBuilder(failures={"first": 1})

    summary = job.run(build)

    assert (summary.built, summary.completed, summary.failed) == (1, 1, 0)
    first = job.footage()["f1"]
    assert first.status == FootageStatus.DOWNLOADED
    assert first.attempts == 1
    assert first.error == "VisionTagError: tagging first failed"
    assert Path(first.local_path or "").read_bytes() == b"first"
    assert job.drive.file_ids_in(INBOX) == ["f1"]
    assert job.footage()["f2"].status == FootageStatus.DONE
    assert {shot.footage_file_id for shot in job.shots()} == {job.footage()["f2"].id}

    summary = job.run(build)

    assert (summary.built, summary.completed) == (1, 1)
    first = job.footage()["f1"]
    assert first.status == FootageStatus.DONE
    assert (first.attempts, first.error) == (0, None)
    assert job.drive.downloads == ["f1", "f2"]


def test_a_file_failing_to_build_max_attempts_times_becomes_failed(job: Job) -> None:
    job.drive.add_file(INBOX, "f1", b"first")
    max_attempts = job.ingest_settings.max_attempts
    build = FakeShotBuilder(failures={"first": max_attempts + 5})

    summaries = [job.run(build) for _ in range(max_attempts)]

    assert [summary.failed for summary in summaries] == [0] * (max_attempts - 1) + [1]
    assert job.footage()["f1"].status == FootageStatus.FAILED
    assert job.footage()["f1"].attempts == max_attempts

    job.run(build)
    assert len(build.calls) == max_attempts
    assert job.shots() == []


def test_a_build_that_finds_no_shots_is_recorded_as_an_error(job: Job) -> None:
    job.drive.add_file(INBOX, "f1", b"first")

    summary = job.run(FakeShotBuilder(shots_per_file={"first": 0}))

    assert summary.built == 0
    row = job.footage()["f1"]
    assert row.status == FootageStatus.DOWNLOADED
    assert row.attempts == 1
    assert "no shots" in (row.error or "")


def test_the_real_shot_builder_turns_a_drive_clip_into_tagged_shots(
    job: Job, portrait_scenes_clip: Path
) -> None:
    job.tagger = FakeVisionTagger()
    job.drive.add_file(INBOX, "f1", portrait_scenes_clip.read_bytes())

    summary = job.run()

    assert (summary.built, summary.shots, summary.completed) == (1, 4, 1)
    assert job.footage()["f1"].status == FootageStatus.DONE
    assert [round(shot.start_s, 2) for shot in job.shots()] == [0.0, 2.5, 5.0, 7.5]


def test_an_unreadable_file_fails_in_the_real_builder_and_is_recorded_not_raised(
    job: Job,
) -> None:
    job.drive.add_file(INBOX, "f1", b"not a video")

    summary = job.run()

    assert summary.built == 0
    row = job.footage()["f1"]
    assert row.status == FootageStatus.DOWNLOADED
    assert (row.error or "").startswith("FfmpegError")
    assert row.attempts == 1


def test_a_failed_move_after_tagging_keeps_the_shots_and_finishes_next_run(job: Job) -> None:
    job.drive.add_file(INBOX, "f1", b"first")
    job.drive.fail_moves["f1"] = 1
    build = FakeShotBuilder(shots_per_file={"first": 2})

    summary = job.run(build)

    assert (summary.built, summary.shots, summary.completed) == (1, 2, 0)
    row = job.footage()["f1"]
    assert row.status == FootageStatus.TAGGED
    assert "move of f1 failed" in (row.error or "")
    assert len(job.shots()) == 2

    summary = job.run(build)

    assert summary.poll.completed == 1
    assert job.footage()["f1"].status == FootageStatus.DONE
    assert len(build.calls) == 1
    assert len(job.shots()) == 2
    assert scratch_files(job.store) == []
