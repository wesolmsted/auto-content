from pathlib import Path
from typing import Annotated, NoReturn

import typer

from engine.config import load_settings
from engine.db.enums import SnippetType
from engine.db.session import create_db_engine, make_session_factory, session_scope
from engine.snippets import store
from engine.snippets.analysis import analyze_song, detect_drop

app = typer.Typer(help="Songs and the snippets cut from them.", no_args_is_help=True)


@app.command()
def add(
    audio: Annotated[Path, typer.Argument(help="Song audio file.")],
    start: Annotated[float, typer.Option(help="Snippet start in the song, seconds.")],
    end: Annotated[float, typer.Option(help="Snippet end in the song, seconds.")],
    snippet_type: Annotated[SnippetType, typer.Option("--type", help="Snippet type.")],
    drop: Annotated[float | None, typer.Option(help="Override the detected drop time.")] = None,
    label: Annotated[str | None, typer.Option(help="Short label for the snippet.")] = None,
) -> None:
    """Analyze a song (once) and add a snippet of it."""
    if not audio.is_file():
        _fail(f"no audio file at {audio}")
    if end <= start:
        _fail(f"--end ({end}) must be after --start ({start})")
    if drop is not None and not start <= drop <= end:
        _fail(f"--drop ({drop}) must lie within --start and --end")

    settings = load_settings()
    engine = create_db_engine(settings.db_url)
    with session_scope(make_session_factory(engine)) as session:
        found = store.find_song(session, audio)
        if found is None:
            song = analyze_song(audio, settings.snippets)
            song_id = store.add_song(session, audio, song)
        else:
            song_id, song = found
        if start < 0 or end > song.duration_s:
            _fail(f"snippet must lie within the song (0 to {song.duration_s:.2f} s)")
        if drop is None:
            drop = detect_drop(audio, song, start, end, settings.snippets)
        snippet_id = store.add_snippet(session, song_id, snippet_type, start, end, drop, label)
    engine.dispose()

    drop_text = "no drop" if drop is None else f"drop at {drop:.2f} s"
    typer.echo(
        f"added snippet {snippet_id}: {audio.name} {start:.2f}-{end:.2f} s, "
        f"{snippet_type.value}, {song.bpm:.1f} BPM, {drop_text}"
    )


@app.command("list")
def list_snippets() -> None:
    """List songs and their snippets."""
    engine = create_db_engine(load_settings().db_url)
    with make_session_factory(engine)() as session:
        snippets = store.list_snippets(session)
    engine.dispose()
    if not snippets:
        typer.echo("no snippets yet: add one with `ace snippets add`")
        return

    rows = [["id", "song", "type", "start", "end", "drop", "label"]] + [
        [
            str(snippet.id),
            snippet.song_title,
            snippet.snippet_type.value,
            f"{snippet.start_s:.2f}",
            f"{snippet.end_s:.2f}",
            "-" if snippet.drop_s is None else f"{snippet.drop_s:.2f}",
            snippet.label or "-",
        ]
        for snippet in snippets
    ]
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    for row in rows:
        typer.echo("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}")
    raise typer.Exit(code=1)
