import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpMockSequence

from engine.config import DriveSettings, load_settings
from engine.drive.google import GoogleDriveClient
from engine.drive.models import DriveFile

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
MB = 1024 * 1024

Response = tuple[dict[str, str], bytes | str]


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr("googleapiclient.http.time.sleep", sleeps.append)
    return sleeps


def drive_settings(**update: Any) -> DriveSettings:
    return load_settings(REPO_CONFIG_DIR, env_file=None).drive.model_copy(update=update)


def client_for(responses: list[Response], **settings: Any) -> tuple[GoogleDriveClient, Any]:
    http = HttpMockSequence(responses)
    service = build("drive", "v3", http=http, static_discovery=True)
    return GoogleDriveClient(None, drive_settings(**settings), service=service), http


def query_of(uri: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(uri).query)


def ok(body: dict[str, Any]) -> Response:
    return {"status": "200"}, json.dumps(body)


def test_list_videos_follows_pages_and_maps_fields() -> None:
    page_1 = {
        "nextPageToken": "page-2",
        "files": [
            {
                "id": "f1",
                "name": "beach.mov",
                "mimeType": "video/quicktime",
                "md5Checksum": "aaa",
                "size": "123456",
                "createdTime": "2026-11-01T10:00:00.000Z",
                "description": "sunset",
            }
        ],
    }
    page_2 = {"files": [{"id": "f2", "name": "city.mp4", "mimeType": "video/mp4"}]}
    drive, http = client_for([ok(page_1), ok(page_2)])

    videos = drive.list_videos("inbox-folder")

    assert videos == [
        DriveFile(
            id="f1",
            name="beach.mov",
            mime_type="video/quicktime",
            md5="aaa",
            size_bytes=123456,
            created_at=datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
            description="sunset",
        ),
        DriveFile(id="f2", name="city.mp4", mime_type="video/mp4"),
    ]
    first_query, second_query = (query_of(uri) for uri, *_ in http.request_sequence)
    assert first_query["q"] == [
        "'inbox-folder' in parents and mimeType contains 'video/' and trashed = false"
    ]
    assert "md5Checksum" in first_query["fields"][0]
    assert "pageToken" not in first_query
    assert second_query["pageToken"] == ["page-2"]


def test_list_videos_of_an_empty_folder_is_empty() -> None:
    drive, _ = client_for([ok({"files": []})])
    assert drive.list_videos("empty") == []


def test_list_videos_retries_server_errors() -> None:
    drive, http = client_for(
        [
            ({"status": "503"}, ""),
            ok({"files": [{"id": "f1", "name": "a.mp4", "mimeType": "video/mp4"}]}),
        ]
    )
    assert [video.id for video in drive.list_videos("inbox")] == ["f1"]
    assert len(http.request_sequence) == 2


def chunk_response(data: bytes, start: int, total: int) -> Response:
    end = start + len(data) - 1
    return {"status": "206", "content-range": f"bytes {start}-{end}/{total}"}, data


def test_download_fetches_in_chunks_and_writes_dest(tmp_path: Path) -> None:
    content = bytes(range(256)) * (10 * 1024)  # 2.5 MB
    drive, http = client_for(
        [
            chunk_response(content[:MB], 0, len(content)),
            chunk_response(content[MB : 2 * MB], MB, len(content)),
            chunk_response(content[2 * MB :], 2 * MB, len(content)),
        ],
        download_chunk_mb=1,
    )
    dest = tmp_path / "scratch" / "footage_1.mp4"
    dest.parent.mkdir()

    assert drive.download("file-1", dest) == dest

    assert dest.read_bytes() == content
    ranges = [headers["range"] for _, _, _, headers in http.request_sequence]
    assert ranges == [
        f"bytes=0-{MB - 1}",
        f"bytes={MB}-{2 * MB - 1}",
        f"bytes={2 * MB}-{3 * MB - 1}",
    ]
    assert "alt=media" in http.request_sequence[0][0]
    assert list(dest.parent.iterdir()) == [dest]


def test_download_retries_a_failed_chunk_with_backoff(
    tmp_path: Path, no_retry_sleep: list[float]
) -> None:
    content = b"x" * (MB + 10)
    drive, http = client_for(
        [
            chunk_response(content[:MB], 0, len(content)),
            ({"status": "500"}, ""),
            chunk_response(content[MB:], MB, len(content)),
        ],
        download_chunk_mb=1,
        max_retries=2,
    )
    dest = tmp_path / "out.mp4"

    drive.download("file-1", dest)

    assert dest.read_bytes() == content
    assert http.request_sequence[2][3]["range"] == f"bytes={MB}-{2 * MB - 1}"
    assert len(no_retry_sleep) == 1


def test_download_that_keeps_failing_raises_and_leaves_no_dest(tmp_path: Path) -> None:
    drive, _ = client_for([({"status": "500"}, ""), ({"status": "500"}, "")], max_retries=1)
    dest = tmp_path / "out.mp4"

    with pytest.raises(HttpError):
        drive.download("file-1", dest)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_move_replaces_all_current_parents_with_the_target() -> None:
    drive, http = client_for(
        [ok({"parents": ["inbox", "other"]}), ok({"id": "f1", "parents": ["processed"]})]
    )

    drive.move("f1", "processed")

    (get_uri, get_method, *_), (update_uri, update_method, *_) = http.request_sequence
    assert get_method == "GET"
    assert "/files/f1" in get_uri
    assert update_method == "PATCH"
    assert "/files/f1" in update_uri
    update_query = query_of(update_uri)
    assert update_query["addParents"] == ["processed"]
    assert update_query["removeParents"] == ["inbox,other"]
