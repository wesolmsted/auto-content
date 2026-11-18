import re

from engine.micro.models import Sidecar

SONG_PREFIX = "song:"
CAPTION_KEY = "caption"
CUTOFF_KEY = "cutoff"
BYTE_ORDER_MARK = "﻿"
HEADER_LINE = re.compile(rf"^({CAPTION_KEY}|{CUTOFF_KEY}):(.*)$", re.IGNORECASE)
CUTOFF_TIME = re.compile(r"^(?:(\d+):)?(\d+(?:\.\d+)?)s?$")


class SidecarError(ValueError):
    """The sidecar text doesn't follow the format in DESIGN.md §7.10."""


def parse_sidecar(text: str) -> Sidecar:
    """Parse a micro set's `.txt`: first non-empty line `song: <name>`, then optional
    `caption: <text>` and `cutoff: <time>` lines in either order, then one text option per
    non-empty line. Raises SidecarError."""
    lines = [line.strip() for line in text.removeprefix(BYTE_ORDER_MARK).splitlines()]
    non_empty = [line for line in lines if line]
    if not non_empty:
        raise SidecarError("sidecar is empty")
    song_line, *rest = non_empty
    if not song_line.lower().startswith(SONG_PREFIX):
        raise SidecarError(f"first non-empty line must be `song: <name>`, got {song_line!r}")
    song_title = song_line[len(SONG_PREFIX) :].strip()
    if not song_title:
        raise SidecarError("song name is empty")

    headers: dict[str, str] = {}
    while rest and (match := HEADER_LINE.match(rest[0])):
        key = match.group(1).lower()
        if key in headers:
            raise SidecarError(f"more than one `{key}:` line")
        headers[key] = match.group(2).strip()
        rest = rest[1:]

    caption = headers.get(CAPTION_KEY)
    if caption == "":
        raise SidecarError("caption is empty")
    cutoff = headers.get(CUTOFF_KEY)
    text_cutoff_s = None if cutoff is None else _parse_cutoff(cutoff)
    if not rest:
        raise SidecarError("sidecar has no text options after the song line")
    return Sidecar(
        song_title=song_title,
        caption=caption,
        text_cutoff_s=text_cutoff_s,
        text_options=tuple(rest),
    )


def _parse_cutoff(value: str) -> float:
    match = CUTOFF_TIME.match(value)
    if match is None:
        raise SidecarError(f"cutoff must be a time like 0:02.4 or 2.4, got {value!r}")
    minutes, seconds = match.group(1), float(match.group(2))
    if minutes is not None and seconds >= 60:
        raise SidecarError(f"cutoff must be a time like 0:02.4 or 2.4, got {value!r}")
    total = int(minutes or 0) * 60 + seconds
    if total <= 0:
        raise SidecarError("cutoff must be after 0:00")
    return total
