from __future__ import annotations

import httpx
import pytest

from app.providers.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider


class _FailingAsyncClient:
    last_timeout: httpx.Timeout | None = None

    def __init__(self, *args, timeout=None, **kwargs) -> None:
        _FailingAsyncClient.last_timeout = timeout

    async def __aenter__(self) -> _FailingAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, **kwargs):
        raise httpx.ConnectError(
            "All connection attempts failed",
            request=httpx.Request("POST", url),
        )


@pytest.mark.asyncio
async def test_llm_provider_raises_clear_unreachable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FailingAsyncClient)
    provider = OpenAICompatibleProvider(
        base_url="http://llm-host:8000/v1",
        model="dummy",
        timeout=12.0,
        connect_timeout=2.5,
    )

    with pytest.raises(RuntimeError, match="LLM endpoint unreachable at http://llm-host:8000/v1"):
        await provider.generate_text("Merhaba")

    assert _FailingAsyncClient.last_timeout is not None
    assert _FailingAsyncClient.last_timeout.connect == 2.5


@pytest.mark.asyncio
async def test_embedding_provider_raises_clear_unreachable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FailingAsyncClient)
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://embed-host:9000/v1",
        model="dummy-embed",
        timeout=9.0,
        connect_timeout=1.5,
    )

    with pytest.raises(RuntimeError, match="Embedding endpoint unreachable at http://embed-host:9000/v1"):
        await provider.embed_texts(["aktif çalışanlar"])

    assert _FailingAsyncClient.last_timeout is not None
    assert _FailingAsyncClient.last_timeout.connect == 1.5