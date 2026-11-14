import pytest

from engine.micro.naming import is_micro_video_name, sidecar_name_for


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("hook_micro.mp4", True),
        ("beach_hook_MICRO.mov", True),
        ("a.b_Micro.MP4", True),
        ("_micro.mp4", True),
        ("hook_micro", False),
        ("hook_micro.txt", False),
        ("hook_micros.mp4", False),
        ("micro_hook.mp4", False),
        ("hookmicro.mp4", False),
        ("hook_micro_v2.mp4", False),
        ("beach.mov", False),
    ],
)
def test_micro_videos_have_a_stem_ending_in_underscore_micro(name: str, expected: bool) -> None:
    assert is_micro_video_name(name) is expected


def test_sidecar_keeps_the_stem_and_swaps_the_extension() -> None:
    assert sidecar_name_for("beach_hook_MICRO.mov") == "beach_hook_MICRO.txt"
    assert sidecar_name_for("a.b_micro.mp4") == "a.b_micro.txt"
