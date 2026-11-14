from engine.micro.models import Sidecar

SONG_PREFIX = "song:"
BYTE_ORDER_MARK = "﻿"


class SidecarError(ValueError):
    """The sidecar text doesn't follow the format in DESIGN.md §7.10."""


def parse_sidecar(text: str) -> Sidecar:
    """Parse a micro set's `.txt`: first non-empty line `song: <name>`, then one text option
    per non-empty line. Raises SidecarError."""
    lines = [line.strip() for line in text.removeprefix(BYTE_ORDER_MARK).splitlines()]
    non_empty = [line for line in lines if line]
    if not non_empty:
        raise SidecarError("sidecar is empty")
    song_line, *text_options = non_empty
    if not song_line.lower().startswith(SONG_PREFIX):
        raise SidecarError(f"first non-empty line must be `song: <name>`, got {song_line!r}")
    song_title = song_line[len(SONG_PREFIX) :].strip()
    if not song_title:
        raise SidecarError("song name is empty")
    if not text_options:
        raise SidecarError("sidecar has no text options after the song line")
    return Sidecar(song_title=song_title, text_options=tuple(text_options))
