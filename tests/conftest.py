from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.db.migrate import upgrade_to_head
from engine.db.session import create_db_engine, make_session_factory
from tests.fixtures import make_fixtures


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


@pytest.fixture(scope="session")
def fixture_media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("fixture_media")


@pytest.fixture(scope="session")
def portrait_scenes_clip(fixture_media_dir: Path) -> Path:
    """1080x1920, 10 s, hard cuts at make_fixtures.PORTRAIT_SCENE_CHANGES_S (2.5, 5.0, 7.5)."""
    return make_fixtures.make_portrait_scenes(fixture_media_dir / "portrait_scenes.mp4")


@pytest.fixture(scope="session")
def landscape_motion_clip(fixture_media_dir: Path) -> Path:
    """1280x720, 6 s moving test pattern with a 440 Hz audio tone."""
    return make_fixtures.make_landscape_motion(fixture_media_dir / "landscape_motion.mp4")


@pytest.fixture(scope="session")
def click_track_wav(fixture_media_dir: Path) -> Path:
    """20 s click track at 120 BPM, downbeat accents every 4 beats, drop at 8.0 s."""
    return make_fixtures.make_click_track(fixture_media_dir / "click_120bpm.wav")
