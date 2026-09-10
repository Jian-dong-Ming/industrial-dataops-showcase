"""Small HTTP adapters. Never expose provider bodies (which may contain secrets)."""

import json
import math
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import settings


class ProviderError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def post_json(
    url: str, key: str, payload: dict[str, Any], *, timeout: float
) -> dict[str, Any]:
    # No redirects: credentials must not be forwarded to another host.
    for attempt in range(2):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                with client.stream(
                    "POST",
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    json=payload,
                ) as response:
                    if response.status_code in {429, 502, 503, 504} and attempt == 0:
                        time.sleep(0.25)
                        continue
                    if response.status_code >= 300:
                        raise ProviderError(
                            "provider_auth"
                            if response.status_code in {401, 403}
                            else "provider_unavailable"
                        )
                    # Bound retained response bytes while reading, rather than
                    # checking only after httpx has buffered the entire body.
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        if len(content) + len(chunk) > 2_000_000:
                            raise ProviderError("provider_response_too_large")
                        content.extend(chunk)
                    body = json.loads(content)
                if not isinstance(body, dict):
                    raise ProviderError("provider_invalid_response")
                return body
        except httpx.TimeoutException as exc:
            # Retrying a timed-out generation may incur duplicate charges.
            raise ProviderError("provider_timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("provider_network") from exc
        except ValueError as exc:
            raise ProviderError("provider_invalid_response") from exc
    raise ProviderError("provider_unavailable")


class DeepSeekProvider:
    def __init__(self) -> None:
        if not settings.DEEPSEEK_API_KEY:
            raise ProviderError("provider_not_configured")
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": settings.DEEPSEEK_MODEL,
            "messages": messages,
            "max_tokens": 2000,
            "stream": False,
            "thinking": {"type": "disabled"},
            # A tool-enabled turn can also return a final answer immediately.
            # Keep JSON mode on every turn, not just the final forced turn.
            "response_format": {"type": "json_object"},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        body = post_json(
            "https://api.deepseek.com/chat/completions",
            settings.DEEPSEEK_API_KEY,
            payload,
            timeout=settings.AI_TIMEOUT_SECONDS,
        )
        try:
            usage = body.get("usage", {})
            self.prompt_tokens += max(0, int(usage.get("prompt_tokens", 0)))
            self.completion_tokens += max(0, int(usage.get("completion_tokens", 0)))
            choice = body["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict) or choice.get("finish_reason") == "length":
                raise ValueError
            return message
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("provider_invalid_response") from exc


def embedding_enabled() -> bool:
    return bool(settings.AI_EMBEDDING_URL and settings.AI_EMBEDDING_MODEL)


def embed(texts: list[str]) -> list[list[float]]:
    """Optional, separately configured OpenAI-compatible embedding service.

    DeepSeek chat is NOT claimed to provide embeddings. The operator configures
    this endpoint server-side; clients cannot supply URLs or credentials.
    """
    parsed = urlsplit(settings.AI_EMBEDDING_URL)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "host.docker.internal"}
    ):
        raise ProviderError("embedding_invalid_configuration")
    body = post_json(
        settings.AI_EMBEDDING_URL,
        settings.AI_EMBEDDING_API_KEY,
        {"model": settings.AI_EMBEDDING_MODEL, "input": texts},
        timeout=settings.AI_TIMEOUT_SECONDS,
    )
    try:
        rows = sorted(body["data"], key=lambda row: row["index"])
        if [row["index"] for row in rows] != list(range(len(texts))):
            raise ValueError
        vectors = [[float(number) for number in row["embedding"]] for row in rows]
        dimension = len(vectors[0])
        if not 1 <= dimension <= 4096 or any(
            len(vector) != dimension
            or not all(math.isfinite(number) for number in vector)
            or not any(vector)
            for vector in vectors
        ):
            raise ValueError
        return vectors
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProviderError("embedding_invalid_response") from exc
