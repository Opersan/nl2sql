"""
title: NL2SQL Results Table
author: Codex
version: 1.2.1
required_open_webui_version: 0.6.0

description: >
  Render markdown tables as a native-looking Open WebUI interactive results table
  with compact toolbar, search, sorting, pagination, all rows option
  and Excel export for displayed rows.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from html import escape
import json
import re
from typing import Any, Mapping, Optional

from pydantic import BaseModel, Field

_TABLE_HEADER_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*[-:]+\s*(\|\s*[-:]+\s*)+\|?\s*$")


@dataclass(frozen=True)
class ParsedTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    footer: str | None = None
    narration: str | None = None


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _text_from_output(output: Any) -> str:
    texts: list[str] = []
    if not isinstance(output, list):
        return ""

    for item in output:
        item_map = _as_mapping(item)
        if item_map is None:
            continue

        content = item_map.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content.strip())
            continue

        if not isinstance(content, list):
            continue

        for block in content:
            block_map = _as_mapping(block)
            if block_map is None:
                continue
            text = block_map.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())

    return "\n".join(texts).strip()


def _content_from_body(body: Mapping[str, Any]) -> str:
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        first = _as_mapping(choices[0])
        if first is not None:
            message = _as_mapping(first.get("message"))
            if message is not None:
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
                output_text = _text_from_output(message.get("output"))
                if output_text:
                    return output_text

    messages = body.get("messages")
    if isinstance(messages, list):
        for message_item in reversed(messages):
            message_map = _as_mapping(message_item)
            if message_map is None:
                continue
            if message_map.get("role") != "assistant":
                continue
            content = message_map.get("content")
            if isinstance(content, str) and content.strip():
                return content
            output_text = _text_from_output(message_map.get("output"))
            if output_text:
                return output_text

    content = body.get("content")
    if isinstance(content, str) and content.strip():
        return content

    output_text = _text_from_output(body.get("output"))
    if output_text:
        return output_text

    return ""


def _split_table_row(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [cell.strip() for cell in raw.split("|")]


def _parse_markdown_table(content: str) -> ParsedTable | None:
    lines = [ln.rstrip() for ln in content.splitlines()]
    if not lines:
        return None

    start_idx = None
    for i in range(len(lines) - 1):
        if _TABLE_HEADER_RE.match(lines[i]) and _TABLE_SEPARATOR_RE.match(lines[i + 1]):
            start_idx = i
            break

    if start_idx is None:
        return None

    headers = _split_table_row(lines[start_idx])
    if not headers or all(not h for h in headers):
        return None

    rows: list[list[str]] = []
    footer_lines: list[str] = []
    end_idx = len(lines)

    for idx in range(start_idx + 2, len(lines)):
        ln = lines[idx]
        if _TABLE_HEADER_RE.match(ln):
            row = _split_table_row(ln)
            if not any(cell.strip() for cell in row):
                continue
            if len(row) < len(headers):
                row += [""] * (len(headers) - len(row))
            rows.append(row[: len(headers)])
            continue

        if ln.strip():
            footer_lines.append(ln.strip())
        end_idx = idx + 1
        break

    if not rows:
        return None

    footer = "\n".join(footer_lines).strip() if footer_lines else None
    narration_lines = [ln for ln in lines[:start_idx] if ln.strip()]
    if end_idx < len(lines):
        narration_lines.extend(ln for ln in lines[end_idx:] if ln.strip())
    narration = "\n".join(narration_lines).strip() if narration_lines else None

    return ParsedTable(
        headers=tuple(headers),
        rows=tuple(tuple(r) for r in rows),
        footer=footer,
        narration=narration,
    )


def build_results_table_embed_html(parsed: ParsedTable) -> str:
    payload = {
        "headers": list(parsed.headers),
        "rows": [list(row) for row in parsed.rows],
        "footer": parsed.footer or "",
    }
    dataset_attr = escape(json.dumps(payload), quote=True)

    html = """<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    :root {
      color-scheme: light dark;
      --ow-surface: rgba(255,255,255,0.96);
      --ow-surface-soft: rgba(15,23,42,0.03);
      --ow-surface-softer: rgba(15,23,42,0.02);
      --ow-border: rgba(15,23,42,0.08);
      --ow-border-strong: rgba(15,23,42,0.12);
      --ow-text: rgba(15,23,42,0.94);
      --ow-text-soft: rgba(71,85,105,0.90);
      --ow-text-faint: rgba(100,116,139,0.82);
      --ow-accent: #2563eb;
      --ow-accent-soft: rgba(37,99,235,0.10);
      --ow-accent-border: rgba(37,99,235,0.22);
      --ow-shadow: 0 8px 24px rgba(15,23,42,0.08);
      --ow-radius-xl: 16px;
      --ow-radius-lg: 12px;
      --ow-radius-md: 10px;
      --ow-font: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --ow-surface: rgba(17,24,39,0.92);
        --ow-surface-soft: rgba(255,255,255,0.05);
        --ow-surface-softer: rgba(255,255,255,0.03);
        --ow-border: rgba(255,255,255,0.08);
        --ow-border-strong: rgba(255,255,255,0.12);
        --ow-text: rgba(241,245,249,0.96);
        --ow-text-soft: rgba(148,163,184,0.92);
        --ow-text-faint: rgba(148,163,184,0.76);
        --ow-accent: #60a5fa;
        --ow-accent-soft: rgba(96,165,250,0.14);
        --ow-accent-border: rgba(96,165,250,0.26);
        --ow-shadow: 0 14px 34px rgba(0,0,0,0.28);
      }
    }

    * { box-sizing: border-box; }

    html, body {
      margin: 0;
      padding: 0;
      background: transparent;
      color: var(--ow-text);
      font-family: var(--ow-font);
      font-size: 14px;
      line-height: 1.45;
    }

    .ow-wrap {
      width: 100%;
      padding: 10px 0;
    }

    .ow-card {
      width: 100%;
      border: 1px solid var(--ow-border);
      border-radius: var(--ow-radius-xl);
      background: var(--ow-surface);
      box-shadow: var(--ow-shadow);
      overflow: hidden;
      backdrop-filter: blur(8px);
    }

    .ow-topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--ow-border);
    }

    .ow-title-side {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
      flex-wrap: wrap;
    }

    .ow-title {
      font-size: 14px;
      font-weight: 700;
      color: var(--ow-text);
      white-space: nowrap;
    }

    .ow-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 3px 9px;
      border-radius: 999px;
      border: 1px solid var(--ow-accent-border);
      background: var(--ow-accent-soft);
      color: var(--ow-accent);
      font-size: 11px;
      font-weight: 700;
      white-space: nowrap;
    }

    .ow-count {
      font-size: 12px;
      font-weight: 500;
      color: var(--ow-text-soft);
      white-space: nowrap;
    }

    .ow-toolbar {
      display: grid;
      grid-template-columns: minmax(220px, 1.3fr) minmax(180px, 0.95fr) minmax(220px, 1.1fr) auto;
      gap: 10px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--ow-border);
      background: linear-gradient(180deg, var(--ow-surface-softer), transparent);
      align-items: center;
    }

    .ow-field,
    .ow-select,
    .ow-button {
      height: 38px;
      border-radius: var(--ow-radius-md);
      border: 1px solid var(--ow-border);
      background: var(--ow-surface-soft);
      color: var(--ow-text);
      font: inherit;
      outline: none;
      transition: border-color .15s ease, box-shadow .15s ease, background .15s ease, transform .12s ease;
    }

    .ow-field,
    .ow-select {
      width: 100%;
      padding: 0 12px;
    }

    .ow-field::placeholder {
      color: var(--ow-text-faint);
    }

    .ow-field:focus,
    .ow-select:focus {
      border-color: var(--ow-accent-border);
      box-shadow: 0 0 0 3px var(--ow-accent-soft);
    }

    .ow-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 0 14px;
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
    }

    .ow-button:hover:not([disabled]) {
      transform: translateY(-1px);
    }

    .ow-button:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }

    .ow-button-secondary {
      border-color: var(--ow-border);
      background: var(--ow-surface-soft);
      color: var(--ow-text);
    }

    .ow-button-secondary:hover:not([disabled]) {
      border-color: var(--ow-border-strong);
      background: var(--ow-surface-softer);
    }

    .ow-table-wrap {
      width: 100%;
      overflow: auto;
    }

    table {
      width: 100%;
      min-width: 760px;
      border-collapse: separate;
      border-spacing: 0;
    }

    thead th {
      position: sticky;
      top: 0;
      z-index: 1;
      padding: 11px 12px;
      text-align: left;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--ow-text-faint);
      background: var(--ow-surface);
      border-bottom: 1px solid var(--ow-border);
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }

    thead th.is-active {
      color: var(--ow-text);
    }

    .ow-sort {
      margin-left: 6px;
      font-size: 10px;
      opacity: 0.7;
    }

    tbody td {
      padding: 11px 12px;
      border-bottom: 1px solid var(--ow-border);
      color: var(--ow-text);
      vertical-align: middle;
      word-break: break-word;
      background: transparent;
    }

    tbody tr:nth-child(even) td {
      background: var(--ow-surface-softer);
    }

    tbody tr:hover td {
      background: var(--ow-accent-soft);
    }

    .ow-empty {
      padding: 24px 12px;
      text-align: center;
      color: var(--ow-text-soft);
      font-size: 13px;
    }

    .ow-footerbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      padding: 12px 16px;
      border-top: 1px solid var(--ow-border);
      background: linear-gradient(0deg, var(--ow-surface-softer), transparent);
    }

    .ow-pager {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }

    .ow-meta {
      font-size: 12px;
      color: var(--ow-text-soft);
      white-space: nowrap;
    }

    .ow-page-size {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      color: var(--ow-text-soft);
    }

    .ow-page-size .ow-select {
      width: auto;
      min-width: 92px;
      height: 34px;
    }

    .ow-note {
      padding: 10px 16px 14px;
      border-top: 1px solid var(--ow-border);
      color: var(--ow-text-soft);
      font-size: 12px;
      white-space: pre-wrap;
    }

    @media (max-width: 1100px) {
      .ow-toolbar {
        grid-template-columns: 1fr 1fr;
      }
    }

    @media (max-width: 680px) {
      .ow-topbar,
      .ow-footerbar {
        flex-direction: column;
        align-items: stretch;
      }

      .ow-toolbar {
        display: grid;
        grid-template-columns: 1fr;
      }

      .ow-count {
        white-space: normal;
      }

      table {
        min-width: 640px;
      }
    }
  </style>
</head>
<body>
  <div class="ow-wrap" data-table="__DATASET__">
    <div class="ow-card">
      <div class="ow-topbar">
        <div class="ow-title-side">
          <div class="ow-title">Sonuç Tablosu</div>
          <div class="ow-badge">Canlı</div>
          <div class="ow-count" data-count>Toplam 0 kayıt</div>
        </div>

        <button class="ow-button ow-button-secondary" type="button" data-action="export-excel">
          Gösterileni Excel'e Aktar
        </button>
      </div>

      <div class="ow-toolbar">
        <input class="ow-field" type="search" data-global-search placeholder="Tablo içinde arayınız..." />
        <select class="ow-select" data-column-filter>
          <option value="">Tüm sütunlar</option>
        </select>
        <input class="ow-field" type="search" data-column-search placeholder="Seçili sütunda filtreleyiniz..." />
        <button class="ow-button ow-button-secondary" type="button" data-action="clear-filters">
          Temizle
        </button>
      </div>

      <div class="ow-table-wrap">
        <table>
          <thead></thead>
          <tbody></tbody>
        </table>
      </div>

      <div class="ow-footerbar">
        <div class="ow-pager">
          <button class="ow-button ow-button-secondary" type="button" data-action="prev">Önceki</button>
          <div class="ow-meta" data-meta></div>
          <button class="ow-button ow-button-secondary" type="button" data-action="next">Sonraki</button>
        </div>

        <label class="ow-page-size">
          Sayfa boyutu
          <select class="ow-select" data-page-size>
            <option value="10">10</option>
            <option value="25" selected>25</option>
            <option value="50">50</option>
            <option value="100">100</option>
            <option value="all">Tümü</option>
          </select>
        </label>
      </div>

      <div class="ow-note" data-footer></div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/xlsx/dist/xlsx.full.min.js"></script>
  <script>
    (function () {
      var root = document.querySelector("[data-table]");
      if (!root) return;

      var payload = {};
      try {
        payload = JSON.parse(root.getAttribute("data-table") || "{}");
      } catch (e) {
        payload = {};
      }

      var headers = Array.isArray(payload.headers) ? payload.headers : [];
      var allRows = Array.isArray(payload.rows) ? payload.rows : [];
      var footer = payload.footer || "";

      var globalSearchInput = root.querySelector("[data-global-search]");
      var columnFilterSelect = root.querySelector("[data-column-filter]");
      var columnSearchInput = root.querySelector("[data-column-search]");
      var pageSizeSelect = root.querySelector("[data-page-size]");
      var thead = root.querySelector("thead");
      var tbody = root.querySelector("tbody");
      var meta = root.querySelector("[data-meta]");
      var count = root.querySelector("[data-count]");
      var footerEl = root.querySelector("[data-footer]");
      var prevBtn = root.querySelector('[data-action="prev"]');
      var nextBtn = root.querySelector('[data-action="next"]');
      var exportBtn = root.querySelector('[data-action="export-excel"]');
      var clearBtn = root.querySelector('[data-action="clear-filters"]');

      var state = {
        page: 1,
        pageSize: 25,
        sortIndex: -1,
        sortDir: "asc",
        filteredRows: allRows.slice()
      };

      function escapeHtml(text) {
        return String(text).replace(/[&<>"']/g, function (ch) {
          return {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;"
          }[ch];
        });
      }

      function normalize(value) {
        return String(value || "")
          .toLocaleLowerCase("tr")
          .replace(/ı/g, "i");
      }

      function isNumericLike(value) {
        var v = String(value || "").trim();
        if (!v) return false;
        v = v.replace(/\\s/g, "").replace(/\\./g, "").replace(",", ".");
        return !isNaN(parseFloat(v));
      }

      function numericValue(value) {
        var v = String(value || "").trim();
        v = v.replace(/\\s/g, "").replace(/\\./g, "").replace(",", ".");
        return parseFloat(v);
      }

      function getEffectivePageSize() {
        if (pageSizeSelect.value === "all") {
          return Math.max(state.filteredRows.length, 1);
        }
        return parseInt(pageSizeSelect.value, 10) || 25;
      }

      function getDisplayedRows() {
        var effectivePageSize = getEffectivePageSize();
        if (pageSizeSelect.value === "all") {
          return state.filteredRows.slice();
        }
        var start = (state.page - 1) * effectivePageSize;
        return state.filteredRows.slice(start, start + effectivePageSize);
      }

      function populateColumnFilter() {
        var options = ['<option value="">Tüm sütunlar</option>'];
        headers.forEach(function (header, index) {
          options.push('<option value="' + index + '">' + escapeHtml(header) + '</option>');
        });
        columnFilterSelect.innerHTML = options.join("");
      }

      function renderHeader() {
        if (!thead) return;

        var html = "<tr>" + headers.map(function (header, index) {
          var active = state.sortIndex === index ? "is-active" : "";
          var arrow = "";
          if (state.sortIndex === index) {
            arrow = state.sortDir === "asc" ? "↑" : "↓";
          }
          return '<th class="' + active + '" data-col="' + index + '">' +
            escapeHtml(header) +
            '<span class="ow-sort">' + escapeHtml(arrow) + '</span>' +
          "</th>";
        }).join("") + "</tr>";

        thead.innerHTML = html;

        Array.prototype.slice.call(thead.querySelectorAll("th[data-col]")).forEach(function (th) {
          th.addEventListener("click", function () {
            var idx = parseInt(th.getAttribute("data-col") || "-1", 10);
            if (idx < 0) return;

            if (state.sortIndex === idx) {
              state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
            } else {
              state.sortIndex = idx;
              state.sortDir = "asc";
            }

            sortRows();
            state.page = 1;
            renderHeader();
            renderBody();
            renderMeta();
            reportHeight();
          });
        });
      }

      function sortRows() {
        if (state.sortIndex < 0) return;

        state.filteredRows.sort(function (a, b) {
          var av = a[state.sortIndex] == null ? "" : String(a[state.sortIndex]);
          var bv = b[state.sortIndex] == null ? "" : String(b[state.sortIndex]);

          if (isNumericLike(av) && isNumericLike(bv)) {
            var an = numericValue(av);
            var bn = numericValue(bv);
            return state.sortDir === "asc" ? an - bn : bn - an;
          }

          var cmp = av.localeCompare(bv, "tr", {
            numeric: true,
            sensitivity: "base"
          });

          return state.sortDir === "asc" ? cmp : -cmp;
        });
      }

      function filterRows() {
        var globalQuery = normalize(globalSearchInput.value || "");
        var selectedCol = columnFilterSelect.value;
        var columnQuery = normalize(columnSearchInput.value || "");

        state.filteredRows = allRows.filter(function (row) {
          var globalMatch = true;
          var columnMatch = true;

          if (globalQuery) {
            globalMatch = normalize(row.join(" ")).indexOf(globalQuery) !== -1;
          }

          if (selectedCol !== "" && columnQuery) {
            var colIndex = parseInt(selectedCol, 10);
            var cell = row[colIndex] == null ? "" : String(row[colIndex]);
            columnMatch = normalize(cell).indexOf(columnQuery) !== -1;
          } else if (selectedCol === "" && columnQuery) {
            columnMatch = normalize(row.join(" ")).indexOf(columnQuery) !== -1;
          }

          return globalMatch && columnMatch;
        });

        sortRows();
      }

      function renderBody() {
        if (!tbody) return;

        state.pageSize = getEffectivePageSize();

        var total = state.filteredRows.length;
        var totalPages = Math.max(1, Math.ceil(total / state.pageSize));

        if (state.page > totalPages) {
          state.page = totalPages;
        }

        var currentRows = getDisplayedRows();

        if (!currentRows.length) {
          tbody.innerHTML = '<tr><td class="ow-empty" colspan="' + Math.max(headers.length, 1) + '">Kayıt bulunamadı.</td></tr>';
          return;
        }

        var html = currentRows.map(function (row) {
          var cells = row.map(function (cell) {
            return "<td>" + escapeHtml(cell == null ? "" : String(cell)) + "</td>";
          }).join("");
          return "<tr>" + cells + "</tr>";
        }).join("");

        tbody.innerHTML = html;
      }

      function renderMeta() {
        state.pageSize = getEffectivePageSize();

        var total = state.filteredRows.length;
        var totalPages = pageSizeSelect.value === "all" ? 1 : Math.max(1, Math.ceil(total / state.pageSize));
        var displayedRows = getDisplayedRows();

        if (pageSizeSelect.value === "all") {
          state.page = 1;
        }

        var start = displayedRows.length ? ((pageSizeSelect.value === "all") ? 1 : ((state.page - 1) * state.pageSize) + 1) : 0;
        var end = displayedRows.length ? (start + displayedRows.length - 1) : 0;

        meta.textContent = "Sayfa " + state.page + " / " + totalPages + " · " + start + "-" + end + " / " + total + " kayıt";
        count.textContent = "Toplam " + total + " kayıt";

        prevBtn.disabled = state.page <= 1 || pageSizeSelect.value === "all";
        nextBtn.disabled = state.page >= totalPages || pageSizeSelect.value === "all";
        exportBtn.disabled = displayedRows.length === 0;
      }

      function exportDisplayedRowsToExcel() {
        var rows = getDisplayedRows();
        if (!rows.length || !window.XLSX) return;

        var data = [headers].concat(rows);
        var worksheet = XLSX.utils.aoa_to_sheet(data);
        var workbook = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(workbook, worksheet, "Sonuclar");

        var now = new Date();
        var pad = function (v) { return String(v).padStart(2, "0"); };
        var fileName =
          "sonuc_tablosu_" +
          now.getFullYear() +
          pad(now.getMonth() + 1) +
          pad(now.getDate()) + "_" +
          pad(now.getHours()) +
          pad(now.getMinutes()) +
          pad(now.getSeconds()) +
          ".xlsx";

        XLSX.writeFile(workbook, fileName);
      }

      function clearFilters() {
        globalSearchInput.value = "";
        columnFilterSelect.value = "";
        columnSearchInput.value = "";
        state.page = 1;
        refresh();
      }

      function refresh() {
        filterRows();
        renderHeader();
        renderBody();
        renderMeta();
        reportHeight();
      }

      globalSearchInput.addEventListener("input", function () {
        state.page = 1;
        refresh();
      });

      columnFilterSelect.addEventListener("change", function () {
        state.page = 1;
        refresh();
      });

      columnSearchInput.addEventListener("input", function () {
        state.page = 1;
        refresh();
      });

      pageSizeSelect.addEventListener("change", function () {
        state.page = 1;
        state.pageSize = getEffectivePageSize();
        renderBody();
        renderMeta();
        reportHeight();
      });

      prevBtn.addEventListener("click", function () {
        if (state.page <= 1 || pageSizeSelect.value === "all") return;
        state.page -= 1;
        renderBody();
        renderMeta();
        reportHeight();
      });

      nextBtn.addEventListener("click", function () {
        if (pageSizeSelect.value === "all") return;
        var totalPages = Math.max(1, Math.ceil(state.filteredRows.length / state.pageSize));
        if (state.page >= totalPages) return;
        state.page += 1;
        renderBody();
        renderMeta();
        reportHeight();
      });

      exportBtn.addEventListener("click", function () {
        exportDisplayedRowsToExcel();
      });

      clearBtn.addEventListener("click", function () {
        clearFilters();
      });

      if (footerEl) {
        footerEl.textContent = footer;
        if (!footer) {
          footerEl.style.display = "none";
        }
      }

      var lastHeight = 0;

      function reportHeight() {
        var h = Math.ceil((root.getBoundingClientRect().height || document.body.scrollHeight) + 8);
        if (Math.abs(h - lastHeight) < 2) return;
        lastHeight = h;
        try {
          parent.postMessage({ type: "iframe:height", height: h }, "*");
        } catch (e) {}
      }

      populateColumnFilter();
      refresh();

      window.addEventListener("load", reportHeight);
      window.addEventListener("resize", reportHeight);

      if (window.ResizeObserver) {
        new ResizeObserver(reportHeight).observe(root);
      }
    })();
  </script>
</body>
</html>
"""
    return html.replace("__DATASET__", dataset_attr)


def apply_embed_to_body(body: Mapping[str, Any], html: str) -> dict[str, Any]:
    def _clear_output_text(output: Any) -> None:
        if not isinstance(output, list):
            return

        for item in output:
            item_map = _as_mapping(item)
            if item_map is None:
                continue

            content = item_map.get("content")
            if isinstance(content, str):
                item_map["content"] = ""
                continue

            if not isinstance(content, list):
                continue

            for block in content:
                block_map = _as_mapping(block)
                if block_map is None:
                    continue
                if isinstance(block_map.get("text"), str):
                    block_map["text"] = ""

    updated = deepcopy(dict(body))

    messages = updated.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            if message.get("role") != "assistant":
                continue
            embeds = message.setdefault("embeds", [])
            if isinstance(embeds, list) and html not in embeds:
                embeds.append(html)
            _clear_output_text(message.get("output"))
            break

    embeds = updated.setdefault("embeds", [])
    if isinstance(embeds, list) and html not in embeds:
        embeds.append(html)

    _clear_output_text(updated.get("output"))
    return updated


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=110, description="Run after upstream response filters."
        )
        min_rows: int = Field(
            default=5, description="Minimum rows required to replace markdown table."
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def outlet(
        self,
        body: dict,
        __event_emitter__: Optional[callable] = None,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
    ) -> dict:
        content = _content_from_body(body)
        parsed = _parse_markdown_table(content)

        if parsed is None or len(parsed.rows) < self.valves.min_rows:
            return body

        html = build_results_table_embed_html(parsed)
        updated = apply_embed_to_body(body, html)

        if parsed.narration:
            updated["content"] = parsed.narration
            messages = updated.get("messages")
            if isinstance(messages, list):
                for message in reversed(messages):
                    if not isinstance(message, dict):
                        continue
                    if message.get("role") != "assistant":
                        continue
                    message["content"] = parsed.narration
                    break

        if __event_emitter__ is not None:
            if parsed.narration:
                await __event_emitter__(
                    {"type": "replace", "data": {"content": parsed.narration}}
                )
            await __event_emitter__({"type": "embeds", "data": {"embeds": [html]}})

        return updated