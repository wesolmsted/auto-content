import pytest

from engine.micro.models import Sidecar
from engine.micro.sidecar import SidecarError, parse_sidecar


def test_first_line_is_the_song_and_the_rest_are_text_options_in_order() -> None:
    text = "song: You Dont Dance\nPOV: you finally hit the drop\nwait for it\n"

    assert parse_sidecar(text) == Sidecar(
        song_title="You Dont Dance",
        text_options=("POV: you finally hit the drop", "wait for it"),
    )


def test_blank_lines_whitespace_bom_and_crlf_are_ignored() -> None:
    text = "\ufeff\r\n   \r\n  SONG:   Midnight City  \r\n\r\n  first option \r\n\t\r\nsecond\r\n"

    assert parse_sidecar(text) == Sidecar(
        song_title="Midnight City", text_options=("first option", "second")
    )


def test_song_prefix_needs_no_space_after_the_colon() -> None:
    assert parse_sidecar("Song:Intro\nhello") == Sidecar(
        song_title="Intro", text_options=("hello",)
    )


def test_a_text_option_may_itself_contain_a_song_prefix() -> None:
    assert parse_sidecar("song: A\nsong: B") == Sidecar(song_title="A", text_options=("song: B",))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "sidecar is empty"),
        ("\ufeff \r\n\n\t\n", "sidecar is empty"),
        ("POV: hello\nsong: A\n", "first non-empty line must be `song: <name>`, got 'POV: hello'"),
        ("songs: A\nhello\n", "first non-empty line must be `song: <name>`, got 'songs: A'"),
        ("song:   \nhello\n", "song name is empty"),
        ("song: A\n", "sidecar has no text options after the song line"),
        ("song: A\n  \n\n", "sidecar has no text options after the song line"),
    ],
)
def test_invalid_sidecars_raise_with_a_clear_message(text: str, message: str) -> None:
    with pytest.raises(SidecarError) as raised:
        parse_sidecar(text)

    assert str(raised.value) == message
