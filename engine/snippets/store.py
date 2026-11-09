"""Reads and writes of the `songs` and `snippets` tables."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.db.enums import SnippetType
from engine.db.models import SnippetRow, SongRow
from engine.snippets.models import SongAnalysis


@dataclass(frozen=True)
class SnippetSummary:
    id: int
    song_title: str
    snippet_type: SnippetType
    start_s: float
    end_s: float
    drop_s: float | None
    label: str | None
    active: bool


def find_song(session: Session, audio_path: Path) -> tuple[int, SongAnalysis] | None:
    """The analyzed song stored for this audio file (matched on its resolved path), if any."""
    row = session.scalars(
        select(SongRow)
        .where(SongRow.audio_path == str(audio_path.resolve()))
        .where(SongRow.analyzed_at.is_not(None))
    ).first()
    if row is None or row.duration_s is None or row.bpm is None:
        return None
    analysis = SongAnalysis(
        duration_s=row.duration_s,
        bpm=row.bpm,
        beats=tuple(row.beats_json),
        downbeats=tuple(row.downbeats_json),
    )
    return row.id, analysis


def add_song(session: Session, audio_path: Path, analysis: SongAnalysis) -> int:
    row = SongRow(
        title=audio_path.stem,
        audio_path=str(audio_path.resolve()),
        duration_s=analysis.duration_s,
        bpm=analysis.bpm,
        beats_json=list(analysis.beats),
        downbeats_json=list(analysis.downbeats),
        analyzed_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row.id


def add_snippet(
    session: Session,
    song_id: int,
    snippet_type: SnippetType,
    start_s: float,
    end_s: float,
    drop_s: float | None,
    label: str | None,
) -> int:
    """Insert a snippet. `drop_s` is an absolute song time, like `start_s` and `end_s`."""
    row = SnippetRow(
        song_id=song_id,
        snippet_type=snippet_type,
        start_s=start_s,
        end_s=end_s,
        drop_s=drop_s,
        label=label,
    )
    session.add(row)
    session.flush()
    return row.id


def list_snippets(session: Session) -> list[SnippetSummary]:
    rows = session.execute(
        select(SnippetRow, SongRow.title)
        .join(SongRow, SnippetRow.song_id == SongRow.id)
        .order_by(SnippetRow.id)
    ).all()
    return [
        SnippetSummary(
            id=snippet.id,
            song_title=title,
            snippet_type=snippet.snippet_type,
            start_s=snippet.start_s,
            end_s=snippet.end_s,
            drop_s=snippet.drop_s,
            label=snippet.label,
            active=snippet.active,
        )
        for snippet, title in rows
    ]
