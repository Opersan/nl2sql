"""Centralized retrieval gating policy.

Single source of truth for domain-aware, QU-driven retrieval filtering.

Design principles
-----------------
* All table→module lookups go through the **semantic registry**
  (``SemanticFoundationRegistry.get_entity_by_table()``).
* No table-name substring heuristics anywhere in this module.
* Gating decisions derive exclusively from ``QueryUnderstanding`` signals.

Gating contract
---------------
Gating is applied only when **all** of the following hold:

1. ``entity_confidence == "high"``
2. exactly one inferred module (``primary_module() != None``)
3. ``multi_entity_flag is False``
4. ``requires_cross_domain_reasoning is False``

When gating is active, a table *passes* the filter only if:

* its registry module == primary_module  (same domain — keep)
* it is **not** registered at all         (unknown domain — fail-open)

When the registry is unavailable (load failure), all lookups return
``None`` (unknown domain), so all tables pass — safe fail-open behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domain.catalog_models import TableMetadata
    from app.services.query_understanding import QueryUnderstanding

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Registry-based table → module lookup
# ---------------------------------------------------------------------------

def table_module_from_registry(table_name: str) -> str | None:
    """Return the module for *table_name* from the semantic registry.

    Delegates to ``SemanticFoundationRegistry.get_entity_by_table()`` — the
    authoritative metadata path.  Returns ``None`` when the table is not
    registered or the registry cannot be loaded (fail-open).

    No substring heuristics are used.
    """
    try:
        from app.semantic.registry import get_registry  # lazy; avoids circular imports
        entity = get_registry().get_entity_by_table(table_name)
        return entity.module if entity is not None else None
    except Exception:  # pragma: no cover — registry load failure treated as unknown
        return None


# ---------------------------------------------------------------------------
# QU-driven gating decision
# ---------------------------------------------------------------------------

def is_high_confidence_single_domain(
    query_understanding: "QueryUnderstanding | None",
) -> tuple[bool, str | None]:
    """Return ``(should_gate, primary_module)`` from query-understanding signals.

    Returns ``(True, module)`` only when **all** conditions are met:

    * ``entity_confidence`` is ``"high"``
    * exactly one inferred module
    * ``multi_entity_flag`` is ``False``
    * ``requires_cross_domain_reasoning`` is ``False``

    Any other state returns ``(False, None)`` and callers must **not** apply
    domain filtering.
    """
    if query_understanding is None:
        return False, None
    primary_module = query_understanding.primary_module()
    if primary_module is None:
        return False, None
    if getattr(query_understanding, "entity_confidence", "low") != "high":
        return False, None
    if getattr(query_understanding, "multi_entity_flag", False):
        return False, None
    if getattr(query_understanding, "requires_cross_domain_reasoning", False):
        return False, None
    return True, primary_module


# ---------------------------------------------------------------------------
# Domain noise computation (for sufficiency assessment)
# ---------------------------------------------------------------------------

def compute_domain_noise(
    tables: "list[TableMetadata]",
    primary_module: str,
) -> tuple[bool | None, int]:
    """Compute ``(dominant_domain_match, cross_domain_count)`` from *tables*.

    Uses the semantic registry for authoritative table→module resolution.

    Returns
    -------
    dominant_domain_match : bool | None
        ``True``  — same-domain tables dominate (or no cross-domain)
        ``False`` — cross-domain tables equal or outnumber same-domain
        ``None``  — no module information available (registry unavailable
                    or all tables unregistered); caller should treat as
                    unknown rather than noisy.
    cross_domain_count : int
        Number of tables whose registry module differs from *primary_module*.
    """
    same = 0
    cross = 0
    for table in tables:
        m = table_module_from_registry(table.name)
        if m == primary_module:
            same += 1
        elif m is not None:
            cross += 1
    if same + cross == 0:
        return None, 0
    dominant = cross == 0 or same > cross
    return dominant, cross
