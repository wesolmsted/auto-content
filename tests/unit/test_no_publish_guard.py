"""Guard for DESIGN.md principle 6: the system never posts to any platform."""

import re
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parents[2] / "engine"


PUBLISH_PATTERNS = [
    re.compile(r"media_publish"),
    re.compile(r"/post/publish"),
    re.compile(r"upload[-_]?post", re.IGNORECASE),
    re.compile(r"""\.post\(\s*f?["'][^"'\n]*/media["']"""),
    re.compile(r"""request\(\s*["']POST["']\s*,\s*f?["'][^"'\n]*/media["']"""),
]


def find_publish_calls(source: str) -> list[str]:
    return [match.group(0) for pattern in PUBLISH_PATTERNS for match in pattern.finditer(source)]


@pytest.mark.parametrize(
    "snippet",
    [
        'client.post(f"{GRAPH}/{ig_user_id}/media_publish", data=payload)',
        'url = "https://open.tiktokapis.com/v2/post/publish/video/init/"',
        'httpx.post(f"https://graph.facebook.com/v21.0/{ig_user_id}/media", params=p)',
        'self._client.request("POST", f"/{self.ig_user_id}/media", json=body)',
        'BASE = "https://api.upload-post.com/api/upload"',
        "from upload_post import UploadPostClient",
    ],
)
def test_guard_flags_publish_endpoints(snippet: str) -> None:
    assert find_publish_calls(snippet) != []


@pytest.mark.parametrize(
    "snippet",
    [
        'client.get(f"{GRAPH}/{ig_user_id}/media", params={"fields": "id,permalink"})',
        'client.get(f"{GRAPH}/{media_id}/insights", params={"metric": "views"})',
        'client.post("https://open.tiktokapis.com/v2/video/list/", json={"max_count": 20})',
        'client.post(f"{server}/{topic}", content=body)',
    ],
)
def test_guard_allows_read_only_calls(snippet: str) -> None:
    assert find_publish_calls(snippet) == []


def test_engine_source_has_no_publish_endpoints() -> None:
    offenders = {
        str(path.relative_to(ENGINE_DIR)): hits
        for path in sorted(ENGINE_DIR.rglob("*.py"))
        if (hits := find_publish_calls(path.read_text()))
    }
    assert offenders == {}
