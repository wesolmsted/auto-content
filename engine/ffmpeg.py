"""The one wrapper for calling ffmpeg and ffprobe (DESIGN.md §9: Subprocesses)."""

import json
import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

STDERR_TAIL_LINES = 20


class FfmpegError(RuntimeError):
    """An ffmpeg/ffprobe run failed. The message ends with the tail of its stderr."""

    def __init__(self, argv: Sequence[str], returncode: int, stderr_tail: str) -> None:
        super().__init__(f"{argv[0]} exited with {returncode}:\n{stderr_tail}")
        self.argv = list(argv)
        self.returncode = returncode
        self.stderr_tail = stderr_tail


class MediaInfo(BaseModel):
    """What `probe` reports. Video fields are None for audio-only files.

    `width`/`height` are the coded size; `rotation` is the display rotation in degrees.
    """

    model_config = ConfigDict(frozen=True)

    duration_s: float
    width: int | None
    height: int | None
    fps: float | None
    rotation: int
    has_audio: bool


def run_tool(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run `argv`, logging it and raising FfmpegError with the stderr tail on failure."""
    logger.info("running %s", argv[0], extra={"argv": list(argv)})
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise FfmpegError(argv, 127, f"{argv[0]} not found on PATH") from error
    if completed.returncode != 0:
        tail = "\n".join(completed.stderr.strip().splitlines()[-STDERR_TAIL_LINES:])
        raise FfmpegError(argv, completed.returncode, tail)
    return completed


def run_ffmpeg(args: Sequence[str], ffmpeg: str = "ffmpeg") -> subprocess.CompletedProcess[str]:
    """Run ffmpeg non-interactively, overwriting outputs."""
    return run_tool([ffmpeg, "-hide_banner", "-nostdin", "-y", *args])


def probe(path: Path, ffprobe: str = "ffprobe") -> MediaInfo:
    completed = run_tool(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    report = json.loads(completed.stdout)
    streams = report.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    duration_s = float(report["format"]["duration"])
    if video is None:
        return MediaInfo(
            duration_s=duration_s,
            width=None,
            height=None,
            fps=None,
            rotation=0,
            has_audio=has_audio,
        )
    return MediaInfo(
        duration_s=duration_s,
        width=int(video["width"]),
        height=int(video["height"]),
        fps=_parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate", "0/1")),
        rotation=_rotation(video),
        has_audio=has_audio,
    )


def _parse_rate(rate: str) -> float | None:
    numerator, _, denominator = rate.partition("/")
    if not denominator:
        return float(numerator)
    if float(denominator) == 0:
        return None
    return float(numerator) / float(denominator)


def _rotation(video_stream: dict[str, Any]) -> int:
    for side_data in video_stream.get("side_data_list", []):
        if "rotation" in side_data:
            return int(side_data["rotation"])
    return int(video_stream.get("tags", {}).get("rotate", 0))
