import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from engine.cli import app
from engine.config import load_settings
from engine.db.enums import SnippetType
from engine.db.models import SnippetRow, SongRow
from engine.db.session import create_db_engine, make_session_factory
from engine.snippets import cli as snippets_cli
from engine.snippets.models import SongAnalysis
from tests.fixtures.make_fixtures import CLICK_DROP_S

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

runner = CliRunner()


@pytest.fixture
def initialized_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(REPO_CONFIG_DIR / "default.toml", config_dir / "default.toml")
    monkeypatch.setenv("ACE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ACE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output


def stored_rows() -> tuple[list[SongRow], list[SnippetRow]]:
    engine = create_db_engine(load_settings().db_url)
    with make_session_factory(engine)() as session:
        songs = list(session.scalars(select(SongRow)))
        snippets = list(session.scalars(select(SnippetRow).order_by(SnippetRow.id)))
    engine.dispose()
    return songs, snippets


def add(audio: Path, *extra: str) -> list[str]:
    return ["snippets", "add", str(audio), *extra]


@pytest.mark.usefixtures("initialized_env")
def test_add_analyzes_the_song_and_detects_the_drop(click_track_wav: Path) -> None:
    argv = add(click_track_wav, "--start", "4", "--end", "14", "--type", "drop", "--label", "big")
    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.output
    [song], [snippet] = stored_rows()
    assert song.audio_path == str(click_track_wav.resolve())
    assert song.bpm == pytest.approx(120.0, abs=0.5)
    assert len(song.beats_json) == 40
    assert snippet.song_id == song.id
    assert snippet.snippet_type == SnippetType.DROP
    assert snippet.start_s in song.downbeats_json
    assert snippet.start_s == pytest.approx(4.0, abs=0.02)
    assert (snippet.end_s, snippet.label) == (14.0, "big")
    assert snippet.drop_s == pytest.approx(CLICK_DROP_S, abs=0.02)
    assert snippet.drop_s in song.downbeats_json


@pytest.mark.usefixtures("initialized_env")
def test_second_snippet_reuses_the_stored_analysis(
    click_track_wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = runner.invoke(app, add(click_track_wav, "--start", "0", "--end", "6", "--type", "hook"))
    assert first.exit_code == 0, first.output

    def fail_if_called(*args: object) -> SongAnalysis:
        raise AssertionError("song analyzed twice")

    monkeypatch.setattr(snippets_cli, "analyze_song", fail_if_called)
    second = runner.invoke(
        app, add(click_track_wav, "--start", "10", "--end", "18", "--type", "intro_build")
    )

    assert second.exit_code == 0, second.output
    songs, snippets = stored_rows()
    assert len(songs) == 1
    assert [snippet.snippet_type for snippet in snippets] == [
        SnippetType.HOOK,
        SnippetType.INTRO_BUILD,
    ]
    assert snippets[1].drop_s is None


@pytest.mark.usefixtures("initialized_env")
def test_drop_override_is_stored_as_given(click_track_wav: Path) -> None:
    argv = add(click_track_wav, "--start", "4", "--end", "14", "--type", "drop", "--drop", "10")
    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.output
    _, [snippet] = stored_rows()
    assert snippet.drop_s == 10.0


@pytest.mark.parametrize(
    "window",
    [
        ["--start", "6", "--end", "6"],
        ["--start", "15", "--end", "25"],
        ["--start", "4", "--end", "14", "--drop", "15"],
    ],
    ids=["empty", "past_the_end", "drop_outside"],
)
@pytest.mark.usefixtures("initialized_env")
def test_invalid_window_is_rejected_without_a_snippet(
    click_track_wav: Path, window: list[str]
) -> None:
    result = runner.invoke(app, add(click_track_wav, *window, "--type", "drop"))

    assert result.exit_code == 1
    assert "error" in result.output
    _, snippets = stored_rows()
    assert snippets == []


@pytest.mark.usefixtures("initialized_env")
def test_missing_audio_file_is_rejected(tmp_path: Path) -> None:
    result = runner.invoke(
        app, add(tmp_path / "nope.wav", "--start", "0", "--end", "4", "--type", "hook")
    )

    assert result.exit_code == 1
    assert "nope.wav" in result.output


@pytest.mark.usefixtures("initialized_env")
def test_list_prints_one_row_per_snippet(click_track_wav: Path) -> None:
    assert (
        runner.invoke(
            app,
            add(click_track_wav, "--start", "4", "--end", "14", "--type", "drop", "--label", "big"),
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, add(click_track_wav, "--start", "0", "--end", "6", "--type", "hook")
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["snippets", "list"])

    assert result.exit_code == 0, result.output
    header, *rows = [line.split() for line in result.output.splitlines() if line.strip()]
    assert header == ["id", "song", "type", "start", "end", "drop", "label"]
    assert rows == [
        ["1", "click_120bpm", "drop", "3.99", "14.00", "7.99", "big"],
        ["2", "click_120bpm", "hook", "0.00", "6.00", "-", "-"],
    ]


@pytest.mark.usefixtures("initialized_env")
def test_list_with_no_snippets_says_so() -> None:
    result = runner.invoke(app, ["snippets", "list"])

    assert result.exit_code == 0
    assert "no snippets" in result.output


@pytest.mark.usefixtures("initialized_env")
def test_start_is_snapped_to_the_nearest_downbeat_and_reported(click_track_wav: Path) -> None:
    result = runner.invoke(
        app, add(click_track_wav, "--start", "4.6", "--end", "14", "--type", "hook")
    )

    assert result.exit_code == 0, result.output
    [song], [snippet] = stored_rows()
    assert snippet.start_s in song.downbeats_json
    assert snippet.start_s == pytest.approx(4.0, abs=0.02)
    assert snippet.end_s == 14.0
    assert f"start snapped 4.60 → {snippet.start_s:.2f} s (downbeat)" in result.output


@pytest.mark.usefixtures("initialized_env")
def test_start_already_on_a_downbeat_is_not_reported_as_snapped(click_track_wav: Path) -> None:
    first = runner.invoke(
        app, add(click_track_wav, "--start", "4.6", "--end", "14", "--type", "hook")
    )
    assert first.exit_code == 0, first.output
    _, [snippet] = stored_rows()

    second = runner.invoke(
        app, add(click_track_wav, "--start", repr(snippet.start_s), "--end", "14", "--type", "hook")
    )

    assert second.exit_code == 0, second.output
    assert "snapped" not in second.output
    _, snippets = stored_rows()
    assert snippets[1].start_s == snippet.start_s


@pytest.mark.usefixtures("initialized_env")
def test_drop_override_is_checked_against_the_snapped_window(click_track_wav: Path) -> None:
    earlier_than_typed = runner.invoke(
        app,
        add(click_track_wav, "--start", "4.6", "--end", "14", "--type", "drop", "--drop", "4.2"),
    )
    before_snapped = runner.invoke(
        app,
        add(click_track_wav, "--start", "3.5", "--end", "14", "--type", "drop", "--drop", "3.8"),
    )

    assert earlier_than_typed.exit_code == 0, earlier_than_typed.output
    assert before_snapped.exit_code == 1
    assert "--drop" in before_snapped.output
    _, snippets = stored_rows()
    assert [snippet.drop_s for snippet in snippets] == [4.2]


@pytest.mark.usefixtures("initialized_env")
def test_drop_is_detected_in_the_snapped_window(click_track_wav: Path) -> None:
    result = runner.invoke(
        app, add(click_track_wav, "--start", "8.8", "--end", "14", "--type", "drop")
    )

    assert result.exit_code == 0, result.output
    _, [snippet] = stored_rows()
    assert snippet.drop_s == pytest.approx(CLICK_DROP_S, abs=0.02)
