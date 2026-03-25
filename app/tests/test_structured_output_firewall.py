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
    assert provider.last_structured_parse_taxonomy == "missing_required_intent"
    assert provider.last_structured_salvage_applied is True


@pytest.mark.asyncio
async def test_malformed_but_recoverable_field_shapes_are_salvaged() -> None:
    provider = OpenAICompatibleProvider(base_url="http://dummy", model="dummy")

    async def _fake_chat(*_args, **_kwargs) -> str:
        return """```json
{
  "table": "PO_HEADERS_ALL",
  "select_columns": {"column": "PO_HEADER_ID"},
  "filters": {"column": "AUTHORIZATION_STATUS", "op": "=", "value": "APPROVED", "table": "PO_HEADERS_ALL"},
  "aggregations": [],
  "group_by": [],
  "order_by": {"column": "PO_HEADER_ID", "direction": "DESC", "table": "PO_HEADERS_ALL"},
  "joins": [],
  "limit": 10,
  "needs_clarification": false,
  "clarification_message": null
}
```"""

    provider._chat_completion = _fake_chat  # type: ignore[method-assign]
    plan = await provider.generate_structured("Kullanıcı sorusu: Onaylı siparişleri getir", QueryPlan)

    assert plan.intent == "Onaylı siparişleri getir"
    assert plan.select_columns == ["PO_HEADER_ID"]
    assert len(plan.filters) == 1
    assert plan.order_by[0].column == "PO_HEADER_ID"
    assert provider.last_structured_parse_taxonomy == "missing_required_intent"
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
    assert provider.last_structured_parse_taxonomy == "free_text_instead_of_json"
    assert provider.last_structured_salvage_applied is True


@pytest.mark.asyncio
async def test_multi_object_response_is_rejected() -> None:
    provider = OpenAICompatibleProvider(base_url="http://dummy", model="dummy")

    async def _fake_chat(*_args, **_kwargs) -> str:
        return '{"note":"ignore"}\n{"table":"PO_HEADERS_ALL","select_columns":["PO_HEADER_ID"],"filters":[],"aggregations":[],"group_by":[],"order_by":[],"joins":[],"limit":10,"needs_clarification":false,"clarification_message":null}'

    provider._chat_completion = _fake_chat  # type: ignore[method-assign]
    plan = await provider.generate_structured("Kullanıcı sorusu: Siparisler", QueryPlan)

    assert plan.needs_clarification is True
    assert plan.intent == "clarification_required"
    assert provider.last_structured_parse_taxonomy == "multi_object_response"
    assert provider.last_structured_salvage_applied is False


@pytest.mark.asyncio
async def test_meta_json_shell_falls_back_with_free_text_taxonomy() -> None:
    provider = OpenAICompatibleProvider(base_url="http://dummy", model="dummy")

    async def _fake_chat(*_args, **_kwargs) -> str:
        return '{"response": ""}'

    provider._chat_completion = _fake_chat  # type: ignore[method-assign]
    plan = await provider.generate_structured("Kullanıcı sorusu: Bordrolu çalışanları listele", QueryPlan)

    assert plan.needs_clarification is True
    assert provider.last_structured_parse_taxonomy == "free_text_instead_of_json"


@pytest.mark.asyncio
async def test_already_valid_planner_output_remains_unchanged() -> None:
    provider = OpenAICompatibleProvider(base_url="http://dummy", model="dummy")

    async def _fake_chat(*_args, **_kwargs) -> str:
        return '{"intent":"Aktif çalışanları listele","table":"XXBT_PDKS_PER_DETAILS_V","select_columns":["SICIL_NO"],"filters":[{"column":"CIKIS_TARIHI","op":"IS_NULL","value":null,"table":"XXBT_PDKS_PER_DETAILS_V"}],"aggregations":[],"group_by":[],"order_by":[],"joins":[],"limit":100,"needs_clarification":false,"clarification_message":null}'

    provider._chat_completion = _fake_chat  # type: ignore[method-assign]
    plan = await provider.generate_structured("Kullanıcı sorusu: Aktif çalışanları listele", QueryPlan)

    assert plan.intent == "Aktif çalışanları listele"
    assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
    assert provider.last_structured_parse_taxonomy is None
    assert provider.last_structured_salvage_applied is False
