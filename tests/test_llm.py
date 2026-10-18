import base64
import json

import httpx
import pytest
import respx

from greekpipe.llm import LLMAuthError, LLMBadOutput, LLMClient, LLMTransient, image_part, text_part

BASE = "https://openrouter.ai/api/v1"


def ok(content, **extra):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], **extra})


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client(sleeps):
    return LLMClient("key", sleep=sleeps.append)


def call(client):
    return client.chat_json("m", "sys", [text_part("hi")], "transcript")


@respx.mock(base_url=BASE)
def test_chat_json_parses_fenced_json(respx_mock, client):
    route = respx_mock.post("/chat/completions").mock(
        return_value=ok('```json\n{"a":1}\n```', usage={"cost": 0.001})
    )
    assert call(client) == {"a": 1}
    assert client.last_cost == 0.001
    assert client.last_usage == {"cost": 0.001}
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer key"
    assert req.headers["x-title"] == "greek-course-companion"
    body = json.loads(req.content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert "schema_name" not in body


@respx.mock(base_url=BASE)
def test_cost_accumulates_and_missing_cost_is_zero(respx_mock, client):
    respx_mock.post("/chat/completions").mock(
        side_effect=[ok("{}", usage={"cost": 0.5}), ok("{}", usage={"cost": 0.25}), ok("{}")]
    )
    for _ in range(3):
        call(client)
    assert client.last_cost == 0.75


@respx.mock(base_url=BASE)
def test_chat_json_retries_on_429_then_succeeds(respx_mock, client, sleeps):
    route = respx_mock.post("/chat/completions").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "0"}), ok('{"a":1}')]
    )
    assert call(client) == {"a": 1}
    assert route.call_count == 2
    assert sleeps == [2]


@respx.mock(base_url=BASE)
def test_retry_after_larger_than_backoff_wins(respx_mock, client, sleeps):
    respx_mock.post("/chat/completions").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "30"}), ok("{}")]
    )
    call(client)
    assert sleeps == [30]


@respx.mock(base_url=BASE)
def test_chat_json_raises_transient_after_max_retries(respx_mock, client, sleeps):
    route = respx_mock.post("/chat/completions").mock(return_value=httpx.Response(503))
    with pytest.raises(LLMTransient):
        call(client)
    assert route.call_count == 5
    assert sleeps == [2, 4, 8, 16]


@respx.mock(base_url=BASE)
def test_timeout_is_retried(respx_mock, client):
    route = respx_mock.post("/chat/completions").mock(
        side_effect=[httpx.ReadTimeout("slow"), ok("{}")]
    )
    assert call(client) == {}
    assert route.call_count == 2


@respx.mock(base_url=BASE)
def test_200_with_error_body_429_is_transient(respx_mock, client):
    route = respx_mock.post("/chat/completions").mock(
        side_effect=[httpx.Response(200, json={"error": {"code": 429, "message": "slow"}}), ok("{}")]
    )
    assert call(client) == {}
    assert route.call_count == 2


@respx.mock(base_url=BASE)
def test_200_with_error_body_other_code_is_bad_output(respx_mock, client):
    respx_mock.post("/chat/completions").mock(
        return_value=httpx.Response(200, json={"error": {"code": 400, "message": "nope"}})
    )
    with pytest.raises(LLMBadOutput):
        call(client)


@respx.mock(base_url=BASE)
def test_chat_json_bad_output(respx_mock, client):
    respx_mock.post("/chat/completions").mock(return_value=ok("sorry"))
    with pytest.raises(LLMBadOutput, match="transcript"):
        call(client)


@respx.mock(base_url=BASE)
def test_non_object_json_is_bad_output(respx_mock, client):
    respx_mock.post("/chat/completions").mock(return_value=ok("[1,2]"))
    with pytest.raises(LLMBadOutput):
        call(client)


def test_image_part_is_data_url(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8abc")
    part = image_part(p)
    assert part["type"] == "image_url"
    assert part["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8abc").decode()


def test_text_part():
    assert text_part("x") == {"type": "text", "text": "x"}


@pytest.mark.parametrize("content", [None, ["x"]])
@respx.mock(base_url=BASE)
def test_null_content_is_bad_output(respx_mock, client, content):
    respx_mock.post("/chat/completions").mock(return_value=ok(content))
    with pytest.raises(LLMBadOutput, match="non-text"):
        call(client)


@respx.mock(base_url=BASE)
def test_non_json_200_body_is_transient(respx_mock, client, sleeps):
    route = respx_mock.post("/chat/completions").mock(
        return_value=httpx.Response(200, text="<html>gateway</html>")
    )
    with pytest.raises(LLMTransient):
        call(client)
    assert route.call_count == 5
    assert sleeps == [2, 4, 8, 16]


@respx.mock(base_url=BASE)
def test_non_dict_error_is_bad_output(respx_mock, client):
    respx_mock.post("/chat/completions").mock(
        return_value=httpx.Response(200, json={"error": "boom"})
    )
    with pytest.raises(LLMBadOutput):
        call(client)


@pytest.mark.parametrize("status", [401, 402, 403])
@respx.mock(base_url=BASE)
def test_401_raises_auth_error_without_retry(respx_mock, client, sleeps, status):
    route = respx_mock.post("/chat/completions").mock(return_value=httpx.Response(status))
    with pytest.raises(LLMAuthError):
        call(client)
    assert route.call_count == 1
    assert sleeps == []


@respx.mock(base_url=BASE)
def test_200_body_402_is_auth_error(respx_mock, client):
    respx_mock.post("/chat/completions").mock(
        return_value=httpx.Response(200, json={"error": {"code": 402, "message": "credits"}})
    )
    with pytest.raises(LLMAuthError):
        call(client)


@respx.mock(base_url=BASE)
def test_408_is_transient(respx_mock, client):
    route = respx_mock.post("/chat/completions").mock(side_effect=[httpx.Response(408), ok("{}")])
    assert call(client) == {}
    assert route.call_count == 2
