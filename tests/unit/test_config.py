import shutil
from pathlib import Path

import pytest

from engine.config import load_settings
from engine.db.enums import Orientation, Platform

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "config"
    directory.mkdir()
    shutil.copy(REPO_CONFIG_DIR / "default.toml", directory / "default.toml")
    return directory


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["ACE_CONFIG_DIR", "ACE_DATA_DIR", "ACE_RENDER__FPS", "ANTHROPIC_API_KEY"]:
        monkeypatch.delenv(name, raising=False)


def test_each_fixed_recipe_runs_in_both_orientations() -> None:
    settings = load_settings(REPO_CONFIG_DIR, env_file=None)
    assert settings.recipes.space.footage_orientation == (
        Orientation.PORTRAIT,
        Orientation.LANDSCAPE,
    )
    orientations_by_style: dict[str, set[Orientation]] = {}
    for recipe in settings.recipes.fixed:
        style = recipe.model_copy(update={"footage_orientation": Orientation.PORTRAIT}).id
        orientations_by_style.setdefault(style, set()).add(recipe.footage_orientation)
    assert len(orientations_by_style) == 3
    assert all(
        orientations == {Orientation.PORTRAIT, Orientation.LANDSCAPE}
        for orientations in orientations_by_style.values()
    )


def write_local(config_dir: Path, text: str) -> None:
    (config_dir / "local.toml").write_text(text)


def test_repo_defaults_load_with_six_distinct_fixed_recipes() -> None:
    settings = load_settings(REPO_CONFIG_DIR, env_file=None)
    assert settings.vision.model == "claude-sonnet-5-5"
    assert settings.render.fps == 30
    assert settings.data_dir == Path("data")
    assert len({recipe.id for recipe in settings.recipes.fixed}) == 6
    assert settings.captions.templates[Platform.TIKTOK] == "{song}"
    assert settings.anthropic_api_key is None


def test_local_toml_overrides_defaults_key_by_key(config_dir: Path) -> None:
    write_local(config_dir, '[render]\nfps = 24\n\n[drive]\nfootage_inbox_folder_id = "inbox123"\n')
    settings = load_settings(config_dir, env_file=None)
    assert settings.render.fps == 24
    assert settings.render.width == 1080
    assert settings.drive.footage_inbox_folder_id == "inbox123"
    assert settings.drive.max_retries == 5


def test_env_overrides_local_toml(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_local(config_dir, 'data_dir = "/from/local"\n\n[render]\nfps = 24\nwidth = 720\n')
    monkeypatch.setenv("ACE_RENDER__FPS", "60")
    monkeypatch.setenv("ACE_DATA_DIR", "/from/env")
    settings = load_settings(config_dir, env_file=None)
    assert settings.render.fps == 60
    assert settings.render.width == 720
    assert settings.data_dir == Path("/from/env")


def test_env_file_ranks_below_real_env(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-dotenv\nACE_RENDER__FPS=25\nUNRELATED=1\n")
    assert load_settings(config_dir, env_file=env_file).render.fps == 25
    monkeypatch.setenv("ACE_RENDER__FPS", "50")
    settings = load_settings(config_dir, env_file=env_file)
    assert settings.render.fps == 50
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "from-dotenv"


def test_secrets_come_from_their_plain_env_names(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("NTFY_TOPIC", "topic-xyz")
    settings = load_settings(config_dir, env_file=None)
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-test"
    assert settings.ntfy_topic is not None
    assert settings.ntfy_topic.get_secret_value() == "topic-xyz"
    assert "topic-xyz" not in repr(settings)


def test_config_dir_defaults_to_ace_config_dir_env(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_local(config_dir, "[cadence]\nrenders_per_day = 9\n")
    monkeypatch.setenv("ACE_CONFIG_DIR", str(config_dir))
    assert load_settings(env_file=None).cadence.renders_per_day == 9


@pytest.mark.parametrize(
    "local_text",
    ["unknown_key = 1\n", "[render]\nframe_rate = 24\n", "[nonsense]\nx = 1\n"],
    ids=["top_level", "nested", "section"],
)
def test_unknown_keys_in_local_toml_are_rejected(config_dir: Path, local_text: str) -> None:
    write_local(config_dir, local_text)
    with pytest.raises(ValueError):
        load_settings(config_dir, env_file=None)


def test_derived_paths_live_under_data_dir(config_dir: Path) -> None:
    write_local(config_dir, 'data_dir = "/srv/ace"\n')
    settings = load_settings(config_dir, env_file=None)
    assert settings.db_url == "sqlite:////srv/ace/db/engine.sqlite3"
    assert settings.media_dir == Path("/srv/ace/media")
    assert settings.secrets_dir == Path("/srv/ace/secrets")


def test_database_url_overrides_the_sqlite_default(config_dir: Path) -> None:
    write_local(config_dir, 'database_url = "postgresql://db/ace"\n')
    assert load_settings(config_dir, env_file=None).db_url == "postgresql://db/ace"


def test_empty_values_copied_from_env_example_are_ignored(config_dir: Path, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ACE_DATA_DIR=\nANTHROPIC_API_KEY=\nNTFY_SERVER=\n")
    settings = load_settings(config_dir, env_file=env_file)
    assert settings.data_dir == Path("data")
    assert settings.anthropic_api_key is None
    assert settings.ntfy_server == "https://ntfy.sh"


def test_ntfy_server_defaults_to_ntfy_sh(config_dir: Path) -> None:
    assert load_settings(config_dir, env_file=None).ntfy_server == "https://ntfy.sh"


def test_google_client_json_path_expands_the_home_directory(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_JSON", "~/secrets/client.json")
    settings = load_settings(config_dir, env_file=None)
    assert settings.google_oauth_client_json == tmp_path / "secrets" / "client.json"
