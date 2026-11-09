from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.db.migrate import upgrade_to_head
from engine.db.session import create_db_engine, make_session_factory


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    """A fresh SQLite database with every migration applied."""
    url = f"sqlite:///{tmp_path / 'test.sqlite3'}"
    upgrade_to_head(url)
    return url


@pytest.fixture
def db_session(db_url: str) -> Iterator[Session]:
    engine = create_db_engine(db_url)
    with make_session_factory(engine)() as session:
        yield session
    engine.dispose()
