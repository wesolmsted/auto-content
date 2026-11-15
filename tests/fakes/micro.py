import random
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.config import MicroSettings, Settings
from engine.db.enums import MicroSetStatus, MicroSourceStatus
from engine.db.models import MicroSetRow, MicroSourceRow
from engine.ffmpeg import FfmpegError
from engine.micro.models import MicroEdit, MicroSourceMedia, MicroTextPosition


def fixed_edit(rng: random.Random, settings: MicroSettings, text: str) -> MicroEdit:
    """A stand-in for `sample_edit`: the same small edit every time, with `text`.
    Output duration (see `fixed_duration_s`) is the source duration minus 0.5 s."""
    return MicroEdit(
        trim_start_s=0.2,
        trim_end_s=0.3,
        speed=1.0,
        zoom=1.03,
        crop_x=0.5,
        crop_y=0.5,
        mirror=False,
        brightness=0.0,
        contrast=1.0,
        saturation=1.0,
        hue_degrees=0.0,
        text=text,
        text_position=MicroTextPosition.TOP,
        text_size_fraction=0.05,
        text_y_offset=0.0,
    )


def fixed_duration_s(edit: MicroEdit, source_duration_s: float) -> float:
    return (source_duration_s - edit.trim_start_s - edit.trim_end_s) / edit.speed


class FakeMicroRenderer:
    """A stand-in for `render_micro`: writes a small file and returns a fake argv. The first
    `failures` calls raise FfmpegError instead."""

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[MicroEdit, MicroSourceMedia, Path]] = []

    def __call__(
        self, edit: MicroEdit, source: MicroSourceMedia, out_path: Path, settings: Settings
    ) -> list[str]:
        self.calls.append((edit, source, out_path))
        argv = ["ffmpeg", "-i", str(source.path), str(out_path)]
        if self.failures > 0:
            self.failures -= 1
            raise FfmpegError(argv, 1, "boom")
        out_path.write_bytes(b"micro video")
        return argv


def add_micro_set(
    session: Session,
    song_title: str = "You Dont Dance",
    texts: tuple[str, ...] = ("POV: the drop", "wait for it"),
    status: MicroSetStatus = MicroSetStatus.ACTIVE,
) -> MicroSetRow:
    count = session.scalar(select(func.count()).select_from(MicroSetRow)) or 0
    micro_set = MicroSetRow(
        drive_folder_id=f"folder{count + 1}",
        name=f"{song_title.lower()}_micro",
        song_title=song_title,
        text_options_json=list(texts),
        status=status,
    )
    session.add(micro_set)
    session.commit()
    return micro_set


def add_micro_video(
    session: Session,
    micro_set: MicroSetRow,
    name: str = "clip.mp4",
    duration_s: float = 10.0,
    has_audio: bool = True,
    status: MicroSourceStatus = MicroSourceStatus.READY,
) -> MicroSourceRow:
    count = session.scalar(select(func.count()).select_from(MicroSourceRow)) or 0
    video = MicroSourceRow(
        set_id=micro_set.id,
        drive_file_id=f"file{count + 1}",
        md5=f"md5-{count + 1}",
        name=name,
        local_path=f"/media/micro/micro_source_{count + 1}.mp4",
        duration_s=duration_s,
        has_audio=has_audio,
        status=status,
    )
    session.add(video)
    session.commit()
    return video
