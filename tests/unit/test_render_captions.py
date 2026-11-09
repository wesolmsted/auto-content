from engine.db.enums import Platform
from engine.render.job import captions_for

TEMPLATES = {Platform.IG_TRIAL: "{song} 🌃", Platform.TIKTOK: "{song} #fyp"}


def test_each_target_gets_its_platform_template_filled_with_the_song_title() -> None:
    captions = captions_for((Platform.IG_TRIAL, Platform.TIKTOK), TEMPLATES, "Night Drive", {})

    assert captions == {Platform.IG_TRIAL: "Night Drive 🌃", Platform.TIKTOK: "Night Drive #fyp"}


def test_only_targets_get_captions() -> None:
    assert captions_for((Platform.IG_TRIAL,), TEMPLATES, "Night Drive", {}) == {
        Platform.IG_TRIAL: "Night Drive 🌃"
    }


def test_a_caption_already_in_the_plan_wins_over_the_template() -> None:
    captions = captions_for(
        (Platform.IG_TRIAL, Platform.TIKTOK),
        TEMPLATES,
        "Night Drive",
        {Platform.TIKTOK: "custom", Platform.IG_TRIAL: ""},
    )

    assert captions == {Platform.IG_TRIAL: "Night Drive 🌃", Platform.TIKTOK: "custom"}
