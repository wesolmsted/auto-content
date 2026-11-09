from sqlalchemy.orm import Session

from engine.db.enums import FootageStatus
from engine.db.models import Base, FootageFileRow, SongRow
from engine.db.repository import table_counts


def test_empty_database_lists_every_table_with_zero_rows(db_session: Session) -> None:
    counts = table_counts(db_session)
    assert set(counts) == set(Base.metadata.tables)
    assert counts["footage_files"] == {}
    assert counts["songs"] == {"total": 0}


def test_counts_rows_per_status_and_totals_for_tables_without_status(
    db_session: Session,
) -> None:
    db_session.add_all(
        [
            FootageFileRow(drive_file_id="a", name="a.mp4"),
            FootageFileRow(drive_file_id="b", name="b.mp4"),
            FootageFileRow(drive_file_id="c", name="c.mp4", status=FootageStatus.FAILED),
            SongRow(title="Song", audio_path="song.wav"),
        ]
    )
    db_session.flush()
    counts = table_counts(db_session)
    assert counts["footage_files"] == {"new": 2, "failed": 1}
    assert counts["songs"] == {"total": 1}
    assert counts["renders"] == {}
