import random

from sqlalchemy.orm import Session

from engine.config import Settings
from engine.recipes.batch import plan_batch
from tests.integration.test_recipes_batch import NOW, fixed, seeded, settings

__all__ = ["fixed", "seeded", "settings"]


def test_a_snippet_id_limits_the_batch_to_that_snippet(
    db_session: Session, settings: Settings, seeded: dict[str, int]
) -> None:
    unfiltered = plan_batch(db_session, settings, random.Random(1), NOW, 4)
    assert {plan.snippet_id for plan in unfiltered} == {seeded["drop"], seeded["hook"]}

    plans = plan_batch(db_session, settings, random.Random(1), NOW, 4, snippet_id=seeded["hook"])

    assert len(plans) == 3
    assert {plan.snippet_id for plan in plans} == {seeded["hook"]}
