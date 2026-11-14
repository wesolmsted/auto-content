import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from engine.config import load_settings
from engine.db.enums import MicroRenderStatus, MicroSourceStatus
from engine.db.models import MicroRenderRow, MicroSourceRow
from engine.db.session import create_db_engine, make_session_factory, session_scope
from engine.handoff.app import create_app
from engine.storage.media_store import LocalMediaStore, MediaKind

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
TOKEN = "a-long-random-handoff-token"
SETTINGS = load_settings(REPO_CONFIG_DIR, env_file=None).model_copy(
    update={"handoff_token": SecretStr(TOKEN)}
)
NOW = datetime(2026, 6, 10, 21, 0, tzinfo=UTC)
VIDEO_BYTES = bytes(range(256)) * 40


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass
class MicroHandoff:
    client: TestClient
    sessions: sessionmaker[Session]
    store: LocalMediaStore
    clock: Clock

    def login(self) -> None:
        response = self.client.get("/login", params={"token": TOKEN}, follow_redirects=False)
        assert response.status_code == 303

    def add_micro(
        self,
        status: MicroRenderStatus = MicroRenderStatus.READY,
        scheduled_for: datetime = NOW - timedelta(minutes=5),
        notified_at: datetime | None = None,
        caption: str = "You Dont Dance",
        text: str = "POV: you finally hit the drop",
        with_file: bool = True,
    ) -> int:
        with session_scope(self.sessions) as session:
            source = MicroSourceRow(
                drive_file_id=f"drive-{caption}-{scheduled_for.isoformat()}-{text}",
                name="beach_hook_micro.mov",
                song_title=caption,
                text_options_json=[text],
                status=MicroSourceStatus.READY,
            )
            session.add(source)
            session.flush()
            render = MicroRenderRow(
                source_id=source.id,
                scheduled_for=scheduled_for,
                edit_json={},
                text=text,
                caption=caption,
                duration_s=9.5,
                status=status,
                notified_at=notified_at,
            )
            session.add(render)
            session.flush()
            if with_file:
                path = self.store.path_for(MediaKind.RENDERS, f"micro_{render.id}.mp4")
                path.write_bytes(VIDEO_BYTES)
                render.file_path = f"/somewhere/else/micro_{render.id}.mp4"
            return render.id

    def micro(self, render_id: int) -> MicroRenderRow:
        with self.sessions() as session:
            return session.get_one(MicroRenderRow, render_id)


@pytest.fixture
def handoff(db_url: str, tmp_path: Path) -> Iterator[MicroHandoff]:
    engine = create_db_engine(db_url)
    sessions = make_session_factory(engine)
    store = LocalMediaStore(tmp_path / "media")
    clock = Clock(NOW)
    with TestClient(create_app(SETTINGS, sessions, store, clock=clock)) as client:
        yield MicroHandoff(client, sessions, store, clock)
    engine.dispose()


@pytest.fixture
def signed_in(handoff: MicroHandoff) -> MicroHandoff:
    handoff.login()
    return handoff


def micro_links(html: str) -> list[int]:
    return [int(render_id) for render_id in re.findall(r'href="/micro/(\d+)"', html)]


# Auth


@pytest.mark.parametrize("path", ["/micro/{id}", "/micro/{id}/preview", "/micro/{id}/download"])
def test_every_micro_route_needs_the_login_cookie(handoff: MicroHandoff, path: str) -> None:
    render_id = handoff.add_micro()

    response = handoff.client.get(path.format(id=render_id), follow_redirects=False)

    assert response.status_code == 401
    render = handoff.micro(render_id)
    assert render.status == MicroRenderStatus.READY
    assert render.download_count == 0
    assert render.downloaded_at is None


# Index


def test_home_lists_due_ready_micro_renders_newest_first_with_song_and_text(
    signed_in: MicroHandoff,
) -> None:
    older = signed_in.add_micro(
        scheduled_for=NOW - timedelta(hours=8), caption="Old Song", text="older text"
    )
    on_the_dot = signed_in.add_micro(scheduled_for=NOW, caption="Now Song", text="right now")
    future_but_notified = signed_in.add_micro(
        scheduled_for=NOW + timedelta(hours=1),
        notified_at=NOW - timedelta(minutes=1),
        caption="Early Song",
        text="notified early",
    )
    signed_in.add_micro(scheduled_for=NOW + timedelta(seconds=1), caption="Future Song")
    for status in (
        MicroRenderStatus.RENDERING,
        MicroRenderStatus.DOWNLOADED,
        MicroRenderStatus.SUPERSEDED,
        MicroRenderStatus.FAILED,
    ):
        signed_in.add_micro(status=status, caption=f"Song {status}")

    response = signed_in.client.get("/")

    assert response.status_code == 200
    html = response.text
    assert micro_links(html) == [future_but_notified, on_the_dot, older]
    assert "Micro edits" in html
    for caption, text in [
        ("Old Song", "older text"),
        ("Now Song", "right now"),
        ("Early Song", "notified early"),
    ]:
        assert caption in html
        assert text in html
    assert "Future Song" not in html
    assert "Song downloaded" not in html


def test_home_says_when_there_are_no_micro_edits(signed_in: MicroHandoff) -> None:
    signed_in.add_micro(scheduled_for=NOW + timedelta(hours=2))

    html = signed_in.client.get("/").text

    assert micro_links(html) == []
    assert "No micro edits right now." in html
    assert "Nothing ready right now." in html


# Micro page


def test_micro_page_shows_preview_save_caption_text_and_reminder(signed_in: MicroHandoff) -> None:
    render_id = signed_in.add_micro(caption="You Dont Dance", text="wait for it")

    response = signed_in.client.get(f"/micro/{render_id}")

    assert response.status_code == 200
    html = response.text
    assert f'src="/micro/{render_id}/preview"' in html
    assert f'href="/micro/{render_id}/download"' in html
    assert 'id="save-video"' in html
    assert f'download="micro_{render_id}.mp4"' in html
    assert re.search(r'<p class="caption" id="caption">You Dont Dance</p>', html)
    assert html.count("Copy caption") == 1
    assert "wait for it" in html
    assert "Turn on Trial before you share it." in html
    assert "Reject" not in html
    assert "/link" not in html
    assert 'name="url"' not in html


def test_micro_page_keeps_save_after_the_first_download(signed_in: MicroHandoff) -> None:
    render_id = signed_in.add_micro(status=MicroRenderStatus.DOWNLOADED)

    html = signed_in.client.get(f"/micro/{render_id}").text

    assert f'href="/micro/{render_id}/download"' in html


@pytest.mark.parametrize(
    "status",
    [MicroRenderStatus.RENDERING, MicroRenderStatus.SUPERSEDED, MicroRenderStatus.FAILED],
)
def test_micro_page_hides_save_when_not_downloadable(
    signed_in: MicroHandoff, status: MicroRenderStatus
) -> None:
    render_id = signed_in.add_micro(status=status, with_file=False)

    response = signed_in.client.get(f"/micro/{render_id}")

    assert response.status_code == 200
    assert f'href="/micro/{render_id}/download"' not in response.text
    assert "Save video" not in response.text


def test_unknown_micro_render_is_404(signed_in: MicroHandoff) -> None:
    assert signed_in.client.get("/micro/999").status_code == 404
    assert signed_in.client.get("/micro/999/preview").status_code == 404
    assert signed_in.client.get("/micro/999/download").status_code == 404


# Preview


def test_micro_preview_serves_the_video_and_changes_nothing(signed_in: MicroHandoff) -> None:
    render_id = signed_in.add_micro()

    response = signed_in.client.get(f"/micro/{render_id}/preview")

    assert response.status_code == 200
    assert response.content == VIDEO_BYTES
    assert response.headers["content-type"] == "video/mp4"
    assert "attachment" not in response.headers.get("content-disposition", "")
    render = signed_in.micro(render_id)
    assert render.status == MicroRenderStatus.READY
    assert render.download_count == 0
    assert render.downloaded_at is None


def test_micro_preview_supports_range_requests(signed_in: MicroHandoff) -> None:
    render_id = signed_in.add_micro()

    response = signed_in.client.get(
        f"/micro/{render_id}/preview", headers={"Range": "bytes=100-199"}
    )

    assert response.status_code == 206
    assert response.content == VIDEO_BYTES[100:200]
    assert response.headers["content-range"] == f"bytes 100-199/{len(VIDEO_BYTES)}"
    assert signed_in.micro(render_id).download_count == 0


def test_micro_preview_without_a_file_is_404(signed_in: MicroHandoff) -> None:
    render_id = signed_in.add_micro(with_file=False)

    assert signed_in.client.get(f"/micro/{render_id}/preview").status_code == 404


# Download


def test_first_micro_download_marks_it_downloaded(signed_in: MicroHandoff) -> None:
    render_id = signed_in.add_micro()

    response = signed_in.client.get(f"/micro/{render_id}/download")

    assert response.status_code == 200
    assert response.content == VIDEO_BYTES
    assert response.headers["content-type"] == "video/mp4"
    assert (
        response.headers["content-disposition"] == f'attachment; filename="micro_{render_id}.mp4"'
    )
    render = signed_in.micro(render_id)
    assert render.status == MicroRenderStatus.DOWNLOADED
    assert render.downloaded_at == NOW
    assert render.download_count == 1


def test_repeat_micro_downloads_count_but_keep_the_first_downloaded_at(
    signed_in: MicroHandoff,
) -> None:
    render_id = signed_in.add_micro()
    signed_in.client.get(f"/micro/{render_id}/download")
    signed_in.clock.now = NOW + timedelta(hours=2)

    first_repeat = signed_in.client.get(f"/micro/{render_id}/download")
    second_repeat = signed_in.client.get(f"/micro/{render_id}/download")

    assert first_repeat.status_code == 200
    assert second_repeat.content == VIDEO_BYTES
    render = signed_in.micro(render_id)
    assert render.status == MicroRenderStatus.DOWNLOADED
    assert render.downloaded_at == NOW
    assert render.download_count == 3


@pytest.mark.parametrize(
    "status",
    [MicroRenderStatus.RENDERING, MicroRenderStatus.SUPERSEDED, MicroRenderStatus.FAILED],
)
def test_micro_download_is_409_unless_ready_or_downloaded(
    signed_in: MicroHandoff, status: MicroRenderStatus
) -> None:
    render_id = signed_in.add_micro(status=status)

    response = signed_in.client.get(f"/micro/{render_id}/download")

    assert response.status_code == 409
    render = signed_in.micro(render_id)
    assert render.status == status
    assert render.download_count == 0
    assert render.downloaded_at is None


def test_micro_download_without_a_file_is_404_and_changes_nothing(
    signed_in: MicroHandoff,
) -> None:
    render_id = signed_in.add_micro(with_file=False)

    response = signed_in.client.get(f"/micro/{render_id}/download")

    assert response.status_code == 404
    render = signed_in.micro(render_id)
    assert render.status == MicroRenderStatus.READY
    assert render.download_count == 0
    assert render.downloaded_at is None


def test_a_downloaded_micro_leaves_the_home_list(signed_in: MicroHandoff) -> None:
    render_id = signed_in.add_micro()
    assert micro_links(signed_in.client.get("/").text) == [render_id]

    signed_in.client.get(f"/micro/{render_id}/download")

    assert micro_links(signed_in.client.get("/").text) == []
