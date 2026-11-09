import httpx
import pytest
import respx

from engine.http import request_with_retries

URL = "https://api.example.test/v1/thing"


def send(sleeps: list[float], max_retries: int = 3) -> httpx.Response:
    with httpx.Client() as client:
        return request_with_retries(
            client, "GET", URL, max_retries=max_retries, backoff_base_s=0.5, sleep=sleeps.append
        )


@respx.mock
def test_retries_429_and_5xx_with_exponential_backoff_then_succeeds() -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(503), httpx.Response(200, json={"ok": 1})]
    )
    sleeps: list[float] = []
    response = send(sleeps)
    assert response.json() == {"ok": 1}
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]


@respx.mock
def test_4xx_is_raised_immediately_without_retry() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    sleeps: list[float] = []
    with pytest.raises(httpx.HTTPStatusError) as raised:
        send(sleeps)
    assert raised.value.response.status_code == 404
    assert route.call_count == 1
    assert sleeps == []


@respx.mock
def test_gives_up_after_max_retries_and_raises_the_last_error() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(500))
    sleeps: list[float] = []
    with pytest.raises(httpx.HTTPStatusError):
        send(sleeps, max_retries=2)
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]


@respx.mock
def test_transport_errors_are_retried() -> None:
    route = respx.get(URL).mock(side_effect=[httpx.ConnectError("down"), httpx.Response(200)])
    sleeps: list[float] = []
    assert send(sleeps).status_code == 200
    assert route.call_count == 2

    respx.get(URL).mock(side_effect=httpx.ConnectError("still down"))
    with pytest.raises(httpx.ConnectError):
        send([], max_retries=1)
