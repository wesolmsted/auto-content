import base64
import logging
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import SecretStr

from engine.config import load_settings
from engine.notify.base import NotificationError
from engine.notify.models import Notification
from engine.notify.ntfy import NtfyNotifier, ntfy_notifier_from_settings

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SERVER = "https://ntfy.example.test"
TOPIC = "ace-topic-9f3b2c71d0e84a6b"
TOKEN = "tk_secret_5d1e0a77"
TOPIC_URL = f"{SERVER}/{TOPIC}"

NOTIFICATION = Notification(
    title="Hand-off ready: Night Drive - every_beat high_contrast",
    body="7s for IG Trial + TikTok",
    click_url="https://mac.tailnet.test/handoff/12",
    tags=("clapper", "ig_trial", "tiktok"),
)


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with httpx.Client() as client:
        yield client


def make_notifier(
    client: httpx.Client, token: str | None = TOKEN, sleeps: list[float] | None = None
) -> NtfyNotifier:
    return NtfyNotifier(
        SERVER + "/",
        TOPIC,
        token,
        client,
        max_retries=2,
        backoff_base_s=0.5,
        sleep=(sleeps if sleeps is not None else []).append,
    )


@respx.mock
def test_posts_the_body_to_the_topic_with_title_click_tags_and_bearer_token(
    client: httpx.Client,
) -> None:
    route = respx.post(TOPIC_URL).mock(return_value=httpx.Response(200, json={"id": "x"}))
    make_notifier(client).send(NOTIFICATION)

    assert route.call_count == 1
    request = route.calls.last.request
    assert request.content == b"7s for IG Trial + TikTok"
    assert request.headers["Title"] == "Hand-off ready: Night Drive - every_beat high_contrast"
    assert request.headers["Click"] == "https://mac.tailnet.test/handoff/12"
    assert request.headers["Tags"] == "clapper,ig_trial,tiktok"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"


@respx.mock
def test_without_token_click_or_tags_those_headers_are_omitted(client: httpx.Client) -> None:
    route = respx.post(TOPIC_URL).mock(return_value=httpx.Response(200))
    make_notifier(client, token=None).send(Notification(title="Test", body="hello"))

    headers = route.calls.last.request.headers
    assert headers["Title"] == "Test"
    assert "Authorization" not in headers
    assert "Click" not in headers
    assert "Tags" not in headers


@respx.mock
def test_non_ascii_title_is_sent_as_an_rfc2047_encoded_word(client: httpx.Client) -> None:
    route = respx.post(TOPIC_URL).mock(return_value=httpx.Response(200))
    title = "Hand-off ready: Café · every_beat"
    make_notifier(client).send(Notification(title=title, body="7s ✨"))

    request = route.calls.last.request
    encoded = base64.b64encode(title.encode()).decode()
    assert request.headers["Title"] == f"=?UTF-8?B?{encoded}?="
    assert request.content == "7s ✨".encode()


@respx.mock
def test_5xx_is_retried_with_backoff(client: httpx.Client) -> None:
    route = respx.post(TOPIC_URL).mock(
        side_effect=[httpx.Response(502), httpx.Response(503), httpx.Response(200)]
    )
    sleeps: list[float] = []
    make_notifier(client, sleeps=sleeps).send(NOTIFICATION)
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]


@respx.mock
def test_persistent_5xx_raises_notification_error(client: httpx.Client) -> None:
    route = respx.post(TOPIC_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(NotificationError, match="500"):
        make_notifier(client).send(NOTIFICATION)
    assert route.call_count == 3


@respx.mock
def test_4xx_raises_notification_error_without_retry(client: httpx.Client) -> None:
    route = respx.post(TOPIC_URL).mock(return_value=httpx.Response(403))
    sleeps: list[float] = []
    with pytest.raises(NotificationError, match="403"):
        make_notifier(client, sleeps=sleeps).send(NOTIFICATION)
    assert route.call_count == 1
    assert sleeps == []


@respx.mock
def test_transport_failure_raises_notification_error(client: httpx.Client) -> None:
    respx.post(TOPIC_URL).mock(side_effect=httpx.ConnectError(f"cannot reach {TOPIC_URL}"))
    with pytest.raises(NotificationError) as raised:
        make_notifier(client).send(NOTIFICATION)
    assert TOPIC not in str(raised.value)


@respx.mock
def test_topic_and_token_never_appear_in_logs_or_errors(
    client: httpx.Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    respx.post(TOPIC_URL).mock(side_effect=[httpx.Response(200), httpx.Response(401)])
    notifier = make_notifier(client)
    notifier.send(NOTIFICATION)
    with pytest.raises(NotificationError) as raised:
        notifier.send(NOTIFICATION)
    error_chain = f"{raised.value!s} {raised.value.__cause__!s} {raised.value.__context__!s}"

    assert caplog.records, "expected the send to be logged"
    for text in (caplog.text, error_chain):
        assert TOPIC not in text
        assert TOKEN not in text


def test_from_settings_uses_the_configured_server_topic_token_and_retries(
    client: httpx.Client,
) -> None:
    settings = load_settings(REPO_CONFIG_DIR, env_file=None).model_copy(
        update={
            "ntfy_server": SERVER,
            "ntfy_topic": SecretStr(TOPIC),
            "ntfy_token": SecretStr(TOKEN),
        }
    )
    notifier = ntfy_notifier_from_settings(settings, client)
    assert (notifier.server, notifier.topic, notifier.token) == (SERVER, TOPIC, TOKEN)
    assert notifier.max_retries == settings.notify.max_retries
    assert notifier.backoff_base_s == settings.http.backoff_base_s


@pytest.mark.parametrize("missing", ["ntfy_server", "ntfy_topic"])
def test_from_settings_names_the_missing_variable(client: httpx.Client, missing: str) -> None:
    settings = load_settings(REPO_CONFIG_DIR, env_file=None).model_copy(
        update={"ntfy_server": SERVER, "ntfy_topic": SecretStr(TOPIC), missing: None}
    )
    with pytest.raises(ValueError, match=missing.upper()):
        ntfy_notifier_from_settings(settings, client)
