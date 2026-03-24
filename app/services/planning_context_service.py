"""Planning context assembly stage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.catalog_models import CatalogSnapshot
from app.providers.llm.base import LLMProvider
from app.services.catalog_service import CatalogService
from app.services.planning_models import PlanningContext, RequestContext, RetrievedContext, RetrievalDiagnostics

if TYPE_CHECKING:
    from app.services.query_understanding import QueryUnderstanding

logger = get_logger(__name__)


class _PruneResult(BaseModel):
    pruned_schema: dict[str, list[str]] = {}


class PlanningContextAssemblyService:
    """Collect schema context and prune columns before prompt assembly."""

    def __init__(
        self,
        catalog: CatalogService,
        llm: LLMProvider,
    ) -> None:
        self._catalog = catalog
        self._llm = llm

    async def assemble(
        self,
        request_context: RequestContext,
        query_understanding: "QueryUnderstanding",
    ) -> PlanningContext:
        full_snapshot = await self._catalog.get_snapshot()
        retrieved_snapshot = await self._catalog.get_relevant_context(
            request_context.user_message,
            query_understanding=query_understanding,
        )
        schema_diag = dict(self._catalog.last_retrieval_diagnostics or {})
        pruned_columns = await self.prune_columns(request_context.user_message, retrieved_snapshot)
        pruned_columns = self.harden_pruned_columns(
            pruned_columns,
            retrieved_snapshot,
            query_understanding,
        )
        pruned_columns = self.apply_focus_pruning(
            pruned_columns,
            retrieved_snapshot,
            query_understanding,
            root_table_name=str(schema_diag.get("root_table_name") or "") or None,
        )
        return PlanningContext(
            request=request_context,
            query_understanding=query_understanding,
            retrieved_context=RetrievedContext(
                full_snapshot=full_snapshot,
                retrieved_snapshot=retrieved_snapshot,
                pruned_columns=pruned_columns,
                retrieval_diagnostics=RetrievalDiagnostics(
                    schema_table_count=len(retrieved_snapshot.tables),
                    relationship_count=len(retrieved_snapshot.relationships),
                    dominant_domain_match=(
                        bool(schema_diag["dominant_domain_match"])
                        if schema_diag.get("dominant_domain_match") is not None
                        else None
                    ),
                    root_table_name=str(schema_diag.get("root_table_name") or "") or None,
                    root_table_confidence=str(schema_diag.get("root_table_confidence") or "") or None,
                    noisy_context_count=int(schema_diag.get("noisy_context_count", 0) or 0),
                    dropped_candidates=[str(item) for item in schema_diag.get("dropped_candidates", [])],
                    kept_candidates_reason={
                        str(key): str(value)
                        for key, value in dict(schema_diag.get("kept_candidates_reason", {})).items()
                    },
                ),
            ),
        )

    async def prune_columns(
        self,
        user_message: str,
        context: CatalogSnapshot,
    ) -> dict[str, list[str]]:
        if not settings.enable_column_prune:
            return {}

        total_cols = sum(len(table.columns) for table in context.tables)
        if len(context.tables) <= 1 and total_cols <= 15:
            return {}

        schema_lines = [
            f"{table.name}: " + ", ".join(column.name for column in table.columns)
            for table in context.tables
        ]
        schema_text = "\n".join(schema_lines)

        prune_prompt = (
            "Aşağıdaki sorguyu cevaplamak için gereken minimum kolon setini belirle.\n"
            f"Soru: {user_message}\n\n"
            "Tablolar ve mevcut kolonları:\n"
            f"{schema_text}\n\n"
            "SADECE aşağıdaki JSON formatında yanıt ver (başka hiçbir şey yazma):\n"
            '{"pruned_schema": {"TABLE_NAME": ["col1", "col2"]}}\n\n'
            "Kurallar:\n"
            "1. PK ve FK kolonlarını her zaman dahil et.\n"
            "2. Soruyla ilgisiz kolonları çıkar.\n"
            "3. Sadece verilen tablo adlarını kullan.\n"
        )

        try:
            result = await self._llm.generate_structured(prune_prompt, _PruneResult)
            logger.debug("[column-prune] pruned %d tables", len(result.pruned_schema))
            return result.pruned_schema
        except Exception:
            logger.debug("[column-prune] pruning call failed — using all columns (fail-open)")
            return {}

    @staticmethod
    def harden_pruned_columns(
        pruned: dict[str, list[str]],
        context: CatalogSnapshot,
        query_understanding: "QueryUnderstanding",
    ) -> dict[str, list[str]]:
        if not pruned:
            return pruned

        query_filter_dims: set[str] = set()
        for extracted_filter in query_understanding.extracted_filters:
            dimension = extracted_filter.get("dimension", "")
            if dimension:
                query_filter_dims.add(dimension.upper())

        hardened = dict(pruned)
        for table in context.tables:
            table_name = table.name
            if table_name not in hardened:
                continue

            existing = {column.upper() for column in hardened[table_name]}
            must_add: list[str] = []

            for primary_key in table.primary_key:
                if primary_key.upper() not in existing:
                    must_add.append(primary_key)

            for foreign_key in table.foreign_keys:
                if foreign_key.column.upper() not in existing:
                    must_add.append(foreign_key.column)

            for column in table.columns:
                column_upper = column.name.upper()
                if column_upper in query_filter_dims and column_upper not in existing:
                    must_add.append(column.name)
                for alias in column.aliases:
                    if alias.upper() in query_filter_dims and column_upper not in existing:
                        must_add.append(column.name)
                        break

            if must_add:
                hardened[table_name] = hardened[table_name] + must_add
                logger.debug(
                    "[column-prune-harden] Re-added %d column(s) to %s: %s",
                    len(must_add),
                    table_name,
                    must_add,
                )

        return hardened

    @staticmethod
    def apply_focus_pruning(
        pruned: dict[str, list[str]],
        context: CatalogSnapshot,
        query_understanding: "QueryUnderstanding",
        *,
        root_table_name: str | None = None,
    ) -> dict[str, list[str]]:
        """Apply deterministic secondary-table pruning when LLM pruning is sparse.

        Root table detail stays wide; non-root tables are reduced to PK/FK plus
        query-matching columns so cross-domain noise does not bloat the prompt.
        """
        if not context.tables:
            return pruned

        root_name = root_table_name or context.tables[0].name
        query_terms = {
            token.upper()
            for token in (query_understanding.normalized_question or "").replace("'", " ").split()
            if len(token) >= 3
        }
        filter_dims = {
            str(item.get("dimension", "")).upper()
            for item in query_understanding.extracted_filters
            if item.get("dimension")
        }

        focused = {table_name: list(columns) for table_name, columns in pruned.items()}
        for table in context.tables:
            if table.name == root_name:
                continue

            pk_cols = list(table.primary_key)
            fk_cols = [fk.column for fk in table.foreign_keys]

            matched: list[str] = []
            explicit_restricted: set[str] = set()
            for column in table.columns:
                aliases = {alias.upper() for alias in column.aliases}
                signals = {column.name.upper(), *aliases}
                if signals & filter_dims or signals & query_terms:
                    matched.append(column.name)
                    if column.restricted:
                        explicit_restricted.add(column.name.upper())

            ordered: list[str] = []
            for candidate in [*pk_cols, *fk_cols, *focused.get(table.name, []), *matched]:
                normalized = candidate.upper()
                if any(item.upper() == normalized for item in ordered):
                    continue
                column_meta = table.get_column(candidate)
                if column_meta is not None and column_meta.restricted and normalized not in explicit_restricted:
                    continue
                ordered.append(candidate)

            if not ordered:
                ordered = [*pk_cols, *fk_cols]

            focused[table.name] = ordered[:8]

        return focused