"""Grounding Config Provider — Sprint 1 Grounding Hardening.

Single-source-of-truth accessor for filter column resolution configuration.

Loads dimension-to-column mappings, keyword/phrase sets, confusable column
groups, and the non-dimension column list from ``data/config/filter_grounding.json``.

Additionally overlays ``SemanticEntity.likely_identifiers`` from the
semantic foundation so that entity-specific identifier columns (e.g.
PERSON_ID, SICIL_NO, FULL_NAME) are automatically treated as non-remappable
even if they are not listed in the JSON config.

Design invariants
-----------------
- This provider is purely configuration-reading; it does NOT call the LLM,
  the DB, or any async service.
- If the config file is missing or malformed, the provider degrades safely
  to an empty config (no-op behaviour in the resolution stage).
- All strings stored internally are already diacritic-stripped + casefold so
  that the service can perform a plain substring comparison without repeating
  normalisation.
- All column names stored internally are UPPER-CASED for O(1) membership tests.

Usage
-----
    from app.services.grounding_config_provider import GroundingConfigProvider

    provider = GroundingConfigProvider()          # default path
    provider = GroundingConfigProvider(config_path=Path("tests/fixtures/filter_grounding.json"))

    dims_by_priority = provider.get_dimension_priority_order()
    cfg = provider.get_dimension_config("department")
    if cfg:
        print(cfg.preferred_column)     # "BIRIM_ADI"
        print(cfg.confusable_columns)   # frozenset(...)

    provider.is_non_dimension_column("BORDROLU")   # True
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.semantic.registry import get_registry
from app.utils.turkish import normalize_for_matching

logger = get_logger(__name__)

_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "config" / "filter_grounding.json"
)

# ---------------------------------------------------------------------------
# Data-classes (public API)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionGrounding:
    """Resolved, normalised config for a single business dimension."""

    name: str
    """Internal dimension identifier, e.g. ``"department"``."""

    preferred_column: str
    """Canonical DB column for this dimension — UPPER-CASED."""

    priority: int
    """Lower value = higher priority when two dimensions conflict."""

    keywords: frozenset[str]
    """Normalised single-token root keywords (diacritic-stripped, casefold)."""

    phrases: tuple[str, ...]
    """Multi-word normalised phrases for substring detection (sorted longest-first)."""

    confusable_columns: frozenset[str]
    """Set of UPPER-CASED column names that are remapping candidates for this dimension."""


@dataclass
class GroundingConfig:
    """Full resolved grounding config."""

    dimensions: list[DimensionGrounding] = field(default_factory=list)
    """Dimensions sorted by priority (ascending = most specific first)."""

    non_dimension_columns: frozenset[str] = field(default_factory=frozenset)
    """UPPER-CASED column names that must never be remapped."""

    loaded_ok: bool = False
    """True when the config was loaded without errors."""

    config_path: str = ""
    """Path from which the config was loaded (for diagnostics)."""


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class GroundingConfigProvider:
    """Loads and exposes the filter grounding configuration.

    Singleton-friendly: construct once, share across requests.

    Parameters
    ----------
    config_path:
        Path to ``filter_grounding.json``.  Defaults to
        ``data/config/filter_grounding.json`` relative to the project root.
    extra_non_dimension_columns:
        Additional column names to add to the non-dimension set (e.g. injected
        from ``SemanticEntity.likely_identifiers``).  Normalised to UPPER-CASE.
    """

    def __init__(
        self,
        config_path: Path | None = None,
        extra_non_dimension_columns: frozenset[str] | None = None,
    ) -> None:
        self._config_path = config_path or _DEFAULT_CONFIG_PATH
        self._extra = extra_non_dimension_columns or frozenset()
        self._config = self._load()

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def loaded_ok(self) -> bool:
        """True when the config file was loaded without fatal errors."""
        return self._config.loaded_ok

    def get_dimension_priority_order(self) -> list[DimensionGrounding]:
        """Return all dimensions sorted by priority (most specific first)."""
        return list(self._config.dimensions)

    def get_dimension_config(self, dimension_name: str) -> DimensionGrounding | None:
        """Return the DimensionGrounding for *dimension_name*, or None."""
        for dim in self._config.dimensions:
            if dim.name == dimension_name:
                return dim
        return None

    def get_dimension_names_by_priority(self) -> list[str]:
        """Return dimension names in priority order (most specific first)."""
        return [d.name for d in self._config.dimensions]

    def is_non_dimension_column(self, column_upper: str) -> bool:
        """Return True if *column_upper* should never be remapped."""
        return column_upper in self._config.non_dimension_columns

    def get_non_dimension_columns(self) -> frozenset[str]:
        """Return the full set of non-dimension column names (UPPER-CASED)."""
        return self._config.non_dimension_columns

    # ------------------------------------------------------------------
    # Internal loading
    # ------------------------------------------------------------------

    def _load(self) -> GroundingConfig:
        """Load config from JSON.  Returns empty config on any error."""
        try:
            raw = self._config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning(
                "[grounding-config] Config file not found: %s — filter column resolution will no-op.",
                self._config_path,
            )
            return GroundingConfig(config_path=str(self._config_path))
        except OSError as exc:
            logger.warning(
                "[grounding-config] Cannot read config file %s: %s — filter column resolution will no-op.",
                self._config_path,
                exc,
            )
            return GroundingConfig(config_path=str(self._config_path))

        try:
            data: dict = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "[grounding-config] Invalid JSON in %s: %s — filter column resolution will no-op.",
                self._config_path,
                exc,
            )
            return GroundingConfig(config_path=str(self._config_path))

        return self._parse(data)

    def _parse(self, data: dict) -> GroundingConfig:
        """Parse the validated JSON dict into a GroundingConfig."""

        raw_dims: dict[str, dict] = data.get("dimensions", {})
        raw_ndcols: list[str] = data.get("non_dimension_columns", [])

        if not raw_dims:
            logger.warning("[grounding-config] No dimensions found in config — filter resolution will no-op.")

        # Build DimensionGrounding instances
        dims: list[DimensionGrounding] = []
        for dim_name, dim_cfg in raw_dims.items():
            try:
                preferred = str(dim_cfg.get("preferred_column", "")).upper()
                priority = int(dim_cfg.get("priority", 99))
                keywords = frozenset(normalize_for_matching(k) for k in dim_cfg.get("keywords", []) if k)
                raw_phrases = [normalize_for_matching(p) for p in dim_cfg.get("phrases", []) if p]
                # Longest phrases first for better specificity
                phrases = tuple(sorted(raw_phrases, key=len, reverse=True))
                confusable = frozenset(c.upper() for c in dim_cfg.get("confusable_columns", []) if c)

                if not preferred:
                    logger.warning("[grounding-config] Dimension %r missing preferred_column — skipped.", dim_name)
                    continue

                dims.append(
                    DimensionGrounding(
                        name=dim_name,
                        preferred_column=preferred,
                        priority=priority,
                        keywords=keywords,
                        phrases=phrases,
                        confusable_columns=confusable,
                    )
                )
            except (TypeError, ValueError) as exc:
                logger.warning("[grounding-config] Error parsing dimension %r: %s — skipped.", dim_name, exc)

        # Sort by priority
        dims.sort(key=lambda d: d.priority)

        # Build non-dimension column set.
        # Order of authority:
        # 1. Shared semantic metadata projection (existing source-of-truth)
        # 2. Centralized config fallback for columns not yet projected
        # 3. Explicit caller-provided extras (tests / adapters)
        semantic_ndcols = self._load_semantic_non_dimension_columns()
        config_ndcols = frozenset(c.upper() for c in raw_ndcols if c)
        non_dim = semantic_ndcols | config_ndcols | self._extra

        config = GroundingConfig(
            dimensions=dims,
            non_dimension_columns=non_dim,
            loaded_ok=True,
            config_path=str(self._config_path),
        )

        logger.debug(
            "[grounding-config] Loaded %d dimension(s), %d non-dimension column(s) from %s.",
            len(dims),
            len(non_dim),
            self._config_path,
        )
        return config

    def _load_semantic_non_dimension_columns(self) -> frozenset[str]:
        """Load non-remappable columns from the shared semantic metadata.

        Current projection sources:
        - ``SemanticEntity.likely_identifiers``
        - ``SemanticEntity.status_filter_column``
        - ``SemanticEntity.time_column``

        This keeps identifier/status/time semantics anchored in the existing
        semantic layer instead of redefining them inside the filter resolution
        service.  Missing semantic data degrades safely to an empty set.
        """
        try:
            registry = get_registry()
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning(
                "[grounding-config] Semantic registry unavailable: %s — using config-only non-dimension columns.",
                exc,
            )
            return frozenset()

        columns: set[str] = set()
        for entity in registry.get_all_entities():
            columns.update(col.upper() for col in entity.likely_identifiers if col)
            if entity.status_filter_column:
                columns.add(entity.status_filter_column.upper())
            if entity.time_column:
                columns.add(entity.time_column.upper())

        return frozenset(columns)
