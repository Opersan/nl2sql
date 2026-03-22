"""Semantic foundation package.

Provides typed models, JSONL loader, and runtime registry for the
6-layer semantic data model: glossary, entities, relationships,
metrics, lookups, and flexfields.

Typical usage::

    from app.semantic.registry import get_registry

    registry = get_registry()
    entries = registry.resolve_term("calisan")
    entity  = registry.get_entity("HR_EMPLOYEES")
"""
