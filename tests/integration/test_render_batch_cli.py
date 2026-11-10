import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from engine.cli import app
from engine.config import load_settings
from engine.db.enums import Orientation, RenderStatus, SnippetType
from engine.db.models import FootageFileRow, RecipeRow, RenderRow, ShotRow, SnippetRow, SongRow
from engine.db.session import create_db_engine, make_session_factory
from tests.integration.test_recipes_batch import make_shot_row

REPO_ROOT = Path(__file__).resolve().parents[2]
NTFY_URL = "https://ntfy.example.test/ace-topic-render-7d1c"

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repo default config, a temp data dir, fast renders, no quiet hours and no ntfy config."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(REPO_ROOT / "config" / "default.toml", config_dir / "default.toml")
    monkeypatch.setenv("ACE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ACE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ACE_RENDER__PRESET", "ultrafast")
    monkeypatch.setenv("ACE_RENDER__LUTS_DIR", str(REPO_ROOT / "config" / "luts"))
    monkeypatch.setenv("ACE_NOTIFY__QUIET_HOURS__START", "00:00:00")
    monkeypatch.setenv("ACE_NOTIFY__QUIET_HOURS__END", "00:00:00")
    for name in ("NTFY_SERVER", "NTFY_TOPIC", "NTFY_TOKEN", "HANDOFF_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    return tmp_path


@pytest.fixture
def snippets(cli_env: Path, click_track_wav: Path, landscape_motion_clip: Path) -> dict[str, int]:
    """The 20 s click track with a drop and a hook snippet, and 60 shots of the landscape clip."""
    beats = [index * 0.5 for index in range(40)]
    with session() as db:
        song = SongRow(
            title="Click",
            audio_path=str(click_track_wav),
            duration_s=20.0,
            bpm=120.0,
            beats_json=beats,
            downbeats_json=beats[::4],
            analyzed_at=datetime(2026, 11, 1, tzinfo=UTC),
        )
        footage = FootageFileRow(drive_file_id="drive-1", name="clip.mp4")
        db.add_all([song, footage])
        db.flush()
        drop = SnippetRow(
            song_id=song.id, snippet_type=SnippetType.DROP, start_s=0.0, end_s=20.0, drop_s=8.0
        )
        hook = SnippetRow(song_id=song.id, snippet_type=SnippetType.HOOK, start_s=0.0, end_s=20.0)
        db.add_all([drop, hook])
        db.add_all(
            make_shot_row(footage.id, index, proxy_path=str(landscape_motion_clip))
            for index in range(60)
        )
        db.commit()
        return {"drop": drop.id, "hook": hook.id}


def session() -> Session:
    return make_session_factory(create_db_engine(load_settings().db_url))()


def count(model: type[RenderRow] | type[RecipeRow]) -> int:
    with session() as db:
        return db.scalar(select(func.count()).select_from(model)) or 0


def planned_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("plan ")]


def recipe_id(index: int) -> str:
    """The id of the index-th portrait fixed recipe; the seeded shots are portrait."""
    portrait = [
        recipe
        for recipe in load_settings().recipes.fixed
        if recipe.footage_orientation is Orientation.PORTRAIT
    ]
    return portrait[index].id


@pytest.mark.usefixtures("snippets")
def test_dry_run_prints_each_planned_timeline_and_writes_nothing() -> None:
    result = runner.invoke(app, ["render", "batch", "--n", "2", "--dry-run"])

    assert result.exit_code == 0, result.output
    plans = planned_lines(result.output)
    assert len(plans) == 2
    fixed_ids = {recipe.id for recipe in load_settings().recipes.fixed}
    for line in plans:
        match = re.fullmatch(
            r"plan \d: recipe=(rcp_\w{12}) snippet=\d+ targets=[\w,]+ duration=\d+\.\d\ds", line
        )
        assert match, line
        assert match[1] in fixed_ids
    assert "targets=ig_trial,tiktok" in plans[0]
    assert len(re.findall(r"^  cuts: \d+\.\d\d( \d+\.\d\d)*$", result.output, re.M)) == 2
    assert len(re.findall(r"^  shots: \d+( \d+)*$", result.output, re.M)) == 2
    assert count(RenderRow) == 0
    assert count(RecipeRow) == 0
    with session() as db:
        assert db.scalar(select(func.max(ShotRow.use_count))) == 0


@pytest.mark.usefixtures("snippets")
def test_n_defaults_to_the_daily_cadence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACE_CADENCE__RENDERS_PER_DAY", "3")

    result = runner.invoke(app, ["render", "batch", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert len(planned_lines(result.output)) == 3


def test_recipe_and_snippet_options_limit_the_plans(snippets: dict[str, int]) -> None:
    by_recipe = runner.invoke(
        app, ["render", "batch", "--n", "3", "--recipe", recipe_id(1), "--dry-run"]
    )
    by_snippet = runner.invoke(
        app, ["render", "batch", "--n", "3", "--snippet", str(snippets["hook"]), "--dry-run"]
    )

    assert by_recipe.exit_code == 0, by_recipe.output
    assert [f"recipe={recipe_id(1)}" in line for line in planned_lines(by_recipe.output)] == [
        True
    ] * 3
    assert by_snippet.exit_code == 0, by_snippet.output
    assert [
        f"snippet={snippets['hook']} " in line for line in planned_lines(by_snippet.output)
    ] == [True] * 3


@pytest.mark.usefixtures("snippets")
def test_an_unknown_recipe_exits_1() -> None:
    result = runner.invoke(app, ["render", "batch", "--recipe", "rcp_000000000000", "--dry-run"])

    assert result.exit_code == 1
    assert "unknown recipe rcp_000000000000" in result.output


@pytest.mark.usefixtures("snippets")
def test_without_ntfy_config_the_batch_renders_and_says_notifications_were_skipped() -> None:
    result = runner.invoke(app, ["render", "batch", "--n", "1"])

    assert result.exit_code == 0, result.output
    assert "planned=1 ready=1 failed=0 notified=0" in result.output
    assert "notifications skipped: ntfy is not configured" in result.output
    with session() as db:
        [render] = db.scalars(select(RenderRow)).all()
        assert render.status is RenderStatus.READY
        assert render.notified_at is None


@pytest.mark.usefixtures("snippets")
def test_with_ntfy_configured_the_batch_sends_a_hand_off_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, topic = NTFY_URL.rsplit("/", 1)
    monkeypatch.setenv("NTFY_SERVER", server)
    monkeypatch.setenv("NTFY_TOPIC", topic)
    monkeypatch.setenv("HANDOFF_BASE_URL", "http://mac.tail.net")

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(NTFY_URL).mock(return_value=httpx.Response(200))
        result = runner.invoke(app, ["render", "batch", "--n", "1"])

    assert result.exit_code == 0, result.output
    assert "planned=1 ready=1 failed=0 notified=1" in result.output
    assert route.call_count == 1
    with session() as db:
        [render] = db.scalars(select(RenderRow)).all()
    assert route.calls[0].request.headers["Click"] == f"http://mac.tail.net/handoff/{render.id}"


def test_render_one_renders_that_recipe_on_that_snippet_without_notifying(
    snippets: dict[str, int],
) -> None:
    result = runner.invoke(
        app, ["render", "one", "--recipe", recipe_id(2), "--snippet", str(snippets["hook"])]
    )

    assert result.exit_code == 0, result.output
    with session() as db:
        [render] = db.scalars(select(RenderRow)).all()
        assert (render.recipe_id, render.snippet_id) == (recipe_id(2), snippets["hook"])
        assert render.status is RenderStatus.READY
        assert render.notified_at is None
    assert f"render {render.id} ready: {render.file_path}" in result.output


def test_render_one_exits_1_when_nothing_can_be_planned(snippets: dict[str, int]) -> None:
    result = runner.invoke(
        app, ["render", "one", "--recipe", recipe_id(2), "--snippet", str(snippets["drop"])]
    )

    assert result.exit_code == 1
    assert "nothing to render" in result.output
    assert count(RenderRow) == 0


@pytest.mark.usefixtures("snippets")
def test_a_failed_render_makes_the_batch_exit_1_after_the_summary(tmp_path: Path) -> None:
    with session() as db:
        for row in db.scalars(select(ShotRow)):
            row.proxy_path = str(tmp_path / "missing.mp4")
        db.commit()

    result = runner.invoke(app, ["render", "batch", "--n", "1"])

    assert result.exit_code == 1, result.output
    assert "planned=1 ready=0 failed=1 notified=0" in result.output
