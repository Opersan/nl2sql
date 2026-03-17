from __future__ import annotations

import pytest

from app.domain.query_plan import QueryPlan
from app.providers.llm.openai_compatible import OpenAICompatibleProvider, _safe_extract_plan_dict


def test_safe_extract_from_fenced_json() -> None:
    raw = """```json
{"intent":"list","table":"PO_HEADERS_ALL","select_columns":["po_header_id"]}
```"""
    extracted = _safe_extract_plan_dict(raw)
    assert extracted is not None
    assert extracted["intent"] == "list"


def test_safe_extract_nested_json() -> None:
    raw = '{"reasoning":"x", "payload": {"plan": {"intent": "x", "table": "PO_HEADERS_ALL"}}}'
    extracted = _safe_extract_plan_dict(raw)
    assert extracted is not None
    assert extracted["intent"] == "x"


def test_safe_extract_reasoning_before_json() -> None:
    raw = "<think>Analysis</think> some text {\"intent\":\"x\",\"table\":\"PO_HEADERS_ALL\"}"
    extracted = _safe_extract_plan_dict(raw)
    assert extracted is not None
    assert extracted["intent"] == "x"


@pytest.mark.asyncio
async def test_empty_json_falls_back_to_clarification() -> None:
    provider = OpenAICompatibleProvider(base_url="http://dummy", model="dummy")

    async def _fake_chat(*_args, **_kwargs) -> str:
        return "{}"

    provider._chat_completion = _fake_chat  # type: ignore[method-assign]
    plan = await provider.generate_structured("x", QueryPlan)

    assert plan.needs_clarification is True
    assert plan.intent == "clarification_required"
    assert plan.clarification_message == "Soruyu biraz daha detaylandırabilir misiniz?"


@pytest.mark.asyncio
async def test_invalid_json_falls_back_to_clarification() -> None:
    provider = OpenAICompatibleProvider(base_url="http://dummy", model="dummy")

    async def _fake_chat(*_args, **_kwargs) -> str:
        return "{invalid"

    provider._chat_completion = _fake_chat  # type: ignore[method-assign]
    plan = await provider.generate_structured("x", QueryPlan)

    assert plan.needs_clarification is True
    assert plan.intent == "clarification_required"
