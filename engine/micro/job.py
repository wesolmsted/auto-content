"""`ace micro render`: a micro render for each of today's open slots (DESIGN.md §7.10)."""

import logging
import random
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import MicroSettings, Settings
from engine.db.enums import MicroRenderStatus, MicroSetStatus, MicroSourceStatus
from engine.db.models import MicroRenderRow, MicroSetRow, MicroSourceRow
from engine.ffmpeg import FfmpegError
from engine.micro.edits import output_duration_s, sample_edit
from engine.micro.models import MicroEdit, MicroSourceMedia
from engine.micro.render import render_micro
from engine.micro.schedule import pick_set_and_video, slots_for_day, slots_to_fill, take_next_text
from engine.storage.media_store import MediaKind, MediaStore

logger = logging.getLogger(__name__)

JOB_NAME = "micro_render"

MicroRenderer = Callable[[MicroEdit, MicroSourceMedia, Path, Settings], list[str]]
EditSampler = Callable[[random.Random, MicroSettings, str], MicroEdit]
DurationOf = Callable[[MicroEdit, float], float]


class MicroRenderSummary(BaseModel):
    """`slots`: today's open slots; `no_source`: those left empty for lack of a ready video."""

    model_config = ConfigDict(frozen=True)

    slots: int = 0
    ready: int = 0
    failed: int = 0
    no_source: int = 0


def run_micro_renders(
    session: Session,
    store: MediaStore,
    settings: Settings,
    now: datetime,
    rng: random.Random,
    *,
    renderer: MicroRenderer = render_micro,
    edit_sampler: EditSampler = sample_edit,
    duration_of: DurationOf = output_duration_s,
) -> MicroRenderSummary:
    """Fill each of today's open slots with a micro render (DESIGN.md §7.10, Schedule).

    A slot with any micro render, failed included, is filled. Each slot takes the least
    recently used `active` set with a `ready` video, and that set's least recently used
    video; both get `last_used_at = scheduled_for`. Rendering errors are recorded on the row
    and never propagate. Commits after each state change.
    """
    timezone = settings.notify.timezone
    today = slots_for_day(now, settings.micro.post_times, timezone)
    filled = set(
        session.scalars(
            select(MicroRenderRow.scheduled_for).where(MicroRenderRow.scheduled_for.in_(today))
        )
    )
    open_slots = slots_to_fill(
        now, settings.micro.post_times, timezone, settings.micro.late_grace_minutes, filled
    )
    sets = session.scalars(
        select(MicroSetRow).where(MicroSetRow.status == MicroSetStatus.ACTIVE)
    ).all()
    videos = session.scalars(
        select(MicroSourceRow).where(MicroSourceRow.status == MicroSourceStatus.READY)
    ).all()

    ready = failed = 0
    for number, slot in enumerate(open_slots):
        picked = pick_set_and_video(sets, videos)
        if picked is None:
            logger.warning(
                "no active micro set with a ready video: %d slot(s) left open",
                len(open_slots) - number,
                extra={"job": JOB_NAME},
            )
            return MicroRenderSummary(
                slots=len(open_slots),
                ready=ready,
                failed=failed,
                no_source=len(open_slots) - number,
            )
        micro_set, video = picked
        row = _create_render(session, micro_set, video, slot, rng, settings, edit_sampler)
        status = _render(session, row, video, store, settings, renderer, duration_of)
        if status is MicroRenderStatus.READY:
            ready += 1
        else:
            failed += 1
    return MicroRenderSummary(slots=len(open_slots), ready=ready, failed=failed)


def _create_render(
    session: Session,
    micro_set: MicroSetRow,
    video: MicroSourceRow,
    slot: datetime,
    rng: random.Random,
    settings: Settings,
    edit_sampler: EditSampler,
) -> MicroRenderRow:
    text = take_next_text(micro_set)
    micro_set.last_used_at = slot
    video.last_used_at = slot
    edit = edit_sampler(rng, settings.micro, text)
    edit = edit.model_copy(
        update={
            "text_cutoff_s": micro_set.text_cutoff_s,
            "mirror": edit.mirror and micro_set.mirror_allowed,
        }
    )
    row = MicroRenderRow(
        source_id=video.id,
        scheduled_for=slot,
        edit_json=edit.model_dump(mode="json"),
        text=text,
        caption=micro_set.song_title or micro_set.name,
        status=MicroRenderStatus.RENDERING,
    )
    session.add(row)
    session.commit()
    return row


def _render(
    session: Session,
    row: MicroRenderRow,
    video: MicroSourceRow,
    store: MediaStore,
    settings: Settings,
    renderer: MicroRenderer,
    duration_of: DurationOf,
) -> MicroRenderStatus:
    """Render and validate `row`, retrying up to `render.max_attempts`; `ready` or `failed`."""
    log_extra = {"job": JOB_NAME, "row_id": row.id}
    edit = MicroEdit.model_validate(row.edit_json)
    out_path = store.path_for(MediaKind.RENDERS, f"micro_{row.id}.mp4")

    for attempt in range(1, settings.render.max_attempts + 1):
        row.attempts = attempt
        try:
            source = MicroSourceMedia(
                path=Path(video.local_path or ""),
                duration_s=video.duration_s or 0.0,
                has_audio=bool(video.has_audio),
            )
            row.ffmpeg_argv_json = renderer(edit, source, out_path, settings)
            duration_s = duration_of(edit, source.duration_s)
        except Exception as error:
            logger.exception("micro render attempt %d failed", attempt, extra=log_extra)
            if isinstance(error, FfmpegError):
                row.ffmpeg_argv_json = error.argv
            row.error = str(error)
            session.commit()
            continue
        row.file_path = str(out_path)
        row.duration_s = duration_s
        row.error = None
        row.status = MicroRenderStatus.READY
        session.commit()
        logger.info("micro render ready", extra=log_extra)
        return row.status

    row.status = MicroRenderStatus.FAILED
    session.commit()
    return row.status
