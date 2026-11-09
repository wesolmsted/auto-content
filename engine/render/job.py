"""Execute one RenderPlan: a `renders` row from `rendering` to `ready` or `failed` (DESIGN.md §7.4).

Notifications are not sent here; the caller does that for `ready` renders.
"""

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from sqlalchemy.orm import Session

from engine.config import Settings
from engine.db.enums import Platform, RenderStatus
from engine.db.models import RenderRow, SnippetRow, SongRow
from engine.ffmpeg import FfmpegError
from engine.recipes.models import RenderPlan
from engine.render.ffmpeg import render, validate
from engine.storage.media_store import MediaKind, MediaStore

logger = logging.getLogger(__name__)

JOB_NAME = "render"


def captions_for(
    targets: Sequence[Platform],
    templates: Mapping[Platform, str],
    song_title: str,
    plan_captions: Mapping[Platform, str],
) -> dict[Platform, str]:
    """One caption per target: the plan's own caption if it has one, else the config template."""
    return {
        target: plan_captions.get(target) or templates[target].format(song=song_title)
        for target in targets
    }


def execute_plan(session: Session, plan: RenderPlan, store: MediaStore, settings: Settings) -> int:
    """Create the plan's `renders` row, render and validate it, and return the row id.

    Each failed try is recorded on the row (`error`, `attempts`) and retried up to
    `render.max_attempts`, after which the row is `failed`. Rendering errors never propagate.
    """
    snippet = session.get_one(SnippetRow, plan.snippet_id)
    song = session.get_one(SongRow, snippet.song_id)
    row = RenderRow(
        recipe_id=plan.recipe_id,
        snippet_id=plan.snippet_id,
        timeline_json=plan.timeline.model_dump(mode="json"),
        shot_ids_json=plan.timeline.shot_ids,
        targets_json=[target.value for target in plan.targets],
        captions_json={
            target.value: caption
            for target, caption in captions_for(
                plan.targets, settings.captions.templates, song.title, plan.captions
            ).items()
        },
        status=RenderStatus.RENDERING,
    )
    session.add(row)
    session.commit()
    log_extra = {"job": JOB_NAME, "row_id": row.id}
    out_path = store.path_for(MediaKind.RENDERS, f"render_{row.id}.mp4")

    for attempt in range(1, settings.render.max_attempts + 1):
        row.attempts = attempt
        try:
            argv = render(
                plan.timeline,
                Path(song.audio_path),
                snippet.start_s,
                out_path,
                settings.render,
                ffmpeg=settings.tools.ffmpeg,
            )
            row.ffmpeg_argv_json = argv
            validate(
                out_path, plan.timeline.duration_s, settings.render, ffprobe=settings.tools.ffprobe
            )
        except Exception as error:
            logger.exception("render attempt %d failed", attempt, extra=log_extra)
            if isinstance(error, FfmpegError):
                row.ffmpeg_argv_json = error.argv
            row.error = str(error)
            session.commit()
            continue
        row.file_path = str(out_path)
        row.duration_s = plan.timeline.duration_s
        row.error = None
        row.status = RenderStatus.READY
        session.commit()
        logger.info("render ready", extra=log_extra)
        return row.id

    row.status = RenderStatus.FAILED
    session.commit()
    return row.id
