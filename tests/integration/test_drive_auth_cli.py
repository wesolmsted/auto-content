import json
import shutil
import stat
from pathlib import Path

import pytest
from google.oauth2.credentials import Credentials
from typer.testing import CliRunner

from engine.cli import app
from engine.drive.google import DRIVE_SCOPES

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

runner = CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(REPO_CONFIG_DIR / "default.toml", config_dir / "default.toml")
    monkeypatch.setenv("ACE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ACE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_JSON", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path / "data"


def test_auth_google_saves_the_consented_token_privately(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_json = tmp_path / "client_secret.json"
    client_json.write_text("{}")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_JSON", str(client_json))
    flows: list[Path] = []

    def fake_flow(path: Path) -> Credentials:
        flows.append(path)
        return Credentials(
            token="access",
            refresh_token="refresh",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="id",
            client_secret="secret",
            scopes=DRIVE_SCOPES,
        )

    monkeypatch.setattr("engine.drive.cli.run_oauth_flow", fake_flow)

    result = runner.invoke(app, ["auth", "google"])

    assert result.exit_code == 0, result.output
    assert flows == [client_json]
    token_file = data_dir / "secrets" / "google_token.json"
    assert json.loads(token_file.read_text())["refresh_token"] == "refresh"
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert "refresh" not in result.output


def test_auth_google_without_client_json_explains_and_exits_1(data_dir: Path) -> None:
    result = runner.invoke(app, ["auth", "google"])

    assert result.exit_code == 1
    assert "GOOGLE_OAUTH_CLIENT_JSON" in result.output
    assert not (data_dir / "secrets" / "google_token.json").exists()
