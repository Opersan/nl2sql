"""Tests for glossary term resolution via SemanticFoundationRegistry.

Validates that:
- Turkish (TR) synonyms resolve to the correct canonical entity_id.
- English (EN) synonyms resolve to the correct canonical entity_id.
- Phrase-level substring matching works for inflected forms.
- filter_alias and metric_alias terms do NOT resolve to entity_ids.
- Ambiguous terms (e.g. "malzeme" → INV + PO) return multiple entries.
- All 6 modules are reachable via glossary terms.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.semantic.loader import load_semantic_foundation
from app.semantic.registry import SemanticFoundationRegistry


@pytest.fixture(scope="module")
def registry() -> SemanticFoundationRegistry:
    """Shared registry loaded from the production data directory."""
    data_dir = Path(__file__).resolve().parents[2] / "data" / "semantic"
    foundation = load_semantic_foundation(semantic_dir=data_dir)
    return SemanticFoundationRegistry(foundation)


# ---------------------------------------------------------------------------
# Turkish term resolution
# ---------------------------------------------------------------------------

class TestTurkishTermResolution:
    def test_calisan_resolves_to_hr_employees(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("calisan")
        entity_ids = {e.canonical for e in entries if not e.canonical.startswith(("filter:", "metric:"))}
        assert "HR_EMPLOYEES" in entity_ids

    def test_personel_resolves_to_hr_employees_via_phrase(self, registry: SemanticFoundationRegistry) -> None:
        matches = registry.resolve_phrases_in_text("aktif personel listesi")
        entity_ids = {e.canonical for e in matches}
        assert "HR_EMPLOYEES" in entity_ids

    def test_inflected_calisanlari_resolves_via_phrase(self, registry: SemanticFoundationRegistry) -> None:
        # "çalışanları" (accusative suffix) must still yield HR entity via phrase substring
        matches = registry.resolve_phrases_in_text("istanbul daki calisanlari getir")
        entity_ids = {e.canonical for e in matches}
        assert "HR_EMPLOYEES" in entity_ids

    def test_satin_alma_resolves_to_po_purchasing(self, registry: SemanticFoundationRegistry) -> None:
        matches = registry.resolve_phrases_in_text("satin alma siparislerini listele")
        entity_ids = {e.canonical for e in matches}
        assert "PO_PURCHASING" in entity_ids

    def test_siparis_resolves_to_po_purchasing_via_phrase(self, registry: SemanticFoundationRegistry) -> None:
        matches = registry.resolve_phrases_in_text("onaylanmis siparisler")
        entity_ids = {e.canonical for e in matches}
        assert "PO_PURCHASING" in entity_ids

    def test_fatura_resolves_to_ap_invoices(self, registry: SemanticFoundationRegistry) -> None:
        matches = registry.resolve_phrases_in_text("tedarikci fatura listesi")
        entity_ids = {e.canonical for e in matches}
        assert "AP_INVOICES" in entity_ids

    def test_alacak_resolves_to_ar_transactions(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("alacak")
        entity_ids = {e.canonical for e in entries}
        assert "AR_TRANSACTIONS" in entity_ids

    def test_muhasebe_resolves_to_gl_journal_via_phrase(self, registry: SemanticFoundationRegistry) -> None:
        matches = registry.resolve_phrases_in_text("muhasebe kayitlari")
        entity_ids = {e.canonical for e in matches}
        assert "GL_JOURNAL_ENTRIES" in entity_ids

    def test_stok_resolves_to_inv_items(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("stok")
        entity_ids = {e.canonical for e in entries}
        assert "INV_ITEMS" in entity_ids


# ---------------------------------------------------------------------------
# English term resolution
# ---------------------------------------------------------------------------

class TestEnglishTermResolution:
    def test_employee_resolves_to_hr_employees(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("employee")
        entity_ids = {e.canonical for e in entries}
        assert "HR_EMPLOYEES" in entity_ids

    def test_vendor_resolves_to_po_purchasing(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("vendor")
        entity_ids = {e.canonical for e in entries}
        assert "PO_PURCHASING" in entity_ids

    def test_invoice_resolves_to_ap_invoices(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("invoice")
        entity_ids = {e.canonical for e in entries}
        assert "AP_INVOICES" in entity_ids

    def test_customer_resolves_to_ar_transactions(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("customer")
        entity_ids = {e.canonical for e in entries}
        assert "AR_TRANSACTIONS" in entity_ids

    def test_journal_resolves_to_gl_journal_entries(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("journal")
        entity_ids = {e.canonical for e in entries}
        assert "GL_JOURNAL_ENTRIES" in entity_ids

    def test_inventory_resolves_to_inv_items(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("inventory")
        entity_ids = {e.canonical for e in entries}
        assert "INV_ITEMS" in entity_ids


# ---------------------------------------------------------------------------
# Filter and metric aliases do NOT resolve to entity_ids
# ---------------------------------------------------------------------------

class TestAliasTypes:
    def test_filter_alias_canonical_not_entity_id(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("aktif")
        # aktif is a filter_alias — canonical starts with "filter:"
        assert all(e.canonical.startswith("filter:") for e in entries)

    def test_metric_alias_canonical_starts_with_metric(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("sayisi")
        # sayisi is a metric_alias
        metric_entries = [e for e in entries if e.canonical.startswith("metric:")]
        assert len(metric_entries) > 0


# ---------------------------------------------------------------------------
# Ambiguous terms map to multiple entities
# ---------------------------------------------------------------------------

class TestAmbiguousTerms:
    def test_malzeme_maps_to_inv_and_po(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("malzeme")
        entity_ids = {e.canonical for e in entries}
        # malzeme appears in both INV and PO contexts
        assert "INV_ITEMS" in entity_ids
        assert "PO_PURCHASING" in entity_ids

    def test_urun_maps_to_inv_and_po(self, registry: SemanticFoundationRegistry) -> None:
        entries = registry.resolve_term("urun")
        entity_ids = {e.canonical for e in entries}
        assert "INV_ITEMS" in entity_ids
        assert "PO_PURCHASING" in entity_ids


# ---------------------------------------------------------------------------
# Full coverage: all 6 modules reachable from glossary
# ---------------------------------------------------------------------------

def test_all_six_modules_reachable_from_glossary(registry: SemanticFoundationRegistry) -> None:
    """Scan all phrase entries for all 6 expected modules."""
    all_entries = registry._phrase_entries + [
        (k, e) for k, entries in registry._term_index.items() for e in entries
    ]
    canonical_set = {e.canonical for _, e in all_entries}
    expected = {"HR_EMPLOYEES", "PO_PURCHASING", "AP_INVOICES",
                "AR_TRANSACTIONS", "GL_JOURNAL_ENTRIES", "INV_ITEMS"}
    missing = expected - canonical_set
    assert not missing, f"No glossary entries reach these entities: {missing}"
