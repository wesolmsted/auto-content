import re
import shutil
import stat
from pathlib import Path

import pytest
from sqlalchemy import inspect
from typer.testing import CliRunner

from engine.cli import app
from engine.config import load_settings
from engine.db.enums import FootageStatus
from engine.db.models import Base, FootageFileRow, SongRow
from engine.db.session import create_db_engine, make_session_factory, session_scope

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated config dir (repo defaults only) and data dir; returns the data dir."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(REPO_CONFIG_DIR / "default.toml", config_dir / "default.toml")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ACE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ACE_DATA_DIR", str(data_dir))
    monkeypatch.chdir(tmp_path)
    return data_dir


def test_init_creates_data_dirs_and_a_migrated_database(cli_env: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output

    for relative in ["db", "media/scratch", "media/shots", "media/renders", "secrets"]:
        assert (cli_env / relative).is_dir(), relative
    assert stat.S_IMODE((cli_env / "secrets").stat().st_mode) == 0o700

    engine = create_db_engine(load_settings().db_url)
    assert set(inspect(engine).get_table_names()) >= set(Base.metadata.tables)
    engine.dispose()


def test_init_is_idempotent(cli_env: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    second = runner.invoke(app, ["init"])
    assert second.exit_code == 0, second.output


def test_init_fails_when_ffmpeg_is_missing_but_still_sets_up_the_database(
    cli_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACE_TOOLS__FFMPEG", "ffmpeg-that-does-not-exist")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "ffmpeg-that-does-not-exist" in result.output
    assert (cli_env / "db" / "engine.sqlite3").exists()


def test_status_before_init_says_to_run_init(cli_env: Path) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "ace init" in result.output


def test_status_shows_counts_per_table_and_status(cli_env: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    engine = create_db_engine(load_settings().db_url)
    with session_scope(make_session_factory(engine)) as session:
        session.add_all(
            [
                FootageFileRow(drive_file_id="a", name="a.mp4"),
                FootageFileRow(drive_file_id="b", name="b.mp4"),
                FootageFileRow(drive_file_id="c", name="c.mp4", status=FootageStatus.FAILED),
                SongRow(title="Song", audio_path="song.wav"),
            ]
        )
    engine.dispose()

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    lines = {line.split()[0]: line for line in result.output.splitlines() if line.strip()}
    assert re.search(r"\bnew=2\b", lines["footage_files"])
    assert re.search(r"\bfailed=1\b", lines["footage_files"])
    assert re.search(r"\btotal=1\b", lines["songs"])
    assert re.search(r"\bempty\b", lines["renders"])
    assert set(Base.metadata.tables) <= set(lines)


@pytest.mark.parametrize(
    "argv",
    [
        ["auth", "meta"],
        ["auth", "tiktok"],
        ["ingest", "references"],
        ["ingest", "exports"],
        ["render", "batch", "--n", "2", "--dry-run"],
        ["render", "one", "--recipe", "rcp_0123456789ab", "--snippet", "1"],
        ["serve", "--port", "8001"],
        ["measure", "due"],
        ["posts", "link", "1", "ig_trial", "https://www.instagram.com/reel/abc/"],
        ["learn", "update"],
        ["report", "weekly"],
    ],
    ids=" ".join,
)
def test_unbuilt_commands_exist_and_exit_1(cli_env: Path, argv: list[str]) -> None:
    result = runner.invoke(app, argv)
    assert result.exit_code == 1, result.output
    assert "not implemented yet" in result.output
