from pathlib import Path

import pytest

from engine.ffmpeg import FfmpegError, probe, run_ffmpeg


def test_failed_run_raises_with_argv_returncode_and_stderr_tail(tmp_path: Path) -> None:
    missing = tmp_path / "missing.mp4"
    with pytest.raises(FfmpegError) as raised:
        run_ffmpeg(["-i", str(missing), str(tmp_path / "out.mp4")])
    error = raised.value
    assert error.returncode != 0
    assert error.argv[0] == "ffmpeg"
    assert str(missing) in error.argv
    assert "No such file or directory" in error.stderr_tail
    assert "No such file or directory" in str(error)


def test_successful_run_writes_its_output(tmp_path: Path) -> None:
    out = tmp_path / "tone.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=1.5", str(out)])
    info = probe(out)
    assert info.duration_s == pytest.approx(1.5, abs=0.01)
    assert info.has_audio is True
    assert info.width is None
    assert info.fps is None


def test_probe_reads_video_geometry_and_audio(landscape_motion_clip: Path) -> None:
    info = probe(landscape_motion_clip)
    assert (info.width, info.height) == (1280, 720)
    assert info.fps == pytest.approx(30.0)
    assert info.rotation == 0
    assert info.has_audio is True


def test_probe_reports_display_rotation(tmp_path: Path, landscape_motion_clip: Path) -> None:
    rotated = tmp_path / "rotated.mp4"
    run_ffmpeg(
        ["-display_rotation", "90", "-i", str(landscape_motion_clip), "-c", "copy", str(rotated)]
    )
    assert abs(probe(rotated).rotation) == 90


def test_probe_of_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FfmpegError):
        probe(tmp_path / "missing.mp4")
