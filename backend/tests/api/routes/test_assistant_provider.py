import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from app.assistant import provider
from app.assistant.provider import DeepSeekProvider, ProviderError, post_json
from app.core.config import settings


def mock_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    client_class = httpx.Client
    monkeypatch.setattr(
        provider.httpx,
        "Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.parametrize("use_tools", [False, True])
def test_chat_protocol_and_usage(
    monkeypatch: pytest.MonkeyPatch, use_tools: bool
) -> None:
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-only")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-only"
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"]["type"] == "json_object"
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )

    mock_transport(monkeypatch, handler)
    llm = DeepSeekProvider()
    from app.assistant.tools import tool_definitions

    assert llm.chat(
        [{"role": "user", "content": "测试"}],
        tool_definitions() if use_tools else None,
    ) == {"content": "{}"}
    assert llm.prompt_tokens == 5 and llm.completion_tokens == 3


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "provider_auth"),
        (403, "provider_auth"),
        (400, "provider_unavailable"),
        (302, "provider_unavailable"),
    ],
)
def test_provider_errors_do_not_expose_bodies(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    mock_transport(
        monkeypatch,
        lambda request: httpx.Response(status, text="sensitive-internal-details"),
    )
    with pytest.raises(ProviderError) as caught:
        post_json("https://api.deepseek.com/chat/completions", "secret", {}, timeout=1)
    assert caught.value.code == code
    assert "sensitive" not in str(caught.value)


def test_retry_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(503)

    mock_transport(monkeypatch, handler)
    with pytest.raises(ProviderError):
        post_json("https://api.deepseek.com/chat/completions", "secret", {}, timeout=1)
    assert len(attempts) == 2


def test_timeout_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ReadTimeout("internal-sensitive-address")

    mock_transport(monkeypatch, handler)
    with pytest.raises(ProviderError, match="provider_timeout"):
        post_json("https://api.deepseek.com/chat/completions", "secret", {}, timeout=1)
    assert len(attempts) == 1


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"choices": []},
        {
            "choices": [
                {"finish_reason": "length", "message": {"content": "incomplete"}}
            ]
        },
    ],
)
def test_invalid_response(monkeypatch: pytest.MonkeyPatch, body: Any) -> None:
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-only")
    mock_transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(ProviderError, match="provider_invalid_response"):
        DeepSeekProvider().chat([])


def test_embedding_response_reorders_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "AI_EMBEDDING_URL", "https://embedding.example/embeddings"
    )
    mock_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0, 1]},
                    {"index": 0, "embedding": [1, 0]},
                ]
            },
        ),
    )
    assert provider.embed(["第一段", "第二段"]) == [[1, 0], [0, 1]]


def test_oversized_response_stops_reading_and_closes_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CountingStream(httpx.SyncByteStream):
        chunks_read = 0
        closed = False

        def __iter__(self) -> Iterator[bytes]:
            for _ in range(400):
                self.chunks_read += 1
                yield b" " * 8192

        def close(self) -> None:
            self.closed = True

    stream = CountingStream()
    mock_transport(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    with pytest.raises(ProviderError, match="provider_response_too_large"):
        post_json(
            "https://api.deepseek.com/chat/completions", "test-only", {}, timeout=1
        )
    assert stream.chunks_read == 245
    assert stream.closed


def test_response_at_byte_limit_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"{}" + b" " * 1_999_998
    mock_transport(monkeypatch, lambda request: httpx.Response(200, content=content))
    assert (
        post_json(
            "https://api.deepseek.com/chat/completions", "test-only", {}, timeout=1
        )
        == {}
    )
