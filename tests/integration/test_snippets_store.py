from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.db.enums import SnippetType
from engine.db.models import SnippetRow, SongRow
from engine.snippets import store
from engine.snippets.models import SongAnalysis

ANALYSIS = SongAnalysis(duration_s=4.0, bpm=120.0, beats=(0.0, 0.5, 1.0), downbeats=(0.0,))


def test_song_round_trips_and_is_found_by_its_resolved_path(
    db_session: Session, tmp_path: Path
) -> None:
    audio = tmp_path / "My Song.wav"
    song_id = store.add_song(db_session, audio, ANALYSIS)
    db_session.commit()

    found = store.find_song(db_session, tmp_path / "." / "My Song.wav")

    assert found == (song_id, ANALYSIS)
    row = db_session.get(SongRow, song_id)
    assert row is not None
    assert row.title == "My Song"
    assert row.audio_path == str(audio.resolve())
    assert row.analyzed_at is not None


def test_unknown_song_is_not_found(db_session: Session, tmp_path: Path) -> None:
    store.add_song(db_session, tmp_path / "a.wav", ANALYSIS)

    assert store.find_song(db_session, tmp_path / "b.wav") is None


def test_snippets_are_added_and_listed_with_their_song(db_session: Session, tmp_path: Path) -> None:
    song_id = store.add_song(db_session, tmp_path / "Song.wav", ANALYSIS)
    first = store.add_snippet(db_session, song_id, SnippetType.DROP, 1.0, 3.0, 2.0, "chorus")
    second = store.add_snippet(db_session, song_id, SnippetType.HOOK, 0.0, 2.0, None, None)

    listed = store.list_snippets(db_session)

    assert [snippet.id for snippet in listed] == [first, second]
    assert listed[0] == store.SnippetSummary(
        id=first,
        song_title="Song",
        snippet_type=SnippetType.DROP,
        start_s=1.0,
        end_s=3.0,
        drop_s=2.0,
        label="chorus",
        active=True,
    )
    assert listed[1].drop_s is None
    rows = db_session.scalars(select(SnippetRow)).all()
    assert {row.song_id for row in rows} == {song_id}
