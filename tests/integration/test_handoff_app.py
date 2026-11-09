import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engine.config import Settings, load_settings
from engine.db.enums import (
    MatchMethod,
    Platform,
    PostStatus,
    RejectReason,
    RenderStatus,
    SnippetType,
)
from engine.db.models import PostRow, RecipeRow, RenderRow, SnippetRow, SongRow
from engine.db.session import create_db_engine, make_session_factory, session_scope
from engine.handoff.app import create_app
from engine.storage.media_store import LocalMediaStore, MediaKind

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
BASE_SETTINGS = load_settings(REPO_CONFIG_DIR, env_file=None)
TOKEN = "a-long-random-handoff-token"
SETTINGS = BASE_SETTINGS.model_copy(update={"handoff_token": SecretStr(TOKEN)})
RECIPE = BASE_SETTINGS.recipes.fixed[1]
NOW = datetime(2026, 6, 10, 21, 0, tzinfo=UTC)
VIDEO_BYTES = bytes(range(256)) * 40
BOTH_TARGETS = (Platform.IG_TRIAL, Platform.TIKTOK)
IG_URL = "https://www.instagram.com/reel/DAbc12xY/"
TIKTOK_URL = "https://www.tiktok.com/@creator/video/7301234567890123456"


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Handoff:
    client: TestClient
    sessions: sessionmaker[Session]
    store: LocalMediaStore
    clock: Clock

    def login(self) -> None:
        response = self.client.get("/login", params={"token": TOKEN}, follow_redirects=False)
        assert response.status_code == 303

    def add_render(
        self,
        status: RenderStatus = RenderStatus.READY,
        targets: tuple[Platform, ...] = BOTH_TARGETS,
        song_title: str = "Night Drive",
        created_at: datetime = NOW,
        post_statuses: dict[Platform, PostStatus] | None = None,
    ) -> int:
        with session_scope(self.sessions) as session:
            if session.get(RecipeRow, RECIPE.id) is None:
                session.add(RecipeRow(id=RECIPE.id, params_json=RECIPE.canonical_params()))
            song = SongRow(title=song_title, audio_path=f"{song_title}.wav")
            session.add(song)
            session.flush()
            snippet = SnippetRow(
                song_id=song.id, snippet_type=SnippetType.DROP, start_s=30, end_s=37
            )
            session.add(snippet)
            session.flush()
            render = RenderRow(
                recipe_id=RECIPE.id,
                snippet_id=snippet.id,
                timeline_json={},
                duration_s=7.0,
                targets_json=[target.value for target in targets],
                captions_json={target.value: f"{song_title} on {target}" for target in targets},
                status=status,
                created_at=created_at,
            )
            session.add(render)
            session.flush()
            path = self.store.path_for(MediaKind.RENDERS, f"render_{render.id}.mp4")
            path.write_bytes(VIDEO_BYTES)
            render.file_path = str(path)
            for platform, post_status in (post_statuses or {}).items():
                session.add(PostRow(render_id=render.id, platform=platform, status=post_status))
            return render.id

    def render(self, render_id: int) -> RenderRow:
        with self.sessions() as session:
            return session.get_one(RenderRow, render_id)

    def posts(self, render_id: int) -> list[PostRow]:
        with self.sessions() as session:
            query = select(PostRow).where(PostRow.render_id == render_id).order_by(PostRow.id)
            return list(session.scalars(query).all())


def make_handoff(db_url: str, tmp_path: Path, settings: Settings) -> Iterator[Handoff]:
    engine = create_db_engine(db_url)
    sessions = make_session_factory(engine)
    store = LocalMediaStore(tmp_path / "media")
    clock = Clock(NOW)
    with TestClient(create_app(settings, sessions, store, clock=clock)) as client:
        yield Handoff(client, sessions, store, clock)
    engine.dispose()


@pytest.fixture
def handoff(db_url: str, tmp_path: Path) -> Iterator[Handoff]:
    yield from make_handoff(db_url, tmp_path, SETTINGS)


@pytest.fixture
def signed_in(handoff: Handoff) -> Handoff:
    handoff.login()
    return handoff


def render_links(html: str) -> list[int]:
    return [int(render_id) for render_id in re.findall(r'href="/handoff/(\d+)"', html)]


# Auth


def test_login_with_the_right_token_sets_an_httponly_cookie_and_redirects_home(
    handoff: Handoff,
) -> None:
    response = handoff.client.get("/login", params={"token": TOKEN}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert handoff.client.get("/").status_code == 200


def test_login_with_a_wrong_token_is_refused_and_sets_no_cookie(handoff: Handoff) -> None:
    response = handoff.client.get("/login", params={"token": "guess"}, follow_redirects=False)

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert handoff.client.get("/").status_code == 401


def test_login_is_refused_when_no_token_is_configured(db_url: str, tmp_path: Path) -> None:
    no_token = BASE_SETTINGS.model_copy(update={"handoff_token": None})
    for handoff in make_handoff(db_url, tmp_path, no_token):
        response = handoff.client.get("/login", params={"token": ""}, follow_redirects=False)
        assert response.status_code == 401
        assert handoff.client.get("/").status_code == 401


def test_a_forged_cookie_is_refused(handoff: Handoff) -> None:
    handoff.client.cookies.set("ace_handoff", "guess")
    assert handoff.client.get("/").status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/"),
        ("GET", "/handoff/{id}"),
        ("GET", "/handoff/{id}/preview"),
        ("GET", "/handoff/{id}/download"),
        ("POST", "/handoff/{id}/reject"),
        ("POST", "/handoff/{id}/link"),
        ("GET", "/static/handoff.css"),
    ],
)
def test_every_route_needs_the_login_cookie(handoff: Handoff, method: str, path: str) -> None:
    render_id = handoff.add_render()

    response = handoff.client.request(
        method,
        path.format(id=render_id),
        data={"reason": "boring", "platform": "tiktok", "url": TIKTOK_URL},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert handoff.render(render_id).status == RenderStatus.READY
    assert handoff.render(render_id).download_count == 0
    assert handoff.posts(render_id) == []


# Pages


def test_home_lists_ready_renders_newest_first_then_approved_ones_awaiting_a_post(
    signed_in: Handoff,
) -> None:
    older_ready = signed_in.add_render(created_at=NOW - timedelta(hours=5))
    newer_ready = signed_in.add_render(created_at=NOW - timedelta(hours=1))
    awaiting = signed_in.add_render(
        RenderStatus.APPROVED,
        post_statuses={
            Platform.IG_TRIAL: PostStatus.MATCHED,
            Platform.TIKTOK: PostStatus.AWAITING_POST,
        },
    )
    signed_in.add_render(
        RenderStatus.APPROVED,
        post_statuses={
            Platform.IG_TRIAL: PostStatus.MATCHED,
            Platform.TIKTOK: PostStatus.NOT_POSTED,
        },
    )
    signed_in.add_render(RenderStatus.REJECTED)
    signed_in.add_render(RenderStatus.SUPERSEDED)
    signed_in.add_render(RenderStatus.RENDERING)

    response = signed_in.client.get("/")

    assert response.status_code == 200
    assert render_links(response.text) == [newer_ready, older_ready, awaiting]


def test_handoff_page_shows_preview_save_captions_reminders_and_reject(signed_in: Handoff) -> None:
    render_id = signed_in.add_render()

    response = signed_in.client.get(f"/handoff/{render_id}")

    assert response.status_code == 200
    html = response.text
    assert f'src="/handoff/{render_id}/preview"' in html
    assert f'href="/handoff/{render_id}/download"' in html
    assert "Save video" in html
    assert "Night Drive on ig_trial" in html
    assert "Night Drive on tiktok" in html
    assert html.count("Copy caption") == 2
    assert "Trial" in html
    assert "official sound" in html
    for reason in RejectReason:
        assert f'value="{reason.value}"' in html
    assert f'action="/handoff/{render_id}/reject"' in html
    assert f'action="/handoff/{render_id}/link"' not in html


def test_handoff_page_offers_link_post_after_approval_and_hides_save_and_reject(
    signed_in: Handoff,
) -> None:
    render_id = signed_in.add_render(
        RenderStatus.APPROVED,
        post_statuses={
            Platform.IG_TRIAL: PostStatus.AWAITING_POST,
            Platform.TIKTOK: PostStatus.MATCHED,
        },
    )

    html = signed_in.client.get(f"/handoff/{render_id}").text

    assert f'action="/handoff/{render_id}/link"' in html
    assert html.count('name="url"') == 1
    assert 'value="ig_trial"' in html
    assert f'action="/handoff/{render_id}/reject"' not in html
    assert "Save video" in html


@pytest.mark.parametrize("status", [RenderStatus.REJECTED, RenderStatus.SUPERSEDED])
def test_handoff_page_has_no_save_button_once_rejected_or_superseded(
    signed_in: Handoff, status: RenderStatus
) -> None:
    render_id = signed_in.add_render(status)

    html = signed_in.client.get(f"/handoff/{render_id}").text

    assert f'href="/handoff/{render_id}/download"' not in html
    assert f'action="/handoff/{render_id}/reject"' not in html


def test_unknown_render_is_404(signed_in: Handoff) -> None:
    assert signed_in.client.get("/handoff/999").status_code == 404
    assert signed_in.client.get("/handoff/999/download").status_code == 404


# Preview


def test_preview_serves_the_video_and_never_approves(signed_in: Handoff) -> None:
    render_id = signed_in.add_render()

    response = signed_in.client.get(f"/handoff/{render_id}/preview")

    assert response.status_code == 200
    assert response.content == VIDEO_BYTES
    assert response.headers["content-type"] == "video/mp4"
    assert "attachment" not in response.headers.get("content-disposition", "")
    render = signed_in.render(render_id)
    assert render.status == RenderStatus.READY
    assert render.download_count == 0
    assert render.downloaded_at is None
    assert signed_in.posts(render_id) == []


def test_preview_supports_range_requests(signed_in: Handoff) -> None:
    render_id = signed_in.add_render()

    response = signed_in.client.get(
        f"/handoff/{render_id}/preview", headers={"Range": "bytes=100-199"}
    )

    assert response.status_code == 206
    assert response.content == VIDEO_BYTES[100:200]
    assert response.headers["content-range"] == f"bytes 100-199/{len(VIDEO_BYTES)}"
    assert signed_in.render(render_id).status == RenderStatus.READY


# Download = approval


def test_first_download_approves_and_creates_one_post_per_target(signed_in: Handoff) -> None:
    render_id = signed_in.add_render()

    response = signed_in.client.get(f"/handoff/{render_id}/download")

    assert response.status_code == 200
    assert response.content == VIDEO_BYTES
    assert response.headers["content-disposition"].startswith("attachment")
    assert f"render_{render_id}.mp4" in response.headers["content-disposition"]
    render = signed_in.render(render_id)
    assert render.status == RenderStatus.APPROVED
    assert render.downloaded_at == NOW
    assert render.download_count == 1
    posts = signed_in.posts(render_id)
    assert [(post.platform, post.status) for post in posts] == [
        (Platform.IG_TRIAL, PostStatus.AWAITING_POST),
        (Platform.TIKTOK, PostStatus.AWAITING_POST),
    ]


def test_repeat_downloads_only_count_and_never_duplicate_posts(signed_in: Handoff) -> None:
    render_id = signed_in.add_render(targets=(Platform.IG_TRIAL,))
    signed_in.client.get(f"/handoff/{render_id}/download")
    signed_in.clock.now = NOW + timedelta(minutes=10)

    for _ in range(2):
        response = signed_in.client.get(f"/handoff/{render_id}/download")
        assert response.status_code == 200
        assert response.content == VIDEO_BYTES

    render = signed_in.render(render_id)
    assert render.status == RenderStatus.APPROVED
    assert render.download_count == 3
    assert render.downloaded_at == NOW
    assert [post.platform for post in signed_in.posts(render_id)] == [Platform.IG_TRIAL]


@pytest.mark.parametrize(
    "status", [RenderStatus.REJECTED, RenderStatus.SUPERSEDED, RenderStatus.RENDERING]
)
def test_download_is_409_unless_ready_or_approved(signed_in: Handoff, status: RenderStatus) -> None:
    render_id = signed_in.add_render(status)

    response = signed_in.client.get(f"/handoff/{render_id}/download")

    assert response.status_code == 409
    render = signed_in.render(render_id)
    assert render.status == status
    assert render.download_count == 0
    assert render.downloaded_at is None
    assert signed_in.posts(render_id) == []


def test_download_of_a_missing_file_is_404_and_does_not_approve(signed_in: Handoff) -> None:
    render_id = signed_in.add_render()
    Path(signed_in.render(render_id).file_path or "").unlink()

    assert signed_in.client.get(f"/handoff/{render_id}/download").status_code == 404
    assert signed_in.render(render_id).status == RenderStatus.READY


# Reject


def test_reject_records_reason_note_and_review_time(signed_in: Handoff) -> None:
    render_id = signed_in.add_render()

    response = signed_in.client.post(
        f"/handoff/{render_id}/reject",
        data={"reason": "bad_timing", "note": "  cuts late after the drop "},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/handoff/{render_id}"
    render = signed_in.render(render_id)
    assert render.status == RenderStatus.REJECTED
    assert render.reject_reason == RejectReason.BAD_TIMING
    assert render.reject_note == "cuts late after the drop"
    assert render.reviewed_at == NOW
    assert signed_in.client.get(f"/handoff/{render_id}/download").status_code == 409


def test_reject_without_a_note_stores_none(signed_in: Handoff) -> None:
    render_id = signed_in.add_render()

    signed_in.client.post(f"/handoff/{render_id}/reject", data={"reason": "boring", "note": ""})

    render = signed_in.render(render_id)
    assert render.reject_reason == RejectReason.BORING
    assert render.reject_note is None


def test_reject_with_an_unknown_reason_is_refused(signed_in: Handoff) -> None:
    render_id = signed_in.add_render()

    response = signed_in.client.post(f"/handoff/{render_id}/reject", data={"reason": "meh"})

    assert response.status_code == 422
    assert signed_in.render(render_id).status == RenderStatus.READY


def test_reject_after_approval_is_409(signed_in: Handoff) -> None:
    render_id = signed_in.add_render()
    signed_in.client.get(f"/handoff/{render_id}/download")

    response = signed_in.client.post(f"/handoff/{render_id}/reject", data={"reason": "boring"})

    assert response.status_code == 409
    assert signed_in.render(render_id).status == RenderStatus.APPROVED


# Link post


def test_link_post_marks_the_post_matched_manually(signed_in: Handoff) -> None:
    render_id = signed_in.add_render()
    signed_in.client.get(f"/handoff/{render_id}/download")

    response = signed_in.client.post(
        f"/handoff/{render_id}/link",
        data={"platform": "tiktok", "url": TIKTOK_URL + "?lang=en"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/handoff/{render_id}"
    ig_post, tiktok_post = signed_in.posts(render_id)
    assert tiktok_post.status == PostStatus.MATCHED
    assert tiktok_post.match_method == MatchMethod.MANUAL
    assert tiktok_post.external_id == "7301234567890123456"
    assert tiktok_post.permalink == TIKTOK_URL
    assert ig_post.status == PostStatus.AWAITING_POST
    assert ig_post.external_id is None


def test_link_post_works_from_not_posted(signed_in: Handoff) -> None:
    render_id = signed_in.add_render(
        RenderStatus.APPROVED,
        targets=(Platform.IG_TRIAL,),
        post_statuses={Platform.IG_TRIAL: PostStatus.NOT_POSTED},
    )

    signed_in.client.post(
        f"/handoff/{render_id}/link", data={"platform": "ig_trial", "url": IG_URL}
    )

    (post,) = signed_in.posts(render_id)
    assert post.status == PostStatus.MATCHED
    assert post.match_method == MatchMethod.MANUAL
    assert post.external_id == "DAbc12xY"
    assert post.permalink == IG_URL


def test_link_post_with_an_unparseable_url_shows_why_and_changes_nothing(
    signed_in: Handoff,
) -> None:
    render_id = signed_in.add_render()
    signed_in.client.get(f"/handoff/{render_id}/download")

    response = signed_in.client.post(
        f"/handoff/{render_id}/link",
        data={"platform": "ig_trial", "url": "https://www.instagram.com/p/DAbc12xY/"},
    )

    assert response.status_code == 400
    assert "not a reel link" in response.text
    assert all(post.status == PostStatus.AWAITING_POST for post in signed_in.posts(render_id))


def test_link_post_on_an_already_matched_post_is_409(signed_in: Handoff) -> None:
    render_id = signed_in.add_render(
        RenderStatus.APPROVED,
        targets=(Platform.IG_TRIAL,),
        post_statuses={Platform.IG_TRIAL: PostStatus.MATCHED},
    )

    response = signed_in.client.post(
        f"/handoff/{render_id}/link", data={"platform": "ig_trial", "url": IG_URL}
    )

    assert response.status_code == 409
    assert signed_in.posts(render_id)[0].external_id is None


def test_link_post_without_a_post_for_that_platform_is_404(signed_in: Handoff) -> None:
    render_id = signed_in.add_render(targets=(Platform.IG_TRIAL,))

    response = signed_in.client.post(
        f"/handoff/{render_id}/link", data={"platform": "tiktok", "url": TIKTOK_URL}
    )

    assert response.status_code == 404
    assert signed_in.posts(render_id) == []
