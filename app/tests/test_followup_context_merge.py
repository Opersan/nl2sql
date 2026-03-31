"""Tests for FollowupContextMergeService (hybrid LLM + heuristic).

Covers:
1. Successful data turn + follow-up filter refinement preserves previous department filter
2. Clarification-resolved canonical value is preserved across follow-up
3. Projection remains stable when only filters change
4. Explicit filter override works (new plan replaces previous filter for same column)
5. Low-confidence follow-up falls back to normal fresh-query behaviour (merge_strategy="none")
6. Helper / title / tag requests do NOT overwrite snapshot (snapshot safety)
7. Trace payload contains all required fields with meaningful values
8. No previous snapshot → no-op result
9. Table change → low confidence (no merge)
10. Union projection: new columns added to previous projection

NOTE: All tests run WITHOUT an LLM (llm=None), so the heuristic fallback path
is exercised.  LLM-based classification is tested separately.
"""

from __future__ import annotations

import pytest

from app.domain.query_plan import FilterOp, FilterSpec, OrderSpec, QueryPlan, SortDirection
from app.services.followup_context_merge import (
    FollowupContextMergeService,
    SuccessfulTurnSnapshot,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def svc() -> FollowupContextMergeService:
    # No LLM → pure heuristic fallback
    return FollowupContextMergeService()


def _department_plan(extra_filters: list[FilterSpec] | None = None) -> QueryPlan:
    """A typical successful DATA plan: Dizayn department employees."""
    filters = [
        FilterSpec(column="BIRIM_ADI", op=FilterOp.EQ, value="ELEKTRİK DİZAYN"),
    ]
    if extra_filters:
        filters.extend(extra_filters)
    return QueryPlan(
        intent="Dizayn departmanındaki çalışanları listele",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["PERSON_ID", "AD", "SOYAD", "SICIL_NO", "BIRIM_ADI", "ORGANIZATION_ADI"],
        filters=filters,
        order_by=[OrderSpec(column="SOYAD", direction=SortDirection.ASC)],
        limit=100,
    )


def _stajyer_filter_plan() -> QueryPlan:
    """New plan produced by planner for 'tamam ama stajyer olanları getirme'."""
    return QueryPlan(
        intent="Stajyerleri dışla",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["PERSON_ID", "STAJYER"],
        filters=[
            FilterSpec(column="STAJYER", op=FilterOp.NEQ, value="Y"),
        ],
        limit=100,
    )


# ── Test 1: Follow-up preserves department filter ─────────────────────────────


class TestFollowupPreservesDepartmentFilter:
    @pytest.mark.asyncio
    async def test_department_filter_carried_forward(self, svc: FollowupContextMergeService) -> None:
        """After a successful department query, follow-up should preserve BIRIM_ADI filter."""
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        new_plan = _stajyer_filter_plan()
        result = await svc.process("s1", "tamam ama stajyer olanları getirme", new_plan)

        assert result.followup_detected is True
        assert result.merge_strategy == "patch"
        assert result.merged_plan is not None

        # BIRIM_ADI filter must be preserved from previous turn
        merged_cols = {f.column.upper() for f in result.merged_plan.filters}
        assert "BIRIM_ADI" in merged_cols, "Previous department filter should be preserved"
        assert "STAJYER" in merged_cols, "New stajyer filter should be present"

    @pytest.mark.asyncio
    async def test_preserved_filter_has_correct_value(self, svc: FollowupContextMergeService) -> None:
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        new_plan = _stajyer_filter_plan()
        result = await svc.process("s1", "tamam ama stajyer olanları getirme", new_plan)

        assert result.merged_plan is not None
        birim_filters = [f for f in result.merged_plan.filters if f.column.upper() == "BIRIM_ADI"]
        assert len(birim_filters) == 1
        assert birim_filters[0].value == "ELEKTRİK DİZAYN"

    @pytest.mark.asyncio
    async def test_stajyer_filter_has_correct_value(self, svc: FollowupContextMergeService) -> None:
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        new_plan = _stajyer_filter_plan()
        result = await svc.process("s1", "tamam ama stajyer olanları getirme", new_plan)

        assert result.merged_plan is not None
        stajyer_filters = [f for f in result.merged_plan.filters if f.column.upper() == "STAJYER"]
        assert len(stajyer_filters) == 1
        assert stajyer_filters[0].value == "Y"
        assert stajyer_filters[0].op == FilterOp.NEQ


# ── Test 2: Clarification-resolved canonical value preserved ──────────────────


class TestClarificationValuePreserved:
    @pytest.mark.asyncio
    async def test_canonical_value_from_clarification_resume(self, svc: FollowupContextMergeService) -> None:
        """Clarification-resolved canonical value (e.g. 'ELEKTRİK DİZAYN') must survive follow-up."""
        clarification_resolved_plan = QueryPlan(
            intent="ELEKTRİK DİZAYN çalışanları",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["PERSON_ID", "AD", "SOYAD", "SICIL_NO", "BIRIM_ADI"],
            filters=[
                FilterSpec(column="BIRIM_ADI", op=FilterOp.EQ, value="ELEKTRİK DİZAYN"),
            ],
            limit=100,
        )
        svc.record_success("sess", clarification_resolved_plan)

        followup_plan = QueryPlan(
            intent="Aktif çalışan",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["PERSON_ID", "AD"],
            filters=[
                FilterSpec(column="CIKIS_TARIHI", op=FilterOp.IS_NULL),
            ],
            limit=100,
        )
        result = await svc.process("sess", "ama sadece aktif olanlar", followup_plan)
        assert result.followup_detected is True
        assert result.merged_plan is not None

        birim_filters = [f for f in result.merged_plan.filters if f.column.upper() == "BIRIM_ADI"]
        assert len(birim_filters) == 1, "Clarification-resolved BIRIM_ADI must be preserved"
        assert birim_filters[0].value == "ELEKTRİK DİZAYN"


# ── Test 3: Projection stability ──────────────────────────────────────────────


class TestProjectionStability:
    @pytest.mark.asyncio
    async def test_previous_columns_preserved_on_filter_only_change(self, svc: FollowupContextMergeService) -> None:
        """When only a filter changes, previous column list should be preserved."""
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        new_plan = _stajyer_filter_plan()
        result = await svc.process("s1", "tamam ama stajyer olanları getirme", new_plan)

        assert result.preserved_projection is True
        assert result.merged_plan is not None

        merged_cols_upper = [c.upper() for c in result.merged_plan.select_columns]
        # All previous columns must be present
        for col in ["PERSON_ID", "AD", "SOYAD", "SICIL_NO", "BIRIM_ADI", "ORGANIZATION_ADI"]:
            assert col in merged_cols_upper, f"Column {col} was dropped from projection"

    @pytest.mark.asyncio
    async def test_new_column_added_to_projection(self, svc: FollowupContextMergeService) -> None:
        """New columns in follow-up plan should be ADDED to projection (union)."""
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        new_plan = _stajyer_filter_plan()
        result = await svc.process("s1", "tamam ama stajyer olanları getirme", new_plan)

        assert result.merged_plan is not None
        merged_cols_upper = [c.upper() for c in result.merged_plan.select_columns]
        assert "STAJYER" in merged_cols_upper, "New column STAJYER should be added to projection"


# ── Test 4: Explicit filter override ─────────────────────────────────────────


class TestExplicitFilterOverride:
    @pytest.mark.asyncio
    async def test_same_column_filter_is_replaced_not_duplicated(self, svc: FollowupContextMergeService) -> None:
        """If new plan has a filter on the same column as previous, use new value."""
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        new_plan = QueryPlan(
            intent="Muhasebe departmanı",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["PERSON_ID", "AD"],
            filters=[
                FilterSpec(column="BIRIM_ADI", op=FilterOp.EQ, value="MUHASEBE"),
            ],
            limit=100,
        )
        result = await svc.process("s1", "ama muhasebe departmanına bak", new_plan)

        if result.merge_strategy == "patch":
            assert result.merged_plan is not None
            birim_filters = [f for f in result.merged_plan.filters if f.column.upper() == "BIRIM_ADI"]
            # Must have exactly ONE BIRIM_ADI filter (not duplicated)
            assert len(birim_filters) == 1
            # Must use the new value
            assert birim_filters[0].value == "MUHASEBE"


# ── Test 5: Low-confidence follow-up falls back to fresh-query behaviour ──────


class TestLowConfidenceFallback:
    @pytest.mark.asyncio
    async def test_no_trigger_phrase_is_not_followup(self, svc: FollowupContextMergeService) -> None:
        """Messages without trigger phrases must NOT be treated as follow-ups."""
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        new_plan = QueryPlan(
            intent="Tüm çalışanlar",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["PERSON_ID", "AD"],
            filters=[],
            limit=100,
        )
        result = await svc.process("s1", "Tüm çalışanları listele", new_plan)

        assert result.followup_detected is False
        assert result.merge_strategy == "none"
        assert result.merged_plan is None
        assert "classified:fresh_query" in result.reason_codes

    @pytest.mark.asyncio
    async def test_table_change_is_not_followup(self, svc: FollowupContextMergeService) -> None:
        """If the new plan uses a different table, it is NOT a follow-up."""
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        new_plan = QueryPlan(
            intent="Fatura listesi",
            table="AP_INVOICES_V",
            select_columns=["INVOICE_ID"],
            filters=[],
            limit=100,
        )
        result = await svc.process("s1", "ama faturalara bak", new_plan)

        assert result.followup_detected is False
        assert result.merge_strategy == "none"
        assert "table_changed" in result.reason_codes

    @pytest.mark.asyncio
    async def test_low_confidence_merged_plan_is_none(self, svc: FollowupContextMergeService) -> None:
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        result = await svc.process(
            "s1",
            "Tüm aktif personelin listesini ver",  # no trigger phrase
            QueryPlan(
                intent="Aktif personel",
                table="XXBT_PDKS_PER_DETAILS_V",
                select_columns=["PERSON_ID"],
                filters=[],
                limit=100,
            ),
        )
        assert result.merged_plan is None


# ── Test 6: Helper / title / tag requests do NOT overwrite snapshot ───────────


class TestHelperSafety:
    def test_snapshot_not_overwritten_by_separate_session(self, svc: FollowupContextMergeService) -> None:
        """Different session IDs for helper calls must not affect the main session snapshot."""
        main_session = "owui-conv-main"
        helper_session = "owui-conv-helper-title"

        main_plan = _department_plan()
        svc.record_success(main_session, main_plan)

        # Helper call uses a different session — does NOT call record_success
        # (the test verifies that helper sessions are isolated from main)
        helper_snapshot = svc.get_snapshot(helper_session)
        assert helper_snapshot is None, "Helper session must have no snapshot"

        main_snapshot = svc.get_snapshot(main_session)
        assert main_snapshot is not None
        assert main_snapshot.table == "XXBT_PDKS_PER_DETAILS_V"

    def test_snapshot_isolated_per_session(self, svc: FollowupContextMergeService) -> None:
        """Snapshots from different sessions are fully independent."""
        plan_a = _department_plan()
        plan_b = QueryPlan(
            intent="Muhasebe",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["PERSON_ID"],
            filters=[FilterSpec(column="BIRIM_ADI", op=FilterOp.EQ, value="MUHASEBE")],
            limit=50,
        )
        svc.record_success("session-A", plan_a)
        svc.record_success("session-B", plan_b)

        snap_a = svc.get_snapshot("session-A")
        snap_b = svc.get_snapshot("session-B")

        assert snap_a is not None and snap_b is not None
        assert snap_a.filters[0].value == "ELEKTRİK DİZAYN"
        assert snap_b.filters[0].value == "MUHASEBE"


# ── Test 7: Trace payload fields ─────────────────────────────────────────────


class TestTracePayload:
    _REQUIRED_FIELDS = {
        "previous_snapshot_found",
        "previous_snapshot_status",
        "followup_detected",
        "followup_confidence",
        "merge_strategy",
        "preserved_filters",
        "added_filters",
        "dropped_filters",
        "preserved_projection",
        "reason_codes",
    }

    @pytest.mark.asyncio
    async def test_merge_result_has_all_required_trace_fields(self, svc: FollowupContextMergeService) -> None:
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        new_plan = _stajyer_filter_plan()
        result = await svc.process("s1", "tamam ama stajyer olanları getirme", new_plan)

        payload = result.to_trace_payload()
        missing = self._REQUIRED_FIELDS - set(payload.keys())
        assert not missing, f"Missing trace fields: {missing}"

    @pytest.mark.asyncio
    async def test_trace_payload_followup_detected_true(self, svc: FollowupContextMergeService) -> None:
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        new_plan = _stajyer_filter_plan()
        result = await svc.process("s1", "tamam ama stajyer olanları getirme", new_plan)

        payload = result.to_trace_payload()
        assert payload["followup_detected"] is True
        assert payload["merge_strategy"] == "patch"
        assert payload["previous_snapshot_found"] is True
        assert payload["previous_snapshot_status"] == "success"
        assert isinstance(payload["preserved_filters"], list)
        assert isinstance(payload["added_filters"], list)
        assert isinstance(payload["reason_codes"], list)

    @pytest.mark.asyncio
    async def test_trace_payload_no_snapshot(self, svc: FollowupContextMergeService) -> None:
        new_plan = _stajyer_filter_plan()
        result = await svc.process("no-session", "tamam ama stajyer olanları getirme", new_plan)

        payload = result.to_trace_payload()
        assert self._REQUIRED_FIELDS.issubset(set(payload.keys()))
        assert payload["previous_snapshot_found"] is False
        assert payload["previous_snapshot_status"] == "none"
        assert payload["followup_detected"] is False
        assert payload["merge_strategy"] == "none"


# ── Test 8: No previous snapshot → no-op ─────────────────────────────────────


class TestNoSnapshot:
    @pytest.mark.asyncio
    async def test_no_snapshot_returns_none_strategy(self, svc: FollowupContextMergeService) -> None:
        result = await svc.process(
            "fresh-session",
            "tamam ama stajyer olanları getirme",
            _stajyer_filter_plan(),
        )
        assert result.followup_detected is False
        assert result.merge_strategy == "none"
        assert result.merged_plan is None
        assert result.previous_snapshot_found is False
        assert "no_previous_snapshot" in result.reason_codes

    @pytest.mark.asyncio
    async def test_process_safe_without_record(self, svc: FollowupContextMergeService) -> None:
        """process() must not raise even if record_success was never called."""
        result = await svc.process(
            "never-recorded",
            "bir de stajyerleri çıkar",
            QueryPlan(
                intent="Test",
                table="XXBT_PDKS_PER_DETAILS_V",
                select_columns=["PERSON_ID"],
                filters=[],
                limit=10,
            ),
        )
        assert result.merged_plan is None


# ── Test 9: Table change → low confidence ─────────────────────────────────────


class TestTableChange:
    @pytest.mark.asyncio
    async def test_different_table_is_not_followup(self, svc: FollowupContextMergeService) -> None:
        svc.record_success("s1", _department_plan())

        result = await svc.process(
            "s1",
            "ama fatura tablosuna bak",
            QueryPlan(
                intent="Fatura",
                table="AP_INVOICES_V",
                select_columns=["INVOICE_ID"],
                filters=[],
                limit=100,
            ),
        )
        assert result.followup_detected is False
        assert "table_changed" in result.reason_codes
        assert result.merge_strategy == "none"


# ── Test 10: Union projection ─────────────────────────────────────────────────


class TestUnionProjection:
    @pytest.mark.asyncio
    async def test_unique_columns_from_both_plans_are_merged(self, svc: FollowupContextMergeService) -> None:
        prev_plan = _department_plan()  # has PERSON_ID, AD, SOYAD, SICIL_NO, BIRIM_ADI, ORGANIZATION_ADI
        svc.record_success("s1", prev_plan)

        new_plan = QueryPlan(
            intent="Stajyer ve aktiflik",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["PERSON_ID", "STAJYER", "CIKIS_TARIHI"],
            filters=[FilterSpec(column="STAJYER", op=FilterOp.NEQ, value="Y")],
            limit=100,
        )
        result = await svc.process("s1", "ama stajyer olmayanlar ve cikis tarihi", new_plan)

        assert result.merged_plan is not None
        cols_upper = [c.upper() for c in result.merged_plan.select_columns]

        # Previous columns preserved
        for col in ["PERSON_ID", "AD", "SOYAD", "SICIL_NO", "BIRIM_ADI", "ORGANIZATION_ADI"]:
            assert col in cols_upper

        # New columns added
        assert "STAJYER" in cols_upper
        assert "CIKIS_TARIHI" in cols_upper

    @pytest.mark.asyncio
    async def test_no_duplicate_columns_in_projection(self, svc: FollowupContextMergeService) -> None:
        prev_plan = _department_plan()  # includes PERSON_ID
        svc.record_success("s1", prev_plan)

        new_plan = QueryPlan(
            intent="duplicate test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["PERSON_ID", "STAJYER"],  # PERSON_ID already in prev
            filters=[FilterSpec(column="STAJYER", op=FilterOp.NEQ, value="Y")],
            limit=100,
        )
        result = await svc.process("s1", "tamam ama stajyer olanları getirme", new_plan)

        assert result.merged_plan is not None
        cols_upper = [c.upper() for c in result.merged_plan.select_columns]
        # PERSON_ID should appear only once
        assert cols_upper.count("PERSON_ID") == 1


# ── Test: trigger phrase variants ─────────────────────────────────────────────


class TestTriggerPhrases:
    @pytest.mark.parametrize("message", [
        "tamam ama stajyerleri çıkar",
        "hariç stajyerler",
        "sadece aktif olanlar",
        "yalnız muhasebe departmanı",
        "bir de şunu ekle",
        "ek olarak cikis tarihi filtresi",
        "fakat bordrolu olanlar",
        "getirme stajyerleri",
    ])
    @pytest.mark.asyncio
    async def test_known_trigger_phrases_detected(
        self,
        svc: FollowupContextMergeService,
        message: str,
    ) -> None:
        svc.record_success("s1", _department_plan())
        new_plan = QueryPlan(
            intent="Test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["PERSON_ID"],
            filters=[FilterSpec(column="STAJYER", op=FilterOp.NEQ, value="Y")],
            limit=100,
        )
        result = await svc.process("s1", message, new_plan)
        assert result.followup_detected is True, f"Expected follow-up for: {message!r}"

    @pytest.mark.parametrize("message", [
        "Tüm çalışanları listele",
        "Muhasebe departmanındaki faturalar neler?",
        "Son aya ait satınalmalar",
        "2024 yılındaki girişler",
    ])
    @pytest.mark.asyncio
    async def test_non_trigger_messages_not_detected(
        self,
        svc: FollowupContextMergeService,
        message: str,
    ) -> None:
        svc.record_success("s1", _department_plan())
        new_plan = QueryPlan(
            intent="Test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["PERSON_ID"],
            filters=[],
            limit=100,
        )
        result = await svc.process("s1", message, new_plan)
        assert result.followup_detected is False, f"Expected fresh query for: {message!r}"


# ── Test: narrative correction replays previous plan ──────────────────────────


class TestNarrativeCorrection:
    """When user corrects the interpretation (not the data), replay exact previous plan."""

    @pytest.mark.asyncio
    async def test_emin_misin_triggers_narrative_correction(self, svc: FollowupContextMergeService) -> None:
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan, answer_preview="Çalışan henüz doğmamıştır")

        # Planner produces a broken plan (needs_clarification=True, no table)
        broken_plan = QueryPlan(
            intent="?",
            table=None,
            select_columns=[],
            filters=[],
            limit=10,
            needs_clarification=True,
            clarification_message="Hangi kaynağı kullanmamı istersiniz?",
        )
        result = await svc.process(
            "s1",
            "emin misin 1997'de doğmuş dedin nasıl doğmamış oluyor",
            broken_plan,
        )

        assert result.followup_detected is True
        assert result.message_type == "narrative_correction"
        assert result.merge_strategy == "patch"
        assert result.merged_plan is not None
        assert result.merged_plan.needs_clarification is False

    @pytest.mark.asyncio
    async def test_narrative_correction_restores_table(self, svc: FollowupContextMergeService) -> None:
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        broken_plan = QueryPlan(
            intent="?",
            table=None,
            select_columns=[],
            filters=[],
            limit=10,
            needs_clarification=True,
            clarification_message="Lütfen açıklayın.",
        )
        result = await svc.process("s1", "yanlış hesapladın tekrar bak", broken_plan)

        assert result.merged_plan is not None
        assert result.merged_plan.table == "XXBT_PDKS_PER_DETAILS_V"

    @pytest.mark.asyncio
    async def test_narrative_correction_restores_all_filters(self, svc: FollowupContextMergeService) -> None:
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        broken_plan = QueryPlan(
            intent="?",
            table=None,
            select_columns=[],
            filters=[],
            limit=10,
            needs_clarification=True,
            clarification_message="Lütfen açıklayın.",
        )
        result = await svc.process("s1", "doğru değil tekrar hesapla", broken_plan)

        assert result.merged_plan is not None
        filter_cols = {f.column.upper() for f in result.merged_plan.filters}
        assert "BIRIM_ADI" in filter_cols

    @pytest.mark.asyncio
    async def test_narrative_correction_restores_columns(self, svc: FollowupContextMergeService) -> None:
        prev_plan = _department_plan()
        svc.record_success("s1", prev_plan)

        broken_plan = QueryPlan(
            intent="?",
            table=None,
            select_columns=[],
            filters=[],
            limit=10,
            needs_clarification=True,
            clarification_message="Lütfen açıklayın.",
        )
        result = await svc.process("s1", "yanılıyorsun kontrol et", broken_plan)

        assert result.merged_plan is not None
        cols_upper = [c.upper() for c in result.merged_plan.select_columns]
        for col in ["AD", "SOYAD", "BIRIM_ADI"]:
            assert col in cols_upper

    @pytest.mark.parametrize("message", [
        "emin misin",
        "yanlış cevap",
        "doğru değil",
        "nasıl doğmamış oluyor",
        "yanlış hesapladın",
        "yanılıyorsun",
        "hatalı",
        "tekrar bak",
    ])
    @pytest.mark.asyncio
    async def test_correction_signals_detected(
        self,
        svc: FollowupContextMergeService,
        message: str,
    ) -> None:
        svc.record_success("s1", _department_plan())
        plan = QueryPlan(
            intent="?", table=None, select_columns=[], filters=[], limit=10,
            needs_clarification=True, clarification_message="?",
        )
        result = await svc.process("s1", message, plan)
        assert result.followup_detected is True, f"Expected narrative_correction for: {message!r}"
        assert result.message_type == "narrative_correction"


# ── Test: comparison entity synthesis ─────────────────────────────────────────


def _person_plan() -> QueryPlan:
    """A person-specific plan with AD/SOYAD filters (Turn 1/2 snapshot)."""
    return QueryPlan(
        intent="FURKAN KİRAZ hakkında bilgi",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["DOGUM_TARIHI"],
        filters=[
            FilterSpec(column="AD", op=FilterOp.EQ, value="FURKAN"),
            FilterSpec(column="SOYAD", op=FilterOp.EQ, value="KİRAZ"),
        ],
        limit=100,
    )


class TestComparisonEntitySynthesis:
    """When planner produces empty-filter clarification plan for a comparison,
    the reducer should synthesise IN-filters from the new entity name."""

    @pytest.mark.asyncio
    async def test_comparison_synthesises_in_filters(self, svc: FollowupContextMergeService) -> None:
        svc.record_success("s1", _person_plan())

        # Planner produces clarification plan with no filters
        broken_plan = QueryPlan(
            intent="yaş farkı hesapla",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["DOGUM_TARIHI"],
            filters=[],
            limit=100,
            needs_clarification=True,
            clarification_message="Hangi kişi?",
        )
        result = await svc.process(
            "s1",
            "Peki bu kişi ile AHMET UYGUN'un arasındaki yaş farkı nedir",
            broken_plan,
        )

        assert result.followup_detected is True
        assert result.message_type == "comparison_request"
        assert result.merged_plan is not None
        assert result.merged_plan.needs_clarification is False

        # Must have IN filters for both AD and SOYAD
        in_filters = {
            f.column.upper(): f
            for f in result.merged_plan.filters
            if f.op == FilterOp.IN
        }
        assert "AD" in in_filters, "AD should have IN filter"
        assert "SOYAD" in in_filters, "SOYAD should have IN filter"

        ad_values = sorted(v.upper() for v in in_filters["AD"].value)
        assert ad_values == ["AHMET", "FURKAN"]

        soyad_values = sorted(v.upper() for v in in_filters["SOYAD"].value)
        assert soyad_values == ["KİRAZ", "UYGUN"]

    @pytest.mark.asyncio
    async def test_comparison_restores_table_from_snapshot(self, svc: FollowupContextMergeService) -> None:
        svc.record_success("s1", _person_plan())

        broken_plan = QueryPlan(
            intent="yaş farkı",
            table=None,
            select_columns=[],
            filters=[],
            limit=10,
            needs_clarification=True,
            clarification_message="?",
        )
        result = await svc.process(
            "s1",
            "AHMET UYGUN ile arasındaki fark nedir",
            broken_plan,
        )

        assert result.merged_plan is not None
        assert result.merged_plan.table == "XXBT_PDKS_PER_DETAILS_V"

    @pytest.mark.asyncio
    async def test_comparison_with_existing_filters_still_works(self, svc: FollowupContextMergeService) -> None:
        """When planner DOES extract the new entity filters, IN-merge works as before."""
        svc.record_success("s1", _person_plan())

        new_plan = QueryPlan(
            intent="yaş farkı",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["DOGUM_TARIHI"],
            filters=[
                FilterSpec(column="AD", op=FilterOp.EQ, value="AHMET"),
                FilterSpec(column="SOYAD", op=FilterOp.EQ, value="UYGUN"),
            ],
            limit=100,
        )
        result = await svc.process(
            "s1",
            "AHMET UYGUN ile arasındaki yaş farkı",
            new_plan,
        )

        assert result.followup_detected is True
        in_filters = {
            f.column.upper(): f
            for f in result.merged_plan.filters
            if f.op == FilterOp.IN
        }
        assert "AD" in in_filters
        assert "SOYAD" in in_filters

    @pytest.mark.asyncio
    async def test_comparison_clears_clarification(self, svc: FollowupContextMergeService) -> None:
        svc.record_success("s1", _person_plan())

        broken_plan = QueryPlan(
            intent="?",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=[],
            filters=[],
            limit=10,
            needs_clarification=True,
            clarification_message="Detay veriniz.",
        )
        result = await svc.process(
            "s1",
            "bu kişi ile MEHMET YILMAZ arasındaki fark",
            broken_plan,
        )

        assert result.merged_plan is not None
        assert result.merged_plan.needs_clarification is False
        assert result.merged_plan.clarification_message is None
