import json
from pathlib import Path

from alembic import command
from sqlalchemy import inspect, text

from engine.db.migrate import alembic_config, upgrade_to_head
from engine.db.session import create_db_engine

NOW = "2026-11-09 12:00:00.000000"


def seed_two_platform_data(url: str) -> None:
    engine = create_db_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into recipes (id, params_json, source, active, created_at) "
                "values ('rcp_1', '{}', 'fixed', 1, :now)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "insert into songs (id, title, audio_path, beats_json, downbeats_json) "
                "values (1, 'Song', 'song.wav', '[]', '[]')"
            )
        )
        connection.execute(
            text(
                "insert into snippets (id, song_id, snippet_type, start_s, end_s, active, "
                "created_at) values (1, 1, 'drop', 0, 10, 1, :now)"
            ),
            {"now": NOW},
        )
        for render_id, targets, captions in [
            (1, '["ig_trial", "tiktok"]', '{"ig_trial": "IG caption", "tiktok": "TT caption"}'),
            (2, '["ig_trial"]', '{"ig_trial": "Only IG"}'),
        ]:
            connection.execute(
                text(
                    "insert into renders (id, recipe_id, snippet_id, timeline_json, "
                    "shot_ids_json, ffmpeg_argv_json, targets_json, captions_json, status, "
                    "download_count, attempts, created_at, updated_at) values (:id, 'rcp_1', 1, "
                    "'{}', '[]', '[]', :targets, :captions, 'approved', 1, 0, :now, :now)"
                ),
                {"id": render_id, "targets": targets, "captions": captions, "now": NOW},
            )
        for post_id, platform in [(1, "ig_trial"), (2, "tiktok")]:
            connection.execute(
                text(
                    "insert into posts (id, render_id, platform, status, created_at, updated_at) "
                    "values (:id, 1, :platform, 'matched', :now, :now)"
                ),
                {"id": post_id, "platform": platform, "now": NOW},
            )
        for metric_id, post_id, checkpoint, source in [
            (1, 1, "24h", "api"),
            (2, 2, "24h", "api"),
            (3, 2, "export", "export"),
        ]:
            connection.execute(
                text(
                    "insert into metrics (id, post_id, checkpoint, source, fetched_at, raw_json) "
                    "values (:id, :post_id, :checkpoint, :source, :now, '{}')"
                ),
                {
                    "id": metric_id,
                    "post_id": post_id,
                    "checkpoint": checkpoint,
                    "source": source,
                    "now": NOW,
                },
            )
        for platform in ["ig_trial", "tiktok", "internal"]:
            connection.execute(
                text(
                    "insert into variable_scores (variable, value, platform, alpha, beta, "
                    "n_posts, updated_at) values ('look', 'warm', :platform, 1, 1, 0, :now)"
                ),
                {"platform": platform, "now": NOW},
            )
    engine.dispose()


def test_upgrade_to_0002_removes_tiktok_data_and_the_export_table(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'old.sqlite3'}"
    command.upgrade(alembic_config(url), "0001")
    seed_two_platform_data(url)

    upgrade_to_head(url)

    engine = create_db_engine(url)
    with engine.connect() as connection:
        renders = connection.execute(
            text("select id, targets_json, captions_json from renders order by id")
        ).all()
        posts = connection.execute(text("select id, platform from posts order by id")).all()
        metrics = connection.execute(text("select id from metrics order by id")).scalars().all()
        scores = (
            connection.execute(text("select platform from variable_scores order by platform"))
            .scalars()
            .all()
        )
    tables = set(inspect(engine).get_table_names())
    engine.dispose()

    assert [
        (id_, json.loads(targets), json.loads(captions)) for id_, targets, captions in renders
    ] == [
        (1, ["ig_trial"], {"ig_trial": "IG caption"}),
        (2, ["ig_trial"], {"ig_trial": "Only IG"}),
    ]
    assert posts == [(1, "ig_trial")]
    assert metrics == [1]
    assert scores == ["ig_trial", "internal"]
    assert "metric_imports" not in tables


def test_upgrade_to_0005_keeps_micro_sets_that_videos_point_to(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'micro.sqlite3'}"
    command.upgrade(alembic_config(url), "0004")
    engine = create_db_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into micro_sets (id, drive_folder_id, name, song_title, "
                "text_options_json, next_text_index, status, created_at, updated_at, "
                "text_cutoff_s) values (1, 'folder1', 'a_micro', 'A', '[\"hi\"]', 2, 'active', "
                ":now, :now, 2.4)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "insert into micro_sources (id, set_id, drive_file_id, name, status, attempts, "
                "created_at, updated_at) values (1, 1, 'file1', 'clip.mp4', 'ready', 0, :now, :now)"
            ),
            {"now": NOW},
        )
    engine.dispose()

    upgrade_to_head(url)

    engine = create_db_engine(url)
    with engine.connect() as connection:
        sets = connection.execute(
            text("select id, next_text_index, text_cutoff_s, mirror_allowed from micro_sets")
        ).all()
        sources = connection.execute(text("select id, set_id from micro_sources")).all()
    engine.dispose()
    assert sets == [(1, 2, 2.4, 1)]
    assert sources == [(1, 1)]
