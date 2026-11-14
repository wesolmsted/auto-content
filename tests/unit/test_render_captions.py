from engine.db.enums import Platform
from engine.render.job import captions_for

TEMPLATES = {Platform.IG_TRIAL: "{song} 🌃"}


def test_each_target_gets_its_platform_template_filled_with_the_song_title() -> None:
    captions = captions_for((Platform.IG_TRIAL,), TEMPLATES, "Night Drive", {})

    assert captions == {Platform.IG_TRIAL: "Night Drive 🌃"}


def test_only_targets_get_captions() -> None:
    assert captions_for((), TEMPLATES, "Night Drive", {}) == {}


def test_a_caption_already_in_the_plan_wins_over_the_template() -> None:
    captions = captions_for(
        (Platform.IG_TRIAL,), TEMPLATES, "Night Drive", {Platform.IG_TRIAL: "custom"}
    )

    assert captions == {Platform.IG_TRIAL: "custom"}


def test_an_empty_plan_caption_falls_back_to_the_template() -> None:
    captions = captions_for((Platform.IG_TRIAL,), TEMPLATES, "Night Drive", {Platform.IG_TRIAL: ""})

    assert captions == {Platform.IG_TRIAL: "Night Drive 🌃"}
