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


def test_owner_sample_with_caption_and_cutoff_lines() -> None:
    text = (
        "song: you don't dance\n"
        "caption: you don't dance\n"
        "cutoff: 0:02.4\n"
        "2009 type edm >\n"
        "2014 type edm >\n"
        "hyperpop + recession pop\n"
        "\n"
    )

    assert parse_sidecar(text) == Sidecar(
        song_title="you don't dance",
        caption="you don't dance",
        text_cutoff_s=2.4,
        text_options=("2009 type edm >", "2014 type edm >", "hyperpop + recession pop"),
    )


def test_caption_and_cutoff_are_optional_and_may_come_in_either_order() -> None:
    assert parse_sidecar("song: A\nhello") == Sidecar(
        song_title="A", caption=None, text_cutoff_s=None, text_options=("hello",)
    )
    assert parse_sidecar("song: A\n  CUTOFF: 3\nCaption:  new drop \nhello") == Sidecar(
        song_title="A", caption="new drop", text_cutoff_s=3.0, text_options=("hello",)
    )


@pytest.mark.parametrize(
    ("cutoff", "seconds"),
    [
        ("0:02.4", 2.4),
        ("1:05.25", 65.25),
        ("0:07", 7.0),
        ("2.4", 2.4),
        ("2.4s", 2.4),
        ("12", 12.0),
    ],
)
def test_cutoff_accepts_minutes_and_seconds_or_plain_seconds(cutoff: str, seconds: float) -> None:
    sidecar = parse_sidecar(f"song: A\ncutoff: {cutoff}\nhello")

    assert sidecar.text_cutoff_s == pytest.approx(seconds)


def test_caption_and_cutoff_lines_after_the_first_text_option_are_text_options() -> None:
    assert parse_sidecar("song: A\nhello\ncaption: B\ncutoff: 1") == Sidecar(
        song_title="A", text_options=("hello", "caption: B", "cutoff: 1")
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("song: A\ncaption:  \nhello", "caption is empty"),
        ("song: A\ncutoff: soon\nhello", "cutoff must be a time like 0:02.4 or 2.4, got 'soon'"),
        ("song: A\ncutoff: 0:75\nhello", "cutoff must be a time like 0:02.4 or 2.4, got '0:75'"),
        ("song: A\ncutoff:\nhello", "cutoff must be a time like 0:02.4 or 2.4, got ''"),
        ("song: A\ncutoff: 0:00\nhello", "cutoff must be after 0:00"),
        ("song: A\ncaption: x\ncaption: y\nhello", "more than one `caption:` line"),
        ("song: A\ncutoff: 1\ncutoff: 2\nhello", "more than one `cutoff:` line"),
        ("song: A\ncaption: x\ncutoff: 1\n", "sidecar has no text options after the song line"),
    ],
)
def test_invalid_caption_and_cutoff_lines_raise_with_a_clear_message(
    text: str, message: str
) -> None:
    with pytest.raises(SidecarError) as raised:
        parse_sidecar(text)

    assert str(raised.value) == message
