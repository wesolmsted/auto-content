import pytest

from engine.micro.naming import is_micro_folder_name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("you dont dance_micro", True),
        ("You_Dont_Dance_MICRO", True),
        ("song_Micro  ", True),
        ("_micro", True),
        ("song_micros", False),
        ("micro_song", False),
        ("songmicro", False),
        ("song_micro_old", False),
        ("song_micro.mp4", False),
        ("Processed", False),
    ],
)
def test_micro_folders_have_a_name_ending_in_underscore_micro(name: str, expected: bool) -> None:
    assert is_micro_folder_name(name) is expected
