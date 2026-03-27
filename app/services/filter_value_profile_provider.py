from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.data_paths import REPO_ROOT
from app.utils.turkish import normalize_for_matching


@dataclass(frozen=True)
class ValueMatchingPolicy:
    candidate_preview_limit: int = 5
    min_select_score: float = 0.88
    min_score_gap: float = 0.08
    min_fuzzy_ratio: float = 0.76
    min_auto_resolve_score: float = 0.80
    exact_canonical_score: float = 1.0
    exact_alias_score: float = 0.96
    token_subset_score: float = 0.86
    token_overlap_score: float = 0.8
    fuzzy_score_base: float = 0.7
    fuzzy_score_scale: float = 0.25


@dataclass(frozen=True)
class CanonicalValueEntry:
    value: str
    aliases: tuple[str, ...]
    normalized_value: str
    normalized_aliases: tuple[str, ...]

    @property
    def all_normalized_forms(self) -> tuple[str, ...]:
        return (self.normalized_value, *self.normalized_aliases)


@dataclass(frozen=True)
class FilterValueProfile:
    table: str | None
    column: str
    supported_ops: frozenset[str]
    canonical_values: tuple[CanonicalValueEntry, ...]

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}" if self.table else self.column


class FilterValueProfileProvider:
    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path or REPO_ROOT / "data" / "config" / "filter_value_profiles.json"
        self._loaded = False
        self._profiles_by_key: dict[str, FilterValueProfile] = {}
        self._profiles_by_column: dict[str, FilterValueProfile] = {}
        self._policy = ValueMatchingPolicy()

    @property
    def config_path(self) -> Path:
        return self._config_path

    def policy(self) -> ValueMatchingPolicy:
        self._ensure_loaded()
        return self._policy

    def get_profile(self, table: str | None, column: str | None) -> FilterValueProfile | None:
        self._ensure_loaded()
        if not column:
            return None
        normalized_column = str(column).strip().upper()
        normalized_table = str(table).strip().upper() if table else None
        if normalized_table:
            profile = self._profiles_by_key.get(f"{normalized_table}.{normalized_column}")
            if profile is not None:
                return profile
        return self._profiles_by_column.get(normalized_column)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._config_path.exists():
            return
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._parse(raw)

    def _parse(self, raw: dict[str, Any]) -> None:
        policy = raw.get("matching_policy")
        if isinstance(policy, dict):
            self._policy = ValueMatchingPolicy(
                candidate_preview_limit=int(policy.get("candidate_preview_limit", 5)),
                min_select_score=float(policy.get("min_select_score", 0.88)),
                min_score_gap=float(policy.get("min_score_gap", 0.08)),
                min_fuzzy_ratio=float(policy.get("min_fuzzy_ratio", 0.76)),
                min_auto_resolve_score=float(policy.get("min_auto_resolve_score", 0.80)),
                exact_canonical_score=float(policy.get("exact_canonical_score", 1.0)),
                exact_alias_score=float(policy.get("exact_alias_score", 0.96)),
                token_subset_score=float(policy.get("token_subset_score", 0.86)),
                token_overlap_score=float(policy.get("token_overlap_score", 0.8)),
                fuzzy_score_base=float(policy.get("fuzzy_score_base", 0.7)),
                fuzzy_score_scale=float(policy.get("fuzzy_score_scale", 0.25)),
            )

        profiles = raw.get("profiles")
        if not isinstance(profiles, dict):
            return

        by_key: dict[str, FilterValueProfile] = {}
        by_column: dict[str, FilterValueProfile] = {}
        for _, item in profiles.items():
            if not isinstance(item, dict):
                continue
            profile = self._parse_profile(item)
            if profile is None:
                continue
            by_key[profile.key.upper()] = profile
            by_column[profile.column.upper()] = profile
        self._profiles_by_key = by_key
        self._profiles_by_column = by_column

    def _parse_profile(self, item: dict[str, Any]) -> FilterValueProfile | None:
        column = item.get("column")
        if not isinstance(column, str) or not column.strip():
            return None
        table = item.get("table")
        if table is not None and not isinstance(table, str):
            table = None
        supported_ops_raw = item.get("supported_ops", ["=", "!="])
        supported_ops = frozenset(
            str(op).strip() for op in supported_ops_raw if str(op).strip()
        )
        entries_raw = item.get("canonical_values")
        if not isinstance(entries_raw, list):
            return None

        entries: list[CanonicalValueEntry] = []
        for entry_raw in entries_raw:
            if not isinstance(entry_raw, dict):
                continue
            value = entry_raw.get("value")
            if not isinstance(value, str) or not value.strip():
                continue
            aliases_raw = entry_raw.get("aliases", [])
            aliases = tuple(
                alias.strip()
                for alias in aliases_raw
                if isinstance(alias, str) and alias.strip()
            )
            entries.append(
                CanonicalValueEntry(
                    value=value,
                    aliases=aliases,
                    normalized_value=normalize_for_matching(value),
                    normalized_aliases=tuple(normalize_for_matching(alias) for alias in aliases),
                )
            )
        if not entries:
            return None
        return FilterValueProfile(
            table=str(table).strip().upper() if isinstance(table, str) and table.strip() else None,
            column=column.strip().upper(),
            supported_ops=supported_ops or frozenset({"=", "!="}),
            canonical_values=tuple(entries),
        )
