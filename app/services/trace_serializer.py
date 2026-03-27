"""Safe payload serializers for Pipeline Live View (opt-in trace layer).

All functions here are ADDITIVE and purely read-only.  They do NOT modify
any pipeline objects or affect business logic.

Key safety principles:
- Never expose raw embedding vectors (only shape/dim summary)
- Truncate long strings to a safe preview length
- Redact internal Python objects that can't be safely serialized to JSON
- Limit nested depth to avoid circular references / huge payloads
"""

from __future__ import annotations

from typing import Any

# Maximum characters for a text preview in the UI
_MAX_TEXT_PREVIEW = 800
_MAX_TEXT_FULL = 4000
_MAX_LIST_ITEMS = 30
_MAX_DICT_KEYS = 40
_MAX_NESTED_DEPTH = 4


# ---------------------------------------------------------------------------
# Core safe-serialize helpers
# ---------------------------------------------------------------------------


def safe_text(s: Any, max_len: int = _MAX_TEXT_PREVIEW) -> str:
    """Return a safe, truncated string representation of ``s``."""
    if s is None:
        return ""
    text = str(s)
    if len(text) > max_len:
        return text[:max_len] + f"… [{len(text) - max_len} chars truncated]"
    return text


def safe_payload(
    obj: Any,
    *,
    max_depth: int = _MAX_NESTED_DEPTH,
    max_items: int = _MAX_LIST_ITEMS,
    max_str: int = _MAX_TEXT_PREVIEW,
) -> Any:
    """Recursively convert ``obj`` into a safe JSON-serializable structure.

    - Strings are truncated to ``max_str`` chars
    - Lists are capped at ``max_items`` entries
    - Dicts are capped at ``max_dict_keys`` entries and depth-limited
    - Floats, ints, bools, None pass through unchanged
    - Unknown objects are replaced with a type annotation string
    """
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, str):
        return safe_text(obj, max_len=max_str)
    if isinstance(obj, bytes):
        return f"<bytes: {len(obj)} bytes>"

    # Embedding vectors: never expose raw floats, only shape
    if isinstance(obj, list) and obj and isinstance(obj[0], float) and len(obj) > 8:
        return f"<embedding vector: dim={len(obj)}>"

    if isinstance(obj, list):
        if max_depth <= 0:
            return f"<list: {len(obj)} items>"
        truncated = obj[:max_items]
        result = [safe_payload(item, max_depth=max_depth - 1, max_items=max_items, max_str=max_str) for item in truncated]
        if len(obj) > max_items:
            result.append(f"… {len(obj) - max_items} more items")
        return result

    if isinstance(obj, dict):
        if max_depth <= 0:
            return f"<dict: {len(obj)} keys>"
        keys = list(obj.keys())[:_MAX_DICT_KEYS]
        result = {}
        for k in keys:
            result[str(k)] = safe_payload(obj[k], max_depth=max_depth - 1, max_items=max_items, max_str=max_str)
        if len(obj) > _MAX_DICT_KEYS:
            result["__truncated__"] = f"{len(obj) - _MAX_DICT_KEYS} more keys"
        return result

    # Pydantic models
    if hasattr(obj, "model_dump"):
        try:
            return safe_payload(obj.model_dump(mode="json"), max_depth=max_depth, max_items=max_items, max_str=max_str)
        except Exception:
            pass

    # Dataclasses / namedtuples
    if hasattr(obj, "__dict__"):
        try:
            return safe_payload(obj.__dict__, max_depth=max_depth, max_items=max_items, max_str=max_str)
        except Exception:
            pass

    return f"<{type(obj).__name__}>"


# ---------------------------------------------------------------------------
# Stage-specific payload builders
# ---------------------------------------------------------------------------


def build_question_payload(message: str, session_id: str) -> dict[str, Any]:
    return {
        "question": safe_text(message, max_len=500),
        "session_id": session_id,
    }


def build_runtime_context_payload(
    *,
    session_id: str,
    trace_id: str,
    settings_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "trace_id": trace_id,
        "session_id": session_id,
    }
    if settings_snapshot:
        ctx["settings"] = settings_snapshot
    return ctx


def build_settings_snapshot() -> dict[str, Any]:
    """Build a safe, non-secret snapshot of application settings."""
    try:
        from app.core.config import settings

        return {
            "llm_provider": getattr(settings, "llm_provider", "unknown"),
            "enable_oracle_executor": getattr(settings, "enable_oracle_executor", False),
            "retrieval_strategy": getattr(settings, "retrieval_strategy", "unknown"),
            "max_row_limit": getattr(settings, "max_row_limit", None),
            "max_rows_preview": getattr(settings, "max_rows_preview", None),
        }
    except Exception:
        return {}


def build_catalog_readiness_payload() -> dict[str, Any]:
    """Snapshot catalog + semantic registry readiness at request time."""
    payload: dict[str, Any] = {}
    try:
        from app.core.config import settings
        from app.core.data_paths import resolve_catalog_source_path

        src_type = getattr(settings, "metadata_source_type", "none")
        payload["catalog_source_type"] = src_type

        if src_type == "json" and getattr(settings, "metadata_source_path", None):
            try:
                path, used_legacy = resolve_catalog_source_path(settings.metadata_source_path)
                payload["catalog_source_path"] = str(path)
                payload["catalog_file_exists"] = path.exists()
                payload["catalog_used_legacy_path"] = used_legacy
            except Exception as exc:
                payload["catalog_path_error"] = str(exc)
        else:
            payload["catalog_source_path"] = None
            payload["catalog_file_exists"] = None
    except Exception as exc:
        payload["catalog_check_error"] = str(exc)

    try:
        from app.providers.catalog.in_memory import catalog_fingerprint
        fp = catalog_fingerprint()
        payload["catalog_fingerprint"] = fp
    except Exception:
        payload["catalog_fingerprint"] = None

    return payload


def build_semantic_registry_payload() -> dict[str, Any]:
    """Snapshot semantic registry entity/glossary counts."""
    payload: dict[str, Any] = {}
    try:
        from app.services.semantic_planning import _load_registry
        registry = _load_registry()
        payload["loaded"] = True
        payload["entity_count"] = len(getattr(registry, "entities", {}))
        payload["entities"] = list(getattr(registry, "entities", {}).keys())[:20]

        glossary = getattr(registry, "glossary", None) or {}
        payload["glossary_term_count"] = len(glossary)

        metrics = getattr(registry, "metrics", None) or {}
        payload["metric_count"] = len(metrics)

        lookups = getattr(registry, "lookups", None) or {}
        payload["lookup_count"] = len(lookups)

    except Exception as exc:
        payload["loaded"] = False
        payload["error"] = str(exc)[:200]

    return payload


def build_query_understanding_payload(qu_trace: dict[str, Any] | None) -> dict[str, Any]:
    """Safe serializer for query understanding results."""
    if not qu_trace:
        return {}
    return safe_payload(qu_trace, max_str=300)


def build_retrieval_payload(retrieval_trace: dict[str, Any] | None) -> dict[str, Any]:
    """Safe serializer for schema/retrieval stage."""
    if not retrieval_trace:
        return {}
    return safe_payload(retrieval_trace, max_str=400)


def build_prompt_payload(prompt_trace: dict[str, Any] | None) -> dict[str, Any]:
    """Safe serializer for prompt assembly stage — returns full prompt text."""
    if not prompt_trace:
        return {}
    result = {}
    for k, v in prompt_trace.items():
        if k == "full_prompt_text":
            result["full_prompt_preview"] = v if isinstance(v, str) else safe_text(v)
            result["full_prompt_char_count"] = len(v) if isinstance(v, str) else 0
        else:
            result[k] = safe_payload(v, max_str=300)
    return result


def build_llm_request_payload(prompt_trace: dict[str, Any] | None) -> dict[str, Any]:
    """Payload for the planner LLM request event (before response arrives)."""
    if not prompt_trace:
        return {}
    full = prompt_trace.get("full_prompt_text", "")
    return {
        "prompt_char_count": len(full) if isinstance(full, str) else 0,
        "prompt_preview": full if isinstance(full, str) else safe_text(full),
        "prompt_budget_used": safe_payload(prompt_trace.get("budget"), max_str=200),
    }


def build_llm_response_payload(llm_trace: dict[str, Any] | None) -> dict[str, Any]:
    """Safe payload for the planner LLM raw response."""
    if not llm_trace:
        return {}
    raw = llm_trace.get("raw_response_text", "")
    return {
        "raw_response_preview": raw if isinstance(raw, str) else safe_text(raw),
        "raw_response_char_count": len(raw) if isinstance(raw, str) else 0,
        "parse_error": llm_trace.get("parse_error"),
        "parse_error_taxonomy": llm_trace.get("parse_error_taxonomy"),
        "salvage_applied": llm_trace.get("salvage_applied", False),
    }


def build_plan_payload(plan_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Safe payload for a parsed/transformed QueryPlan snapshot."""
    if not plan_snapshot:
        return {}
    return safe_payload(plan_snapshot, max_str=200)


def build_diff_payload(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Safe payload showing before/after of a plan transformation stage."""
    result: dict[str, Any] = {
        "before": safe_payload(before, max_str=200) if before else None,
        "after": safe_payload(after, max_str=200) if after else None,
    }
    if extra:
        result.update(safe_payload(extra, max_str=200))
    return result


def build_validation_payload(validation_trace: dict[str, Any] | None) -> dict[str, Any]:
    if not validation_trace:
        return {}
    return safe_payload(validation_trace, max_str=400)


def build_compile_payload(compile_trace: dict[str, Any] | None) -> dict[str, Any]:
    """Safe payload for SQL compilation — includes SQL but no bind params."""
    if not compile_trace:
        return {}
    result = {}
    for k, v in compile_trace.items():
        if k == "params":
            # Summarize bind params: count and key names, not values
            if isinstance(v, dict):
                result["bind_param_count"] = len(v)
                result["bind_param_keys"] = list(v.keys())[:20]
            elif isinstance(v, list):
                result["bind_param_count"] = len(v)
        else:
            result[k] = safe_payload(v, max_str=500)
    return result


def build_execute_payload(execute_trace: dict[str, Any] | None) -> dict[str, Any]:
    if not execute_trace:
        return {}
    result = {}
    for k, v in execute_trace.items():
        # Never expose raw row data
        if k == "rows":
            result["rows_count"] = len(v) if isinstance(v, list) else 0
        else:
            result[k] = safe_payload(v, max_str=400)
    return result


def build_narrator_prompt_payload(narrator_trace: dict[str, Any] | None) -> dict[str, Any]:
    """Safe payload for narrator prompt — full text."""
    if not narrator_trace:
        return {}
    full = narrator_trace.get("full_prompt_text", "")
    return {
        "prompt_preview": full if isinstance(full, str) else safe_text(full),
        "prompt_char_count": len(full) if isinstance(full, str) else 0,
        "summary_preview": safe_text(narrator_trace.get("summary", ""), max_len=600),
        "narration_shape": narrator_trace.get("narration_shape"),
    }


def build_narrator_llm_response_payload(narrator_trace: dict[str, Any] | None) -> dict[str, Any]:
    if not narrator_trace:
        return {}
    raw = narrator_trace.get("raw_response", "")
    return {
        "raw_response_preview": raw if isinstance(raw, str) else safe_text(raw),
        "raw_response_char_count": len(raw) if isinstance(raw, str) else 0,
        "raw_response_empty": narrator_trace.get("raw_response_empty", False),
        "raw_leak_reason_codes": narrator_trace.get("raw_leak_reason_codes", []),
    }


def build_narrator_sanitize_payload(narrator_trace: dict[str, Any] | None) -> dict[str, Any]:
    if not narrator_trace:
        return {}
    return {
        "sanitizer_reason_code": narrator_trace.get("sanitizer_reason_code"),
        "prompt_contract_violated": narrator_trace.get("prompt_contract_violated", False),
        "narrator_used_fallback_template": narrator_trace.get("narrator_used_fallback_template", False),
        "narration_genericness_flag": narrator_trace.get("narration_genericness_flag", False),
        "sanitized_response_preview": safe_text(narrator_trace.get("sanitized_response"), max_len=600),
    }


def build_narrator_final_payload(narrator_trace: dict[str, Any] | None) -> dict[str, Any]:
    if not narrator_trace:
        return {}
    return {
        "final_response_source": narrator_trace.get("final_response_source"),
        "final_response_preview": safe_text(narrator_trace.get("final_response"), max_len=_MAX_TEXT_FULL),
        "narration_business_value_score": narrator_trace.get("narration_business_value_score"),
        "raw_narration_quality": narrator_trace.get("raw_narration_quality"),
        "final_narration_quality": narrator_trace.get("final_narration_quality"),
        "user_visible_quality": narrator_trace.get("user_visible_quality"),
        "model_behavior_quality": narrator_trace.get("model_behavior_quality"),
    }


def build_filter_column_resolution_payload(fcr_trace: dict[str, Any]) -> dict[str, Any]:
    """Build a safe Pipeline Live View payload for the filter_column_resolution stage.

    Shows original vs resolved columns, change flag, dimension, and reason code
    for every filter processed — so engineers can inspect column corrections in
    the live view without hunting through raw trace JSON.
    """
    changed_count = int(fcr_trace.get("changed_count", 0))
    total = int(fcr_trace.get("total_filters", 0))
    actions_raw = fcr_trace.get("actions") or []

    # Build a compact per-action summary for the UI
    action_summaries = []
    for a in actions_raw[:_MAX_LIST_ITEMS]:
        if not isinstance(a, dict):
            continue
        action_summaries.append({
            "filter_index": a.get("filter_index"),
            "original_column": safe_text(str(a.get("original_column", "")), max_len=80),
            "resolved_column": safe_text(str(a.get("resolved_column", "")), max_len=80),
            "changed": bool(a.get("changed", False)),
            "dimension": a.get("dimension"),
            "confidence": a.get("confidence"),
            "reason": safe_text(str(a.get("reason", "")), max_len=120),
        })

    return {
        "any_changed": bool(fcr_trace.get("any_changed", False)),
        "total_filters": total,
        "changed_count": changed_count,
        "actions": action_summaries,
    }


def build_filter_value_resolution_payload(fvr_trace: dict[str, Any]) -> dict[str, Any]:
    """Build a safe Pipeline Live View payload for filter_value_resolution."""
    actions_raw = fvr_trace.get("actions") or []
    action_summaries = []
    for action in actions_raw[:_MAX_LIST_ITEMS]:
        if not isinstance(action, dict):
            continue
        action_summaries.append({
            "column": safe_text(str(action.get("column", "")), max_len=80),
            "operator": safe_text(str(action.get("operator", "")), max_len=20),
            "original_value": safe_text(str(action.get("original_value", "")), max_len=80),
            "resolved_value": safe_text(str(action.get("resolved_value", "")), max_len=80),
            "changed": bool(action.get("changed", False)),
            "clarification_required": bool(action.get("clarification_required", False)),
            "reason": safe_text(str(action.get("reason", "")), max_len=120),
            "confidence": action.get("confidence"),
            "candidate_values": safe_payload(action.get("candidate_values", []), max_str=80),
        })

    return {
        "any_changed": bool(fvr_trace.get("any_changed", False)),
        "clarification_required": bool(fvr_trace.get("clarification_required", False)),
        "total_filters": len(actions_raw),
        "changed_count": sum(1 for action in actions_raw if isinstance(action, dict) and action.get("changed")),
        "actions": action_summaries,
    }


def build_final_verdict_payload(
    *,
    status: str,
    answer: str,
    plan_snapshot: dict[str, Any] | None = None,
    sql: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    total_elapsed_ms: int | None = None,
    planner_trace: dict[str, Any] | None = None,
    orchestrator_trace: dict[str, Any] | None = None,
    narrator_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "answer_preview": safe_text(answer, max_len=800),
        "total_elapsed_ms": total_elapsed_ms,
        "error_code": error_code,
        "error_message": safe_text(error_message, max_len=300) if error_message else None,
        "sql_preview": safe_text(sql, max_len=1000) if sql else None,
    }
    if plan_snapshot:
        result["plan_table"] = plan_snapshot.get("table")
        result["plan_intent"] = plan_snapshot.get("intent")
        result["plan_needs_clarification"] = plan_snapshot.get("needs_clarification", False)

    if orchestrator_trace:
        orch = orchestrator_trace
        result["last_completed_stage"] = orch.get("last_completed_stage")
        result["root_cause_stage"] = orch.get("root_cause_stage")
        if orch.get("execute"):
            result["row_count"] = orch["execute"].get("row_count")
            result["execution_error_subtype"] = orch["execute"].get("execution_error_subtype")

    if narrator_trace:
        result["narrator_quality"] = narrator_trace.get("final_narration_quality")
        result["narrator_shape"] = narrator_trace.get("narration_shape")

    return result
