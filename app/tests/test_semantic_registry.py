"""Unit tests for the externalized semantic registry.

Verifies that:
- The registry JSON loads and validates correctly.
- PO entity has the expected structure (intent_rules, intent_defaults).
- _infer_entity_intent correctly dispatches each test question.
- _apply_intent_defaults populates plan fields as expected.
- apply_semantic_normalization accepts a registry= injection kwarg.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.query_plan import AggregateFn, FilterOp, QueryPlan
from app.domain.semantic_models import SemanticRegistry
from app.services.semantic_planning import (
    _apply_intent_defaults,
    _infer_entity_intent,
    _load_registry,
    _match_entity,
    apply_semantic_normalization,
)


@pytest.fixture(autouse=True)
def _clear_registry_cache_between_tests() -> None:
    from app.services import semantic_planning

    semantic_planning._load_registry.cache_clear()
    yield
    semantic_planning._load_registry.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blank_po_plan() -> QueryPlan:
    return QueryPlan(
        table="PO_HEADERS_ALL",
        intent="satınalma siparişleri",
        needs_clarification=False,
    )


# ---------------------------------------------------------------------------
# Registry load
# ---------------------------------------------------------------------------

def test_registry_loads_successfully() -> None:
    registry = _load_registry()
    assert isinstance(registry, SemanticRegistry)
    assert registry.version
    assert len(registry.entities) >= 1


def test_registry_has_po_entity() -> None:
    registry = _load_registry()
    po = registry.get_entity("PO_PURCHASING")
    assert po is not None
    assert po.root_table == "PO_HEADERS_ALL"


def test_po_entity_has_keywords() -> None:
    registry = _load_registry()
    po = registry.get_entity("PO_PURCHASING")
    assert po is not None
    # 'po' was intentionally removed to prevent false-positive matching on words
    # like 'pozisyon'. Core business keywords remain in the list.
    assert "sipariş" in po.keywords


def test_po_entity_has_intent_rules() -> None:
    registry = _load_registry()
    po = registry.get_entity("PO_PURCHASING")
    assert po is not None
    assert len(po.intent_rules) >= 4


def test_po_entity_has_intent_defaults() -> None:
    registry = _load_registry()
    po = registry.get_entity("PO_PURCHASING")
    assert po is not None
    assert "po_line_quantity" in po.intent_defaults
    assert "po_pending_delivery" in po.intent_defaults
    assert "po_distribution_amount" in po.intent_defaults
    assert "po_item_line_count" in po.intent_defaults


def test_intent_join_paths_populated() -> None:
    registry = _load_registry()
    assert len(registry.intent_join_paths) >= 1


def test_registry_reads_policy_rules_and_column_aliases() -> None:
    registry = _load_registry()
    assert "password" in registry.policy_rules.sensitive_intent_patterns
    assert registry.column_aliases.global_aliases.get("email") == "EMAIL"
    scoped = registry.column_aliases.table_scoped.get("XXBT_PDKS_PER_DETAILS_V", {})
    assert scoped.get("giris_tarihi") == "ISE_GIRIS_TARIHI"


def test_registry_exposes_lookup_projection() -> None:
    registry = _load_registry()
    values = {lookup.raw_value for lookup in registry.get_lookups_for_column("AUTHORIZATION_STATUS", table_name="PO_HEADERS_ALL")}
    assert {"APPROVED", "IN PROCESS", "INCOMPLETE", "PRE-APPROVED", "REJECTED"} <= values


def test_registry_backward_compat_defaults_when_new_fields_missing() -> None:
    legacy_payload = {
        "version": "1.0",
        "entities": [],
        "intent_join_paths": {},
    }
    registry = SemanticRegistry.model_validate(legacy_payload)

    assert registry.policy_rules.sensitive_intent_patterns == []
    assert registry.column_aliases.global_aliases == {}
    assert registry.column_aliases.table_scoped == {}


def test_registry_backward_compat_defaults_when_policy_rules_missing() -> None:
    payload = {
        "version": "1.0",
        "entities": [],
        "intent_join_paths": {},
        "column_aliases": {
            "global": {"mail": "EMAIL"},
            "table_scoped": {},
        },
    }
    registry = SemanticRegistry.model_validate(payload)

    assert registry.policy_rules.sensitive_intent_patterns == []
    assert registry.column_aliases.global_aliases.get("mail") == "EMAIL"


def test_registry_backward_compat_defaults_when_column_aliases_missing() -> None:
    payload = {
        "version": "1.0",
        "entities": [],
        "intent_join_paths": {},
        "policy_rules": {
            "sensitive_intent_patterns": ["password"],
        },
    }
    registry = SemanticRegistry.model_validate(payload)

    assert registry.policy_rules.sensitive_intent_patterns == ["password"]
    assert registry.column_aliases.global_aliases == {}
    assert registry.column_aliases.table_scoped == {}


def test_registry_loader_file_missing_returns_safe_defaults_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services import semantic_planning

    semantic_planning._load_registry.cache_clear()
    caplog.set_level("INFO")
    missing = Path("C:/this/path/does/not/exist/semantic_registry.json")
    monkeypatch.setattr(semantic_planning, "_REGISTRY_PATH", missing)

    registry = semantic_planning._load_registry()

    assert isinstance(registry, SemanticRegistry)
    assert registry.policy_rules.sensitive_intent_patterns == []
    assert registry.column_aliases.global_aliases == {}
    assert "Optional semantic registry overlay not found" in caplog.text


def test_registry_loader_invalid_global_alias_type_falls_back_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services import semantic_planning

    bad = tmp_path / "semantic_registry_bad_global.json"
    bad.write_text(
        """
{
  "version": "1.0",
  "entities": [],
  "intent_join_paths": {},
  "column_aliases": {
    "global": ["mail", "EMAIL"],
    "table_scoped": {}
  }
}
""".strip(),
        encoding="utf-8",
    )

    semantic_planning._load_registry.cache_clear()
    monkeypatch.setattr(semantic_planning, "_REGISTRY_PATH", bad)
    registry = semantic_planning._load_registry()

    assert registry.column_aliases.global_aliases == {}
    assert registry.column_aliases.table_scoped == {}
    assert "Semantic registry validation failed" in caplog.text


def test_registry_loader_invalid_table_scoped_alias_type_falls_back_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services import semantic_planning

    bad = tmp_path / "semantic_registry_bad_scoped.json"
    bad.write_text(
        """
{
  "version": "1.0",
  "entities": [],
  "intent_join_paths": {},
  "column_aliases": {
    "global": {},
    "table_scoped": "XXBT_PDKS_PER_DETAILS_V"
  }
}
""".strip(),
        encoding="utf-8",
    )

    semantic_planning._load_registry.cache_clear()
    monkeypatch.setattr(semantic_planning, "_REGISTRY_PATH", bad)
    registry = semantic_planning._load_registry()

    assert registry.column_aliases.global_aliases == {}
    assert registry.column_aliases.table_scoped == {}
    assert "Semantic registry validation failed" in caplog.text


# ---------------------------------------------------------------------------
# _infer_entity_intent
# ---------------------------------------------------------------------------

@pytest.fixture()
def po_entity():
    return _load_registry().get_entity("PO_PURCHASING")


def test_infer_intent_line_quantity(po_entity) -> None:
    intent = _infer_entity_intent("sipariş kalemlerinin miktarını göster", po_entity)
    assert intent == "po_line_quantity"


def test_infer_intent_pending_delivery(po_entity) -> None:
    intent = _infer_entity_intent("teslim bekleyen siparişler", po_entity)
    assert intent == "po_pending_delivery"


def test_infer_intent_distribution_amount(po_entity) -> None:
    intent = _infer_entity_intent("dağıtım tutarları nedir", po_entity)
    assert intent == "po_distribution_amount"


def test_infer_intent_item_line_count(po_entity) -> None:
    intent = _infer_entity_intent("ürün bazlı kaç satır var", po_entity)
    assert intent == "po_item_line_count"


def test_infer_intent_default_fallback(po_entity) -> None:
    intent = _infer_entity_intent("satınalma hakkında bilgi ver", po_entity)
    assert intent == po_entity.default_intent


# ---------------------------------------------------------------------------
# _apply_intent_defaults
# ---------------------------------------------------------------------------

def test_apply_defaults_line_quantity(po_entity) -> None:
    updates: dict = {}
    _apply_intent_defaults(po_entity, "po_line_quantity", updates)
    assert updates["needs_clarification"] is False
    assert "line_num" in updates["group_by"]
    aggs = updates["aggregations"]
    assert len(aggs) >= 1
    assert aggs[0].function == AggregateFn.SUM


def test_apply_defaults_pending_delivery(po_entity) -> None:
    updates: dict = {}
    _apply_intent_defaults(po_entity, "po_pending_delivery", updates)
    assert updates["needs_clarification"] is False
    filters = updates["filters"]
    assert len(filters) == 1
    assert filters[0].column == "quantity_received"
    assert filters[0].op == FilterOp.LT


def test_apply_defaults_distribution_amount(po_entity) -> None:
    updates: dict = {}
    _apply_intent_defaults(po_entity, "po_distribution_amount", updates)
    assert "code_combination_id" in updates["group_by"]
    assert len(updates["aggregations"]) >= 2
    assert len(updates["computed_measures"]) >= 1
    assert updates["computed_measures"][0].name == "total_amount"


def test_apply_defaults_unknown_intent_is_noop(po_entity) -> None:
    updates: dict = {"existing_key": "value"}
    _apply_intent_defaults(po_entity, "po_nonexistent", updates)
    assert updates == {"existing_key": "value"}


# ---------------------------------------------------------------------------
# registry= injection in apply_semantic_normalization
# ---------------------------------------------------------------------------

def test_registry_injection_kwarg() -> None:
    """Passing a custom registry= should be used instead of the cached default."""
    real_registry = _load_registry()
    plan = _blank_po_plan()
    # Use the real registry via the kwarg — should produce an identical result to
    # calling without registry= (which also uses the real registry).
    result_injected = apply_semantic_normalization(
        plan, "sipariş kalemlerinin miktarını göster", object(), registry=real_registry
    )
    result_default = apply_semantic_normalization(
        plan, "sipariş kalemlerinin miktarını göster", object()
    )
    assert result_injected.semantic_intent == result_default.semantic_intent
    assert result_injected.root_entity == result_default.root_entity


def test_registry_none_uses_default() -> None:
    plan = _blank_po_plan()
    result = apply_semantic_normalization(plan, "teslim bekleyen siparişler", object(), registry=None)
    assert result.semantic_intent == "po_pending_delivery"


def test_empty_registry_returns_plan_unchanged() -> None:
    plan = _blank_po_plan()
    empty_registry = SemanticRegistry()
    result = apply_semantic_normalization(plan, "herhangi bir sorgu", object(), registry=empty_registry)
    assert result is plan
