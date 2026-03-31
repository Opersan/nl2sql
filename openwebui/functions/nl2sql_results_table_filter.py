"""
title: NL2SQL Results Table
author: Codex
version: 0.1
required_open_webui_version: 0.6.0

description: >
  Render NL2SQL markdown tables as an interactive, paginated table
  with client-side search inside Open WebUI.
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
<html lang="tr"><head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    :root {
      color-scheme: light dark;
      --ink: rgba(236,238,244,0.96);
      --muted: rgba(170,176,192,0.78);
      --line: rgba(255,255,255,0.10);
      --panel: rgba(18,20,26,0.98);
      --panel-2: rgba(255,255,255,0.04);
      --accent: #5a7bd0;
      --accent-strong: #3f5fb8;
      --shadow: 0 12px 32px rgba(0,0,0,0.35);
    }
    @media (prefers-color-scheme: light) {
      :root {
        --ink: rgba(25,27,35,0.96);
        --muted: rgba(81,88,105,0.72);
        --line: rgba(25,27,35,0.12);
        --panel: rgba(255,255,255,0.98);
        --panel-2: rgba(25,27,35,0.04);
        --accent: #3a5ccc;
        --accent-strong: #2446a6;
        --shadow: 0 10px 28px rgba(16,24,40,0.12);
      }
    }
    *, *::before, *::after { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; background: transparent; color: var(--ink); font-family: "Space Grotesk", "Segoe UI", ui-sans-serif, system-ui, sans-serif; font-size: 14px; }
    .wrap { padding: 12px 0 12px; }
    .card {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--panel);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .header {
      display: flex; gap: 12px; align-items: center; justify-content: space-between;
      padding: 14px 16px; border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.04);
    }
    .header-left { display: flex; align-items: center; gap: 10px; }
    .title { font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); font-weight: 700; }
    .stat-pill {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 4px 8px; border-radius: 999px; font-size: 11px; font-weight: 600;
      color: rgba(255,255,255,0.96);
      background: var(--accent-strong);
      box-shadow: 0 6px 14px rgba(0,0,0,0.2);
    }
    .search {
      display: flex; align-items: center; gap: 8px;
      background: var(--panel-2); border: 1px solid var(--line);
      padding: 8px 12px; border-radius: 14px; min-width: 240px;
    }
    .search .icon { color: var(--muted); font-size: 12px; }
    .search input {
      background: transparent; border: none; outline: none; color: var(--ink);
      font-size: 13px; width: 100%;
    }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 680px; }
    thead th {
      text-align: left; font-size: 11px; letter-spacing: 0.05em; text-transform: uppercase;
      color: var(--muted); padding: 10px 12px; border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.04);
      position: sticky; top: 0; z-index: 1;
      cursor: pointer;
      user-select: none;
    }
    thead th .sort {
      margin-left: 6px; font-size: 10px; opacity: 0.6;
    }
    thead th.active { color: var(--ink); }
    tbody td { padding: 10px 12px; border-bottom: 1px solid var(--line); color: var(--ink); }
    tbody tr:nth-child(even) td { background: rgba(255,255,255,0.02); }
    tbody tr:hover td { background: rgba(90,123,208,0.16); }
    .footer { padding: 10px 16px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); }
    .pagination { display: flex; align-items: center; gap: 8px; }
    .pager {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 6px 12px; border-radius: 10px;
      background: var(--panel-2); border: 1px solid var(--line);
      color: var(--ink); font-size: 12px; cursor: pointer;
      transition: transform .12s ease, border-color .12s ease, background .12s ease;
    }
    .pager:hover { border-color: rgba(90,123,208,0.6); transform: translateY(-1px); }
    .pager[disabled] { opacity: 0.4; cursor: not-allowed; }
    .meta { color: var(--muted); font-size: 12px; }
    .bottom {
      display: flex; align-items: center; justify-content: space-between;
      padding: 10px 16px; border-top: 1px solid var(--line);
      background: rgba(255,255,255,0.03);
    }
    .page-size select {
      background: var(--panel-2); border: 1px solid var(--line); color: var(--ink);
      border-radius: 6px; padding: 4px 6px; font-size: 12px;
    }
    .badge {
      padding: 2px 6px; border-radius: 999px; font-size: 10px; letter-spacing: 0.05em;
      background: rgba(90,123,208,0.22); color: rgba(214,224,255,0.95); border: 1px solid rgba(90,123,208,0.45);
    }
    @media (prefers-color-scheme: light) {
      .badge {
        background: rgba(58,92,204,0.12); color: #2a4bb3; border-color: rgba(58,92,204,0.28);
      }
    }
  </style>
</head>
<body>
  <div class="wrap" data-table="__DATASET__">
    <div class="card">
      <div class="header">
        <div class="header-left">
          <div class="title">Sonuc Tablosu <span class="badge">Canli</span></div>
          <span class="stat-pill" data-count>Toplam 0 kayit</span>
        </div>
        <label class="search">
          <span class="icon">Ara</span>
          <input type="search" placeholder="isim, sicil no..." />
        </label>
      </div>
      <div class="table-wrap">
        <table>
          <thead></thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="bottom">
        <div class="pagination">
          <button class="pager" data-action="prev">Onceki</button>
          <span class="meta" data-meta></span>
          <button class="pager" data-action="next">Sonraki</button>
        </div>
        <label class="page-size meta">
          Sayfa boyutu
          <select>
            <option value="10">10</option>
            <option value="25" selected>25</option>
            <option value="50">50</option>
          </select>
        </label>
      </div>
      <div class="footer" data-footer></div>
    </div>
  </div>
  <script>
    (function() {
      var root = document.querySelector('[data-table]');
      if (!root) return;
      var payload = {};
      try { payload = JSON.parse(root.getAttribute('data-table') || '{}'); } catch (e) { payload = {}; }
      var headers = payload.headers || [];
      var allRows = payload.rows || [];
      var footer = payload.footer || '';

      var searchInput = root.querySelector('input[type="search"]');
      var thead = root.querySelector('thead');
      var tbody = root.querySelector('tbody');
      var meta = root.querySelector('[data-meta]');
      var footerEl = root.querySelector('[data-footer]');
      var prevBtn = root.querySelector('[data-action="prev"]');
      var nextBtn = root.querySelector('[data-action="next"]');
      var sizeSelect = root.querySelector('select');
      var page = 1;
      var pageSize = parseInt(sizeSelect.value, 10) || 25;
      var filtered = allRows.slice();
      var sortIndex = -1;
      var sortDir = 'asc';

      function renderHeader() {
        if (!thead) return;
        var html = '<tr>' + headers.map(function(h, i) {
          var active = i === sortIndex ? ' class="active"' : '';
          var arrow = i === sortIndex ? (sortDir === 'asc' ? ' \u2191' : ' \u2193') : '';
          return '<th data-col="' + i + '"' + active + '>' + escapeHtml(h) + '<span class="sort">' + arrow + '</span></th>';
        }).join('') + '</tr>';
        thead.innerHTML = html;
        var ths = thead.querySelectorAll('th[data-col]');
        ths.forEach(function(th) {
          th.addEventListener('click', function() {
            var idx = parseInt(th.getAttribute('data-col') || '-1', 10);
            if (idx < 0) return;
            if (sortIndex === idx) {
              sortDir = sortDir === 'asc' ? 'desc' : 'asc';
            } else {
              sortIndex = idx;
              sortDir = 'asc';
            }
            applySort();
            renderHeader();
            renderBody();
            renderMeta();
          });
        });
      }

      function applySort() {
        if (sortIndex < 0) return;
        filtered.sort(function(a, b) {
          var av = a[sortIndex] == null ? '' : String(a[sortIndex]);
          var bv = b[sortIndex] == null ? '' : String(b[sortIndex]);
          var an = parseFloat(av.replace(',', '.'));
          var bn = parseFloat(bv.replace(',', '.'));
          var useNum = !isNaN(an) && !isNaN(bn);
          if (useNum) {
            return sortDir === 'asc' ? an - bn : bn - an;
          }
          var cmp = av.localeCompare(bv, 'tr', { numeric: true, sensitivity: 'base' });
          return sortDir === 'asc' ? cmp : -cmp;
        });
      }

      function renderBody() {
        if (!tbody) return;
        var start = (page - 1) * pageSize;
        var rows = filtered.slice(start, start + pageSize);
        var html = rows.map(function(row) {
          var cells = row.map(function(c) { return '<td>' + escapeHtml(String(c)) + '</td>'; }).join('');
          return '<tr>' + cells + '</tr>';
        }).join('');
        tbody.innerHTML = html || '<tr><td colspan="' + headers.length + '" style="padding:18px;color:var(--muted);">Kayit bulunamadi.</td></tr>';
      }

      function renderMeta() {
        var total = filtered.length;
        var totalPages = Math.max(1, Math.ceil(total / pageSize));
        if (page > totalPages) page = totalPages;
        meta.textContent = 'Sayfa ' + page + ' / ' + totalPages + ' · ' + total + ' kayit';
        var countEl = root.querySelector('[data-count]');
        if (countEl) countEl.textContent = 'Toplam ' + total + ' kayit';
        prevBtn.disabled = page <= 1;
        nextBtn.disabled = page >= totalPages;
      }

      function applySearch() {
        var q = (searchInput.value || '').trim().toLowerCase();
        if (!q) {
          filtered = allRows.slice();
        } else {
          filtered = allRows.filter(function(row) {
            return row.join(' ').toLowerCase().indexOf(q) !== -1;
          });
        }
        applySort();
        page = 1;
        renderBody();
        renderMeta();
      }

      function escapeHtml(text) {
        return text.replace(/[&<>"']/g, function(ch) {
          return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]);
        });
      }

      prevBtn.addEventListener('click', function() { if (page > 1) { page -= 1; renderBody(); renderMeta(); } });
      nextBtn.addEventListener('click', function() { page += 1; renderBody(); renderMeta(); });
      searchInput.addEventListener('input', applySearch);
      sizeSelect.addEventListener('change', function() {
        pageSize = parseInt(sizeSelect.value, 10) || 25;
        page = 1;
        renderBody();
        renderMeta();
      });

      renderHeader();
      renderBody();
      renderMeta();
      if (footerEl) footerEl.textContent = footer;

      var lastH = 0;
      function reportHeight() {
        var h = Math.ceil((root.getBoundingClientRect().height || document.body.scrollHeight) + 6);
        if (Math.abs(h - lastH) < 2) return;
        lastH = h;
        parent.postMessage({ type: 'iframe:height', height: h }, '*');
      }
      window.addEventListener('load', reportHeight);
      window.addEventListener('resize', reportHeight);
      if (window.ResizeObserver) new ResizeObserver(reportHeight).observe(root);
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
            default=110,
            description="Run after upstream response filters.",
        )
        min_rows: int = Field(
            default=5,
            description="Minimum rows required to replace markdown table.",
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

        # Strip the markdown table from assistant text, keep narration.
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
                await __event_emitter__({"type": "replace", "data": {"content": parsed.narration}})
            await __event_emitter__({"type": "embeds", "data": {"embeds": [html]}})

        return updated
