from __future__ import annotations

from app.services.narrator_service import NarratorService
from scripts.e2e_real_provider_eval import _classify_narration_policy_violations, _sanitize_narration_output


def test_strip_reasoning_sql_ora_and_code_blocks() -> None:
    raw = """
Thinking:
1. Analyze request
```sql
SELECT * FROM PO_HEADERS_ALL
```
ORA-00942: table or view does not exist
Sonuç: 10 kayıt bulundu.
"""
    cleaned = NarratorService._strip_leakage(raw)  # noqa: SLF001

    assert "Thinking:" not in cleaned
    assert "SELECT" not in cleaned.upper()
    assert "ORA-" not in cleaned
    assert "10" in cleaned


def test_strip_returns_safe_default_when_only_leakage() -> None:
    raw = "Draft:\nSELECT po_header_id FROM PO_HEADERS_ALL\nORA-01858"
    cleaned = NarratorService._strip_leakage(raw)  # noqa: SLF001
    assert cleaned == "Sorgu işlendi."


def test_narrator_sanitizer_strips_think_block() -> None:
    raw = "<think>Analyze the Request\nDraft 1\n</think>\nToplam 7 kayıt bulundu."
    cleaned = NarratorService._strip_leakage(raw)  # noqa: SLF001
    assert "<think>" not in cleaned.lower()
    assert "analyze the request" not in cleaned.lower()
    assert cleaned == "Toplam 7 kayıt bulundu."


def test_narrator_sanitizer_strips_heading_reasoning() -> None:
    raw = """
Thinking Process:
1. Analyze the Request
2. Draft the Response
Final Polish:
Kriterlere uygun kayıt bulunamadı.
"""
    cleaned = NarratorService._strip_leakage(raw)  # noqa: SLF001
    assert "thinking process" not in cleaned.lower()
    assert "analyze the request" not in cleaned.lower()
    assert "draft" not in cleaned.lower()
    assert cleaned == "Kriterlere uygun kayıt bulunamadı."


def test_narrator_sanitizer_keeps_final_business_sentence() -> None:
    raw = """
Thinking Process:
1. Analyze
2. Draft

Toplam 100 aktif çalışan kaydı listelenmiştir.
"""
    cleaned = NarratorService._strip_leakage(raw)  # noqa: SLF001
    assert cleaned == "Toplam 100 aktif çalışan kaydı listelenmiştir."


def test_narrator_sanitizer_fallback_success_summary() -> None:
    raw = "Thinking Process:\nRule 7: Do not write SELECT/FROM"
    out = _sanitize_narration_output(
        raw_response=raw,
        answer=None,
        raw_status="success",
        expected_context={"source_summary_text_for_narrator": "Sorgu başarılı. Satır sayısı: 100."},
    )
    assert out["sanitizer_mode"] == "fallback_summary"
    assert out["final_response_source"] == "fallback"
    assert out["final_response"] == "Toplam 100 kayıt listelendi."


def test_narrator_sanitizer_fallback_error() -> None:
    raw = "Thinking Process:\nAnalyze the Request\nDo not show Oracle error codes"
    out = _sanitize_narration_output(
        raw_response=raw,
        answer=None,
        raw_status="execution_error",
        expected_context={"source_summary_text_for_narrator": "Çalıştırma hatası. Hata: ORA-00942"},
    )
    assert out["sanitizer_mode"] == "fallback_error"
    assert out["final_response"] == "İşlem tamamlanamadı. Lütfen daha sonra tekrar deneyin."


def test_sql_leak_not_triggered_by_policy_echo() -> None:
    checks = _classify_narration_policy_violations("Rule 7: Do not write SELECT/FROM in output.")
    assert checks["policy_echo_leak"] is True
    assert checks["sql_leak"] is False


def test_prompt_echo_classified_separately() -> None:
    checks = _classify_narration_policy_violations("Kullanıcı sorusu: Aktif çalışanlar\nSonuç özeti: Satır sayısı: 10")
    assert checks["prompt_echo_leak"] is True
    assert checks["policy_echo_leak"] is False


def test_final_response_source_sanitized_mapping() -> None:
    out = _sanitize_narration_output(
        raw_response="Thinking Process:\n1. Analyze\nToplam 3 kayıt bulundu.",
        answer=None,
        raw_status="success",
        expected_context={"source_summary_text_for_narrator": "Sorgu başarılı. Satır sayısı: 3."},
    )
    assert out["final_response_source"] == "sanitized"
    assert out["final_response"] == out["sanitized_response"]


def test_final_response_mapping_error_detection() -> None:
    source = "sanitized"
    sanitized = "Toplam 5 kayıt bulundu."
    final = "Farklı metin"
    mapping_error = (source == "sanitized" and final != sanitized) or (source == "raw" and final != "RAW")
    assert mapping_error is True


def test_final_response_policy_clean() -> None:
    out = _sanitize_narration_output(
        raw_response="Thinking Process:\n1. Analyze\n2. Draft\nKriterlere uygun kayıt bulunamadı.",
        answer=None,
        raw_status="empty_result",
        expected_context={"source_summary_text_for_narrator": "Satır sayısı: 0. Sonuç bulunamadı."},
    )
    assert out["final_response_policy_violations"] == []
