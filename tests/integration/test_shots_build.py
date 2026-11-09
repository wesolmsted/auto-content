"""build_shots against the generated fixtures (ground truth in tests/fixtures/make_fixtures.py)."""

from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
import pytest

from engine.config import IngestSettings, load_settings
from engine.db.enums import Orientation
from engine.ffmpeg import probe, run_ffmpeg, run_tool
from engine.ingest.models import Shot, ShotTags
from engine.ingest.shots import build_shots
from engine.storage.media_store import LocalMediaStore, MediaKind
from engine.vision.tagger import VisionTagError
from tests.fakes.vision import DEFAULT_TAGS, FakeVisionTagger
from tests.fixtures import make_fixtures as fx

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"
ONE_FRAME_S = 1 / fx.VIDEO_FPS
BLUE_BGR = (0x8A, 0x3A, 0x1E)


class FrameCheckingTagger(FakeVisionTagger):
    """Also keeps the mean colour of each frame, read while the frames still exist."""

    def __init__(self) -> None:
        super().__init__()
        self.frame_colors: list[list[tuple[float, float, float]]] = []

    def tag_shot(self, frames: list[Path]) -> ShotTags:
        colors = []
        for frame in frames:
            image = cv2.imread(str(frame))
            assert image is not None, f"{frame} is not a readable image"
            colors.append(tuple(float(channel) for channel in image.reshape(-1, 3).mean(axis=0)))
        self.frame_colors.append(colors)
        return super().tag_shot(frames)


@pytest.fixture
def settings() -> IngestSettings:
    return load_settings(REPO_CONFIG, env_file=None).ingest


@pytest.fixture
def store(tmp_path: Path) -> LocalMediaStore:
    return LocalMediaStore(tmp_path / "media")


class PortraitBuild(NamedTuple):
    shots: list[Shot]
    tagger: FrameCheckingTagger
    store: LocalMediaStore


@pytest.fixture(scope="module")
def portrait_build(
    portrait_scenes_clip: Path, tmp_path_factory: pytest.TempPathFactory
) -> PortraitBuild:
    """The portrait clip built once with default settings, shared by the read-only tests."""
    tagger = FrameCheckingTagger()
    store = LocalMediaStore(tmp_path_factory.mktemp("portrait_media"))
    ingest = load_settings(REPO_CONFIG, env_file=None).ingest
    return PortraitBuild(build_shots(portrait_scenes_clip, 7, tagger, store, ingest), tagger, store)


@pytest.fixture(scope="module")
def rotated_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A phone-style clip: coded 1280x720 with a 90° display rotation, so it shows as portrait."""
    path = tmp_path_factory.mktemp("rotated") / "rotated.mp4"
    run_ffmpeg(
        [
            *["-display_rotation", "90", "-f", "lavfi"],
            *["-i", "testsrc2=size=1280x720:rate=30:duration=2"],
            *["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        ]
    )
    return path


def mean_color(image: np.ndarray) -> tuple[float, ...]:
    return tuple(float(channel) for channel in image.reshape(-1, 3).mean(axis=0))


def video_codec(path: Path) -> str:
    argv = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries"]
    completed = run_tool([*argv, "stream=codec_name", "-of", "csv=p=0", str(path)])
    return completed.stdout.strip()


def first_and_last_frames(video: Path) -> tuple[np.ndarray, np.ndarray]:
    capture = cv2.VideoCapture(str(video))
    frames = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    capture.release()
    return frames[0], frames[-1]


def test_portrait_clip_splits_into_its_four_scenes(
    portrait_build: PortraitBuild, settings: IngestSettings
) -> None:
    shots, tagger, _ = portrait_build

    boundaries = [0.0, *fx.PORTRAIT_SCENE_CHANGES_S, fx.PORTRAIT_DURATION_S]
    assert [shot.start_s for shot in shots] == pytest.approx(boundaries[:-1], abs=ONE_FRAME_S)
    assert [shot.end_s for shot in shots] == pytest.approx(boundaries[1:], abs=ONE_FRAME_S)
    for shot in shots:
        assert shot.id is None
        assert shot.footage_file_id == 7
        assert (shot.width, shot.height) == fx.PORTRAIT_SIZE
        assert shot.orientation == Orientation.PORTRAIT
        assert shot.fps == pytest.approx(fx.VIDEO_FPS)
        assert shot.tags == DEFAULT_TAGS
        assert 0.0 <= shot.quality_score <= 1.0
    assert len(tagger.calls) == 4
    assert all(len(frames) == settings.frames_per_shot for frames in tagger.calls)


def test_tagger_frames_come_from_inside_their_shot(portrait_build: PortraitBuild) -> None:
    tagger = portrait_build.tagger
    blue_shot_frames = tagger.frame_colors[2]
    for color in blue_shot_frames:
        assert color == pytest.approx(BLUE_BGR, abs=6)
    for shot_index in (1, 3):
        assert tagger.frame_colors[shot_index][0] != pytest.approx(BLUE_BGR, abs=20)


def test_proxies_are_frame_accurate_9_16_h264_files_in_the_shots_dir(
    portrait_build: PortraitBuild,
) -> None:
    shots, _, store = portrait_build

    shots_dir = store.path_for(MediaKind.SHOTS, "probe").parent
    assert len({shot.proxy_path for shot in shots}) == 4
    for shot in shots:
        assert shot.proxy_path.parent == shots_dir
        info = probe(shot.proxy_path)
        assert (info.width, info.height) == (1080, 1920)
        assert video_codec(shot.proxy_path) == "h264"
        assert not info.has_audio
        assert info.duration_s == pytest.approx(shot.duration_s, abs=ONE_FRAME_S)
        assert shot.thumbnail_path is not None
        assert shot.thumbnail_path.parent == shots_dir
        assert cv2.imread(str(shot.thumbnail_path)) is not None

    first, last = first_and_last_frames(shots[2].proxy_path)
    assert mean_color(first) == pytest.approx(BLUE_BGR, abs=6)
    assert mean_color(last) == pytest.approx(BLUE_BGR, abs=6)


def test_moving_footage_scores_more_motion_than_a_static_shot(
    portrait_build: PortraitBuild,
    landscape_motion_clip: Path,
    store: LocalMediaStore,
    settings: IngestSettings,
) -> None:
    portrait = portrait_build.shots
    [landscape] = build_shots(landscape_motion_clip, 2, FakeVisionTagger(), store, settings)

    static_blue = portrait[2]
    assert static_blue.motion_score == pytest.approx(0.0, abs=0.01)
    assert landscape.motion_score > static_blue.motion_score + 0.05
    assert landscape.start_s == 0.0
    assert landscape.end_s == pytest.approx(fx.LANDSCAPE_DURATION_S, abs=ONE_FRAME_S)
    assert (landscape.width, landscape.height) == fx.LANDSCAPE_SIZE
    assert landscape.orientation == Orientation.LANDSCAPE
    proxy = probe(landscape.proxy_path)
    assert (proxy.width, proxy.height) == (404, 720)


def test_flat_colour_shot_scores_lower_quality_than_a_detailed_one(
    portrait_build: PortraitBuild,
) -> None:
    shots = portrait_build.shots
    detailed, flat = shots[0], shots[2]
    assert flat.quality_score < detailed.quality_score


def test_long_scenes_are_split_and_short_ones_dropped(
    landscape_motion_clip: Path, store: LocalMediaStore, settings: IngestSettings
) -> None:
    split = build_shots(
        landscape_motion_clip,
        1,
        FakeVisionTagger(),
        store,
        settings.model_copy(update={"max_shot_s": 2.5}),
    )
    assert [(shot.start_s, shot.end_s) for shot in split] == pytest.approx(
        [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)], abs=ONE_FRAME_S
    )
    assert len({shot.proxy_path for shot in split}) == 3

    dropped = build_shots(
        landscape_motion_clip,
        1,
        FakeVisionTagger(),
        store,
        settings.model_copy(update={"min_shot_s": 7.0}),
    )
    assert dropped == []


def test_rotated_phone_footage_is_recorded_in_display_orientation(
    rotated_clip: Path, store: LocalMediaStore, settings: IngestSettings
) -> None:
    [shot] = build_shots(rotated_clip, 1, FakeVisionTagger(), store, settings)
    assert (shot.width, shot.height) == (720, 1280)
    assert shot.orientation == Orientation.PORTRAIT
    proxy = probe(shot.proxy_path)
    assert (proxy.width, proxy.height) == (720, 1280)
    assert proxy.rotation == 0


def test_tagging_failure_propagates_and_returns_no_partial_result(
    landscape_motion_clip: Path, store: LocalMediaStore, settings: IngestSettings
) -> None:
    tagger = FakeVisionTagger(error=VisionTagError("model down"))
    with pytest.raises(VisionTagError, match="model down"):
        build_shots(
            landscape_motion_clip,
            1,
            tagger,
            store,
            settings.model_copy(update={"max_shot_s": 2.5}),
        )
    assert len(tagger.calls) == 1
    assert not any(store.path_for(MediaKind.SCRATCH, "probe").parent.iterdir())
