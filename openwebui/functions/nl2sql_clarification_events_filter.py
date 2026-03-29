"""
title: NL2SQL Clarification Events
author: Codex
version: 0.1
required_open_webui_version: 0.6.0
description: >
  Uses __event_call__ + execute to show a native full-page modal overlay
  instead of a sandboxed iframe embed. Runs directly in the main page
  context — no iframe, no same-origin flag needed.
  Falls back silently to numbered text if the WebSocket is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping, Optional

from pydantic import BaseModel, Field


_OPTION_LINE_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")


# ---------------------------------------------------------------------------
# Domain types (same contract as the Rich-UI filter)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClarificationChoice:
    index: int
    label: str
    value: str


@dataclass(frozen=True)
class ClarificationView:
    question: str
    choices: tuple[ClarificationChoice, ...]
    clarification_id: str | None = None
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Parsing helpers (identical to the Rich-UI filter)
# ---------------------------------------------------------------------------

def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _first_choice_message(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = _as_mapping(choices[0])
    if first is None:
        return None
    return _as_mapping(first.get("message"))


def _message_metadata(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    message = _first_choice_message(body)
    if message is None:
        return None
    return _as_mapping(message.get("metadata"))


def _top_level_metadata(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return _as_mapping(body.get("metadata"))


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
    message = _first_choice_message(body)
    if message is not None and isinstance(message.get("content"), str):
        return str(message["content"])
    if message is not None:
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


def _payload_choice_value(label: str) -> str:
    normalized = label.strip().lower()
    if normalized == "sen karar ver":
        return "sen karar ver"
    return label.strip()


def _parse_clarification_payload(
    payload: Mapping[str, Any],
    *,
    session_id: str | None,
) -> ClarificationView | None:
    raw_message = payload.get("message")
    raw_options = payload.get("options")
    if not isinstance(raw_message, str) or not isinstance(raw_options, list):
        return None
    choices: list[ClarificationChoice] = []
    for index, option in enumerate(raw_options, start=1):
        option_map = _as_mapping(option)
        if option_map is None:
            continue
        label = option_map.get("label") or option_map.get("value")
        if not isinstance(label, str):
            continue
        option_index = option_map.get("index")
        if not isinstance(option_index, int):
            option_index = index
        cleaned = label.strip()
        if not cleaned:
            continue
        choices.append(
            ClarificationChoice(
                index=option_index,
                label=cleaned,
                value=_payload_choice_value(cleaned),
            )
        )
    if not choices:
        return None
    has_decide = any(choice.value.lower() == "sen karar ver" for choice in choices)
    if not has_decide:
        choices.append(
            ClarificationChoice(
                index=len(choices) + 1,
                label="Sen karar ver",
                value="sen karar ver",
            )
        )
    return ClarificationView(
        question=raw_message.strip(),
        choices=tuple(choices),
        clarification_id=str(payload.get("clarification_id") or "").strip() or None,
        session_id=session_id,
    )


def _parse_clarification_content(
    content: str,
    *,
    clarification_id: str | None,
    session_id: str | None,
) -> ClarificationView | None:
    lines = [line.strip() for line in content.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    question = lines[0]
    choices: list[ClarificationChoice] = []
    for line in lines[1:]:
        match = _OPTION_LINE_RE.match(line)
        if not match:
            continue
        index = int(match.group(1))
        label = match.group(2).strip()
        if not label:
            continue
        value = "sen karar ver" if label.lower() == "sen karar ver" else str(index)
        choices.append(ClarificationChoice(index=index, label=label, value=value))
    if not choices:
        return None
    return ClarificationView(
        question=question,
        choices=tuple(choices),
        clarification_id=clarification_id,
        session_id=session_id,
    )


def _looks_like_clarification_content(content: str) -> bool:
    parsed = _parse_clarification_content(
        content,
        clarification_id=None,
        session_id=None,
    )
    if parsed is None or len(parsed.choices) < 2:
        return False
    lowered = content.lower()
    if "yanit olarak" in lowered or "sen karar ver" in lowered:
        return True
    question = parsed.question.lower()
    return question.endswith("?") or "hangi" in question


def extract_clarification_view(body: Mapping[str, Any]) -> ClarificationView | None:
    session_id = None
    if isinstance(body.get("session_id"), str):
        session_id = str(body["session_id"]).strip() or None
    metadata = _message_metadata(body) or _top_level_metadata(body)
    metadata_status = None
    clarification_id = None
    if metadata is not None:
        metadata_status = metadata.get("status")
        raw_clarification_id = metadata.get("clarification_id")
        if isinstance(raw_clarification_id, str):
            clarification_id = raw_clarification_id.strip() or None
        raw_session_id = metadata.get("session_id")
        if isinstance(raw_session_id, str):
            session_id = raw_session_id.strip() or session_id
    payload = _as_mapping(body.get("clarification_payload"))
    if payload is not None:
        extracted = _parse_clarification_payload(payload, session_id=session_id)
        if extracted is not None:
            return extracted
    content = _content_from_body(body)
    if (
        metadata_status != "clarification"
        and body.get("status") != "clarification"
        and not _looks_like_clarification_content(content)
    ):
        return None
    return _parse_clarification_content(
        content,
        clarification_id=clarification_id,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# JS modal template
# Placeholders: __CHOICES__ (JSON array) and __QUESTION__ (JSON string)
# ---------------------------------------------------------------------------

_MODAL_JS = """\
var _C = __CHOICES__;
var _Q = __QUESTION__;

if (document.getElementById('nlq-clar-ov')) return null;

if (!document.getElementById('nlq-clar-anim')) {
  var animEl = document.createElement('style');
  animEl.id = 'nlq-clar-anim';
  animEl.textContent = '@keyframes nlqIn{from{opacity:0;transform:translateY(10px) scale(.97)}to{opacity:1;transform:none}}';
  document.head.appendChild(animEl);
}

function _mkEl(tag, css, txt) {
  var e = document.createElement(tag);
  if (css) e.style.cssText = css;
  if (txt != null) e.textContent = txt;
  return e;
}

function _mkSvg() {
  var s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 16 16');
  s.setAttribute('width', '10');
  s.setAttribute('height', '10');
  s.setAttribute('fill', '#6366f1');
  var p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  p.setAttribute('d', 'M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm.75 10.5h-1.5v-5h1.5v5Zm0-6.5h-1.5V3.5h1.5V5Z');
  s.appendChild(p);
  return s;
}

function _submitToChat(text) {
  var ta = document.getElementById('chat-input')
    || document.querySelector('[contenteditable="true"][id]')
    || document.querySelector('[contenteditable="true"]')
    || document.querySelector('textarea');
  if (!ta) return;
  ta.focus();
  if (ta.tagName === 'TEXTAREA') {
    var ns = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    if (ns) ns.call(ta, text); else ta.value = text;
    ta.dispatchEvent(new Event('input', {bubbles: true}));
  } else {
    document.execCommand('selectAll', false, null);
    document.execCommand('insertText', false, text);
    if (!ta.textContent.trim()) {
      ta.textContent = text;
      ta.dispatchEvent(new InputEvent('input', {bubbles: true, data: text, inputType: 'insertText'}));
    }
  }
  setTimeout(function() {
    var f = ta.closest ? ta.closest('form') : null;
    var btn = (f && f.querySelector('[type="submit"]'))
      || document.querySelector('[aria-label*="end" i][type="button"]')
      || document.querySelector('[data-testid*="send"]')
      || document.querySelector('button[type="submit"]');
    if (btn && !btn.disabled) { btn.click(); return; }
    ta.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true, cancelable: true}));
  }, 60);
}

return new Promise(function(resolve) {
  var ov = _mkEl('div', 'position:fixed;inset:0;background:rgba(0,0,0,.60);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;z-index:9999;padding:16px;font-family:Inter,"Segoe UI",ui-sans-serif,system-ui,sans-serif;');
  ov.id = 'nlq-clar-ov';

  var card = _mkEl('div', 'background:hsl(240,10%,9%);border:1px solid rgba(99,102,241,.22);border-radius:14px;padding:20px;max-width:440px;width:100%;box-shadow:0 24px 64px rgba(0,0,0,.55);animation:nlqIn .18s ease;');

  // --- header ---
  var hdr = _mkEl('div', 'display:flex;align-items:center;gap:8px;margin-bottom:12px;');
  var iconWrap = _mkEl('div', 'flex-shrink:0;width:18px;height:18px;background:rgba(99,102,241,.14);border:1px solid rgba(99,102,241,.30);border-radius:5px;display:flex;align-items:center;justify-content:center;');
  iconWrap.appendChild(_mkSvg());
  hdr.appendChild(iconWrap);
  hdr.appendChild(_mkEl('span', 'font-size:11px;font-weight:600;letter-spacing:.055em;text-transform:uppercase;color:#6366f1;opacity:.9;', 'Netle\u015ftirme'));

  // --- question ---
  var q = _mkEl('p', 'margin:0 0 14px;font-size:15px;font-weight:650;line-height:1.45;color:rgba(255,255,255,.95);', _Q);

  // --- options ---
  var opts = _mkEl('div', 'display:flex;flex-direction:column;gap:6px;');
  _C.forEach(function(ch) {
    var isD = (ch.d === true);
    var bgBase = isD ? 'transparent' : 'rgba(255,255,255,.03)';
    var bdBase = isD ? '1px dashed rgba(255,255,255,.12)' : '1px solid rgba(255,255,255,.08)';
    var btn = _mkEl('button', 'display:flex;align-items:center;gap:10px;width:100%;padding:9px 12px;background:' + bgBase + ';border:' + bdBase + ';border-radius:10px;color:rgba(255,255,255,.95);cursor:pointer;text-align:left;transition:background .13s,border-color .13s,transform .10s,opacity .15s;appearance:none;-webkit-appearance:none;font-family:inherit;font-size:inherit;');
    btn.type = 'button';

    var badge = _mkEl('span',
      'flex-shrink:0;width:24px;height:24px;border-radius:6px;background:' + (isD ? 'rgba(255,255,255,.05)' : 'rgba(99,102,241,.14)') + ';color:' + (isD ? 'rgba(255,255,255,.44)' : '#a5b4fc') + ';font-size:' + (isD ? '10' : '11') + 'px;font-weight:700;display:flex;align-items:center;justify-content:center;',
      isD ? '\u2736' : String(ch.i)
    );
    var lbl = _mkEl('span', 'flex:1;font-size:14px;font-weight:500;', ch.l);
    var arr = _mkEl('span', 'color:rgba(255,255,255,.44);font-size:17px;line-height:1;opacity:0;transition:opacity .13s,transform .13s;', '\u203a');
    btn.appendChild(badge);
    btn.appendChild(lbl);
    btn.appendChild(arr);

    btn.addEventListener('mouseover', function() {
      btn.style.background = 'rgba(99,102,241,.09)';
      btn.style.borderColor = 'rgba(99,102,241,.50)';
      btn.style.transform = 'translateX(3px)';
      arr.style.opacity = '1';
      arr.style.transform = 'translateX(2px)';
    });
    btn.addEventListener('mouseout', function() {
      btn.style.background = bgBase;
      btn.style.border = bdBase;
      btn.style.transform = '';
      arr.style.opacity = '0';
      arr.style.transform = '';
    });
    btn.addEventListener('click', function() {
      opts.querySelectorAll('button').forEach(function(b) {
        b.style.opacity = '.35';
        b.style.pointerEvents = 'none';
      });
      btn.style.opacity = '1';
      btn.style.borderColor = 'rgba(99,102,241,.55)';
      setTimeout(function() {
        ov.remove();
        _submitToChat(ch.v);
        resolve(ch.v);
      }, 110);
    });
    opts.appendChild(btn);
  });

  // --- footer hint ---
  var foot = _mkEl('p', 'margin:12px 0 0;padding-top:10px;border-top:1px solid rgba(255,255,255,.08);font-size:11px;color:rgba(255,255,255,.44);');
  var codeStyle = 'background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.1);border-radius:4px;padding:0 4px;font-size:10.5px;';
  foot.appendChild(document.createTextNode('Klavyeyle de yan\u0131t verebilirsiniz: '));
  foot.appendChild(_mkEl('code', codeStyle, '1'));
  foot.appendChild(document.createTextNode(' \u00b7 se\u00e7enek ad\u0131 \u00b7 '));
  foot.appendChild(_mkEl('code', codeStyle, 'sen karar ver'));

  card.appendChild(hdr);
  card.appendChild(q);
  card.appendChild(opts);
  card.appendChild(foot);
  ov.appendChild(card);
  document.body.appendChild(ov);

  // close on backdrop click
  ov.addEventListener('click', function(e) {
    if (e.target === ov) { ov.remove(); resolve(null); }
  });

  // close on Escape
  function onKey(e) {
    if (e.key === 'Escape') {
      document.removeEventListener('keydown', onKey);
      ov.remove();
      resolve(null);
    }
  }
  document.addEventListener('keydown', onKey);
});
"""


def _build_modal_js(view: ClarificationView) -> str:
    """Inject the question and choices JSON into the static JS template."""
    choices_json = json.dumps(
        [
            {
                "i": c.index,
                "l": c.label,
                "v": c.value,
                "d": c.value.lower() == "sen karar ver",
            }
            for c in view.choices
        ],
        ensure_ascii=False,
    )
    question_json = json.dumps(view.question, ensure_ascii=False)
    return (
        _MODAL_JS
        .replace("__CHOICES__", choices_json)
        .replace("__QUESTION__", question_json)
    )


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=100,
            description="Run after upstream response filters so clarification payload is final.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def outlet(
        self,
        body: dict,
        __event_emitter__: Optional[callable] = None,
        __event_call__: Optional[callable] = None,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
    ) -> dict:
        # Requires a live WebSocket; degrade gracefully if unavailable
        if __event_call__ is None:
            return body

        clarification = extract_clarification_view(body)
        if clarification is None:
            return body

        if __event_emitter__ is not None:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Se\u00e7enekler y\u00fckleniyor\u2026", "done": False, "hidden": True},
            })

        js = _build_modal_js(clarification)

        chosen = None
        try:
            chosen = await __event_call__({"type": "execute", "data": {"code": js}})
        except Exception:
            pass  # WebSocket timeout or tab closed — fall back to numbered text

        if __event_emitter__ is not None:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Netle\u015ftirme bekleniyor", "done": True, "hidden": True},
            })

        if chosen and __event_emitter__ is not None:
            chosen_label = next(
                (c.label for c in clarification.choices if c.value == chosen),
                str(chosen),
            )
            await __event_emitter__({
                "type": "replace",
                "data": {"content": f"\u2713 **{clarification.question}**\n\n\u2192 {chosen_label}"},
            })

        return body
