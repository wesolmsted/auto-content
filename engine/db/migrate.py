from pathlib import Path

from alembic import command
from alembic.config import Config

# alembic/ sits at the repo root next to engine/, so this needs the project checkout
# (an editable install, as `uv sync` and the Dockerfile do).
ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"


def alembic_config(db_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def upgrade_to_head(db_url: str) -> None:
    """Apply every migration to the database at `db_url`."""
    command.upgrade(alembic_config(db_url), "head")
