from __future__ import annotations

import pytest

from app.domain.query_plan import FilterOp, QueryPlan
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


@pytest.mark.asyncio
async def test_plan_like_payload_missing_intent_is_salvaged() -> None:
    provider = OpenAICompatibleProvider(base_url="http://dummy", model="dummy")

    async def _fake_chat(*_args, **_kwargs) -> str:
        return '{"table":"PO_HEADERS_ALL","select_columns":["PO_HEADER_ID"],"filters":[],"aggregations":[],"group_by":[],"order_by":[],"joins":[],"limit":10,"needs_clarification":false,"clarification_message":null}'

    provider._chat_completion = _fake_chat  # type: ignore[method-assign]
    plan = await provider.generate_structured("Kullanıcı sorusu: Acik siparisleri getir", QueryPlan)

    assert plan.needs_clarification is False
    assert plan.table == "PO_HEADERS_ALL"
    assert plan.intent == "Acik siparisleri getir"
    assert provider.last_structured_parse_taxonomy == "missing_required_field"
    assert provider.last_structured_salvage_applied is True


@pytest.mark.asyncio
async def test_column_object_and_date_delta_are_salvaged() -> None:
    provider = OpenAICompatibleProvider(base_url="http://dummy", model="dummy")

    async def _fake_chat(*_args, **_kwargs) -> str:
        return """{
  "intent": "Son 30 gunde acilan siparisler",
  "table": "PO_HEADERS_ALL",
  "select_columns": [
    {"column": "PO_HEADER_ID", "table": "PO_HEADERS_ALL"},
    {"column": "SEGMENT1", "table": "PO_HEADERS_ALL"}
  ],
  "filters": [
    {"column": "CREATION_DATE", "op": ">=", "value": {"type": "date_delta", "units": 30, "unit": "day"}, "table": "PO_HEADERS_ALL"}
  ],
  "aggregations": [],
  "group_by": [],
  "order_by": [],
  "joins": [],
  "limit": 100,
  "needs_clarification": false,
  "clarification_message": null
}"""

    provider._chat_completion = _fake_chat  # type: ignore[method-assign]
    plan = await provider.generate_structured("Kullanıcı sorusu: Son 30 gunde acilan siparisler", QueryPlan)

    assert plan.select_columns == ["PO_HEADER_ID", "SEGMENT1"]
    assert plan.filters[0].op == FilterOp.GTE
    assert isinstance(plan.filters[0].value, str)


@pytest.mark.asyncio
async def test_free_text_clarification_is_salvaged() -> None:
    provider = OpenAICompatibleProvider(base_url="http://dummy", model="dummy")

    async def _fake_chat(*_args, **_kwargs) -> str:
        return "Lutfen hangi tarih araligini istediginizi netlestirin."

    provider._chat_completion = _fake_chat  # type: ignore[method-assign]
    plan = await provider.generate_structured("Kullanıcı sorusu: Siparisler", QueryPlan)

    assert plan.needs_clarification is True
    assert "tarih aral" in (plan.clarification_message or "").lower()
    assert provider.last_structured_parse_taxonomy == "free_text_clarification_only"
    assert provider.last_structured_salvage_applied is True


@pytest.mark.asyncio
async def test_multi_object_response_sets_taxonomy_when_salvaged() -> None:
    provider = OpenAICompatibleProvider(base_url="http://dummy", model="dummy")

    async def _fake_chat(*_args, **_kwargs) -> str:
        return '{"note":"ignore"}\n{"table":"PO_HEADERS_ALL","select_columns":["PO_HEADER_ID"],"filters":[],"aggregations":[],"group_by":[],"order_by":[],"joins":[],"limit":10,"needs_clarification":false,"clarification_message":null}'

    provider._chat_completion = _fake_chat  # type: ignore[method-assign]
    plan = await provider.generate_structured("Kullanıcı sorusu: Siparisler", QueryPlan)

    assert plan.table == "PO_HEADERS_ALL"
    assert provider.last_structured_parse_taxonomy == "multi_object_response"
    assert provider.last_structured_salvage_applied is True
