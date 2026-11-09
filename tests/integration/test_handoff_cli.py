import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from engine.cli import app
from engine.handoff import cli as handoff_cli

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
TOKEN = "a-long-random-handoff-token"

runner = CliRunner()


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """An initialized data dir; returns the recorded `uvicorn.run` calls instead of serving."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(REPO_CONFIG_DIR / "default.toml", config_dir / "default.toml")
    monkeypatch.setenv("ACE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ACE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HANDOFF_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code in {0, 1}

    calls: list[dict[str, Any]] = []

    def fake_run(served_app: FastAPI, host: str, port: int, **options: Any) -> None:
        calls.append({"app": served_app, "host": host, "port": port, **options})

    monkeypatch.setattr(handoff_cli.uvicorn, "run", fake_run)
    return calls


def test_serve_runs_the_hand_off_app_on_the_configured_address(
    served: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HANDOFF_TOKEN", TOKEN)

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0, result.output
    assert len(served) == 1
    assert (served[0]["host"], served[0]["port"]) == ("127.0.0.1", 8000)
    with TestClient(served[0]["app"]) as client:
        assert client.get("/").status_code == 401
        client.get("/login", params={"token": TOKEN})
        assert client.get("/").status_code == 200


def test_serve_host_and_port_options_override_the_config(
    served: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HANDOFF_TOKEN", TOKEN)

    result = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "8001"])

    assert result.exit_code == 0, result.output
    assert (served[0]["host"], served[0]["port"]) == ("0.0.0.0", 8001)


def test_serve_refuses_to_start_without_a_hand_off_token(served: list[dict[str, Any]]) -> None:
    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 1
    assert "HANDOFF_TOKEN" in result.output
    assert served == []
