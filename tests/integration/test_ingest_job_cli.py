import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from engine.cli import app
from engine.config import Settings, load_settings
from engine.db.enums import FootageStatus
from engine.db.models import FootageFileRow
from engine.db.session import create_db_engine, make_session_factory
from engine.ingest.models import ShotTags
from tests.fakes.drive import FakeDriveClient

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

runner = CliRunner()


class UnusedTagger:
    def tag_shot(self, frames: list[Path]) -> ShotTags:
        raise AssertionError("not called while build_shots is unbuilt")


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(REPO_CONFIG_DIR / "default.toml", config_dir / "default.toml")
    monkeypatch.setenv("ACE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ACE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ACE_DRIVE__FOOTAGE_INBOX_FOLDER_ID", "inbox")
    monkeypatch.setenv("ACE_DRIVE__FOOTAGE_PROCESSED_FOLDER_ID", "processed")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    return tmp_path / "data"


def footage_rows() -> list[FootageFileRow]:
    engine = create_db_engine(load_settings().db_url)
    with make_session_factory(engine)() as session:
        rows = list(session.scalars(select(FootageFileRow)))
    engine.dispose()
    return rows


def test_ingest_footage_runs_the_job_and_reports_counts(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = FakeDriveClient()
    drive.add_file("inbox", "f1", b"first")
    tagger_settings: list[Settings] = []

    def fake_tagger(settings: Settings) -> UnusedTagger:
        tagger_settings.append(settings)
        return UnusedTagger()

    monkeypatch.setattr("engine.ingest.cli.make_drive_client", lambda settings: drive)
    monkeypatch.setattr("engine.ingest.cli.make_tagger", fake_tagger)

    result = runner.invoke(app, ["ingest", "footage"])

    assert result.exit_code == 0, result.output
    assert "new=1" in result.output
    assert "downloaded=1" in result.output
    assert "built=0" in result.output
    [row] = footage_rows()
    assert row.status == FootageStatus.DOWNLOADED
    assert (row.error or "").startswith("NotImplementedError")
    assert Path(row.local_path or "").parent == data_dir.resolve() / "media" / "scratch"
    assert len(tagger_settings) == 1


def test_ingest_footage_without_folder_ids_explains_and_exits_1(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ACE_DRIVE__FOOTAGE_PROCESSED_FOLDER_ID")

    result = runner.invoke(app, ["ingest", "footage"])

    assert result.exit_code == 1
    assert "footage_processed_folder_id" in result.output


def test_ingest_footage_without_a_google_token_says_to_run_auth(data_dir: Path) -> None:
    result = runner.invoke(app, ["ingest", "footage"])

    assert result.exit_code == 1
    assert "ace auth google" in result.output
    assert footage_rows() == []


def test_ingest_footage_without_an_anthropic_key_explains_and_exits_1(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("engine.ingest.cli.make_drive_client", lambda settings: FakeDriveClient())

    result = runner.invoke(app, ["ingest", "footage"])

    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output


def test_ingest_footage_before_init_says_to_run_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(REPO_CONFIG_DIR / "default.toml", config_dir / "default.toml")
    monkeypatch.setenv("ACE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ACE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ACE_DRIVE__FOOTAGE_INBOX_FOLDER_ID", "inbox")
    monkeypatch.setenv("ACE_DRIVE__FOOTAGE_PROCESSED_FOLDER_ID", "processed")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["ingest", "footage"])

    assert result.exit_code == 1
    assert "ace init" in result.output
