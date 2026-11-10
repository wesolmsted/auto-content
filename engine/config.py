"""Settings for the whole app (DESIGN.md §8).

Precedence: environment variables (and `.env`) > `config/local.toml` > `config/default.toml`.
`config/default.toml` is the single source of defaults: every section key is required there.
Non-secret keys can be overridden from the environment as `ACE_<SECTION>__<KEY>`, e.g.
`ACE_RENDER__FPS=24` or `ACE_DATA_DIR=/data`. Secrets come only from the environment.
"""

import os
import tomllib
from datetime import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from engine.db.enums import Orientation, Platform, SnippetType
from engine.recipes.models import (
    CutRate,
    Effect,
    FootageCategory,
    Look,
    OnScreenText,
    OpeningShot,
    Recipe,
)

DEFAULT_CONFIG_DIR = Path("config")
DEFAULT_ENV_FILE = Path(".env")


class Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DriveSettings(Section):
    """Folder ids are personal, so they belong in `config/local.toml` or the environment."""

    footage_inbox_folder_id: str | None = None
    footage_processed_folder_id: str | None = None
    references_inbox_folder_id: str | None = None
    references_processed_folder_id: str | None = None
    exports_inbox_folder_id: str | None = None
    exports_processed_folder_id: str | None = None
    keep_reference_originals: bool
    download_chunk_mb: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    backoff_base_s: float = Field(ge=0)


class IngestSettings(Section):
    scene_threshold: float = Field(gt=0)
    min_shot_s: float = Field(gt=0)
    max_shot_s: float = Field(gt=0)
    frames_per_shot: int = Field(gt=0)
    proxy_max_dim: int = Field(gt=0)
    max_attempts: int = Field(gt=0)


class TagVocabulary(Section):
    """Closed vocabulary for vision tags, included in the tagging prompt."""

    category: tuple[str, ...]
    subject: tuple[str, ...]
    setting: tuple[str, ...]
    lighting: tuple[str, ...]
    mood: tuple[str, ...]


class VisionSettings(Section):
    model: str
    max_tokens: int = Field(gt=0)
    timeout_s: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    vocabulary: TagVocabulary


class SnippetSettings(Section):
    sample_rate: int = Field(gt=0)
    beats_per_bar: int = Field(gt=0)


class RecipeSpace(Section):
    """Allowed values per recipe variable."""

    cut_rate: tuple[CutRate, ...]
    opening_shot: tuple[OpeningShot, ...]
    footage_category: tuple[FootageCategory, ...]
    footage_orientation: tuple[Orientation, ...]
    length_s: tuple[int, ...]
    on_screen_text: tuple[OnScreenText, ...]
    snippet_type: tuple[SnippetType, ...]
    look: tuple[Look, ...]
    effects: tuple[Effect, ...]


class RecipeSettings(Section):
    shot_cooldown_days: int = Field(ge=0)
    quality_floor: float = Field(ge=0, le=1)
    max_repeats_per_week: int = Field(gt=0)
    space: RecipeSpace
    fixed: tuple[Recipe, ...]


class RenderSettings(Section):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)
    video_codec: str
    video_profile: str
    crf: int = Field(ge=0)
    preset: str
    audio_codec: str
    audio_bitrate: str
    luts_dir: Path
    font_file: Path
    keep_days: int = Field(ge=0)
    max_attempts: int = Field(gt=0)


class ToolSettings(Section):
    ffmpeg: str
    ffprobe: str


class CaptionSettings(Section):
    """Caption templates per platform. Placeholders: `{song}`, `{snippet}`."""

    templates: dict[Platform, str]


class CadenceSettings(Section):
    renders_per_day: int = Field(gt=0)
    tiktok_every_render: bool
    tiktok_renders_per_day: int = Field(ge=0)


class HandoffSettings(Section):
    host: str
    port: int = Field(gt=0)
    expire_hours: int = Field(gt=0)


class QuietHours(Section):
    """Local-time window in `notify.timezone`; may wrap past midnight (e.g. 22:00 to 08:00)."""

    start: time
    end: time


class NotifySettings(Section):
    backend: Literal["ntfy"]
    timezone: str
    quiet_hours: QuietHours | None = None
    timeout_s: float = Field(gt=0)
    max_retries: int = Field(ge=0)


class MatchSettings(Section):
    duration_tolerance_s: float = Field(ge=0)
    min_score: float = Field(ge=0)
    window_hours: int = Field(gt=0)
    fetch_slack_hours: float = Field(ge=0)
    caption_weight: float = Field(ge=0)
    time_weight: float = Field(ge=0)


class MeasureSettings(Section):
    checkpoint_hours: tuple[int, ...]
    meta_graph_url: str
    tiktok_api_url: str
    token_expiry_warn_days: int = Field(ge=0)
    max_attempts: int = Field(gt=0)


class HttpSettings(Section):
    """Defaults for every external HTTP client (DESIGN.md §9)."""

    timeout_s: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    backoff_base_s: float = Field(ge=0)


class LearnSettings(Section):
    baseline_window: int = Field(gt=0)
    explore_rate: float = Field(ge=0, le=1)
    settled_min_posts: int = Field(gt=0)
    superseded_weight: float = Field(ge=0, le=1)


def _secret(env_name: str) -> Any:
    return Field(default=None, validation_alias=env_name)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        frozen=True,
        # `.env` may hold unrelated variables; unknown TOML keys are caught in `load_settings`.
        extra="ignore",
        env_prefix="ACE_",
        env_nested_delimiter="__",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
    )

    data_dir: Path
    database_url: str | None = None
    log_level: str

    drive: DriveSettings
    ingest: IngestSettings
    vision: VisionSettings
    snippets: SnippetSettings
    recipes: RecipeSettings
    render: RenderSettings
    tools: ToolSettings
    captions: CaptionSettings
    cadence: CadenceSettings
    handoff: HandoffSettings
    notify: NotifySettings
    match: MatchSettings
    measure: MeasureSettings
    http: HttpSettings
    learn: LearnSettings

    google_oauth_client_json: Path | None = _secret("GOOGLE_OAUTH_CLIENT_JSON")
    anthropic_api_key: SecretStr | None = _secret("ANTHROPIC_API_KEY")
    meta_app_id: str | None = _secret("META_APP_ID")
    meta_app_secret: SecretStr | None = _secret("META_APP_SECRET")
    meta_ig_user_id: str | None = _secret("META_IG_USER_ID")
    tiktok_client_key: str | None = _secret("TIKTOK_CLIENT_KEY")
    tiktok_client_secret: SecretStr | None = _secret("TIKTOK_CLIENT_SECRET")
    ntfy_server: str | None = _secret("NTFY_SERVER")
    ntfy_topic: SecretStr | None = _secret("NTFY_TOPIC")
    ntfy_token: SecretStr | None = _secret("NTFY_TOKEN")
    handoff_base_url: str | None = _secret("HANDOFF_BASE_URL")
    handoff_token: SecretStr | None = _secret("HANDOFF_TOKEN")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # The TOML files arrive as init kwargs, so they rank below the environment.
        return env_settings, dotenv_settings, init_settings

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "engine.sqlite3"

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.db_path}"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        return tomllib.load(file)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(
    config_dir: Path | None = None, env_file: Path | None = DEFAULT_ENV_FILE
) -> Settings:
    """Load settings from `config_dir` (default: `$ACE_CONFIG_DIR` or `./config`) and the env.

    `env_file` is read if it exists; pass None to ignore `.env` (as tests do).
    """
    config_dir = config_dir or Path(os.environ.get("ACE_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    values = _read_toml(config_dir / "default.toml")
    local_file = config_dir / "local.toml"
    if local_file.exists():
        values = _deep_merge(values, _read_toml(local_file))
    unknown_keys = sorted(set(values) - set(Settings.model_fields))
    if unknown_keys:
        raise ValueError(f"unknown config keys in {config_dir}: {', '.join(unknown_keys)}")
    return Settings(_env_file=env_file, **values)
