"""Open WebUI clarification embed helpers.

This module keeps the Open WebUI integration additive: the backend still
returns the existing NL2SQL clarification contract, while Open WebUI can
optionally render that contract as a structured button surface.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from html import escape
import json
import re
from typing import Any, Mapping


_OPTION_LINE_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")


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
    """Extract a clarification contract from Open WebUI-visible response shapes."""

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


def _resize_script() -> str:
    return """
let lastReportedHeight = 0;
const reportHeight = () => {
  const card = document.querySelector(".card");
  const nextHeight = Math.ceil((card?.getBoundingClientRect().height ?? document.body.scrollHeight) + 4);
  if (Math.abs(nextHeight - lastReportedHeight) < 2) return;
  lastReportedHeight = nextHeight;
  window.parent.postMessage({ type: "iframe:height", height: nextHeight }, "*");
};
const queueReport = () => window.requestAnimationFrame(reportHeight);
window.addEventListener("load", queueReport);
window.addEventListener("resize", queueReport);
if (window.ResizeObserver) {
  const observer = new ResizeObserver(queueReport);
  const card = document.querySelector(".card");
  if (card) observer.observe(card);
}
"""


def build_clarification_embed_html(view: ClarificationView) -> str:
    """Render a clarification surface styled to match Open WebUI's native dark UI."""

    option_rows = []
    for choice in view.choices:
        is_decide = choice.value.lower() == "sen karar ver"
        row_cls = ' class="option decide"' if is_decide else ' class="option"'
        option_rows.append(
            f'<button{row_cls} type="button" '
            f'data-prompt="{escape(choice.value, quote=True)}" '
            f'aria-label="{escape(choice.label, quote=True)}">'
            f'<span class="num">{choice.index}.</span>'
            f'<span class="opt-label">{escape(choice.label)}</span>'
            f'<span class="opt-arrow" aria-hidden="true">&#x2192;</span>'
            f'</button>'
        )

    dataset = {
        "clarification_id": view.clarification_id,
        "session_id": view.session_id,
        "choices": [
            {"index": c.index, "label": c.label, "value": c.value}
            for c in view.choices
        ],
    }

    joined = "".join(option_rows)
    question_html = escape(view.question)
    dataset_attr = escape(
        __import__("json").dumps(dataset), quote=True
    )

    return f"""<!doctype html>
<html lang="tr"><head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    html, body {{
      margin: 0; padding: 0;
      background: transparent;
      color: rgba(255,255,255,0.87);
      font-family: Inter, "Segoe UI", ui-sans-serif, system-ui, sans-serif;
      font-size: 14px; line-height: 1.5;
    }}
    .wrap {{ padding: 10px 0 6px; }}
    .label {{
      font-size: 11px; font-weight: 500;
      letter-spacing: 0.07em; text-transform: uppercase;
      color: rgba(255,255,255,0.32);
      margin: 0 0 8px;
    }}
    .question {{
      font-size: 14px; font-weight: 600; line-height: 1.45;
      color: rgba(255,255,255,0.90);
      margin: 0 0 10px;
    }}
    .options {{ display: flex; flex-direction: column; gap: 3px; }}
    .option {{
      display: flex; align-items: center; gap: 10px;
      width: 100%; padding: 7px 10px;
      background: rgba(255,255,255,0.04);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 8px;
      color: rgba(255,255,255,0.87);
      cursor: pointer; text-align: left;
      transition: background .10s, border-color .10s, opacity .15s;
      -webkit-appearance: none; appearance: none;
      font-family: inherit; font-size: 14px;
    }}
    .option:hover {{
      background: rgba(255,255,255,0.07);
      border-color: rgba(255,255,255,0.16);
    }}
    .option:active {{ opacity: .70; }}
    .option.fired {{ opacity: .35; pointer-events: none; }}
    .option.decide {{
      background: transparent;
      border-style: dashed;
      border-color: rgba(255,255,255,0.10);
    }}
    .option.decide:hover {{
      background: rgba(255,255,255,0.04);
      border-color: rgba(255,255,255,0.16);
    }}
    .num {{
      flex-shrink: 0; min-width: 20px;
      font-size: 12px; font-weight: 600; text-align: center;
      color: rgba(255,255,255,0.28);
    }}
    .opt-label {{ flex: 1; font-size: 14px; font-weight: 400; }}
    .opt-arrow {{
      color: rgba(255,255,255,0.28); font-size: 13px; line-height: 1;
      opacity: 0; transition: opacity .10s;
    }}
    .option:hover .opt-arrow {{ opacity: 1; }}
    .foot {{
      margin: 10px 0 0; padding-top: 8px;
      border-top: 1px solid rgba(255,255,255,0.07);
      font-size: 11px; color: rgba(255,255,255,0.32);
    }}
    code {{
      background: rgba(255,255,255,0.07);
      border-radius: 4px; padding: 1px 4px; font-size: 10.5px;
    }}
  </style>
</head>
<body>
  <div class="wrap" data-clarification="{dataset_attr}">
    <p class="label">Netle&#351;tirme</p>
    <p class="question">{question_html}</p>
    <div class="options">
      {joined}
    </div>
    <p class="foot">
      Klavyeyle de yan&#305;t verebilirsiniz: <code>1</code>, se&#231;enek ad&#305; veya <code>sen&nbsp;karar&nbsp;ver</code>
    </p>
  </div>
  <script>
    (function () {{
      var lastH = 0;
      var report = function () {{
        var wrap = document.querySelector('.wrap');
        var h = Math.ceil((wrap ? wrap.getBoundingClientRect().height : document.body.scrollHeight) + 6);
        if (Math.abs(h - lastH) < 2) return;
        lastH = h;
        parent.postMessage({{ type: 'iframe:height', height: h }}, '*');
      }};
      var rAF = function () {{ return requestAnimationFrame(report); }};
      window.addEventListener('load', rAF);
      window.addEventListener('resize', rAF);
      if (window.ResizeObserver) {{
        new ResizeObserver(rAF).observe(document.querySelector('.wrap') || document.body);
      }}
      var submit = function (text) {{
        parent.postMessage({{ type: 'input:prompt', text: text }}, '*');
        setTimeout(function () {{
          parent.postMessage({{ type: 'action:submit', text: '' }}, '*');
        }}, 60);
      }};
      var buttons = document.querySelectorAll('.option');
      buttons.forEach(function (btn) {{
        btn.addEventListener('click', function () {{
          buttons.forEach(function (b) {{
            if (b !== btn) b.classList.add('fired');
          }});
          btn.classList.add('fired');
          submit(btn.dataset.prompt);
        }});
      }});
    }})();
  </script>
</body>
</html>
"""


def apply_embed_to_body(body: Mapping[str, Any], html: str) -> dict[str, Any]:
    """Return a body copy whose assistant clarification text is replaced by the embed."""

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

    choices = updated.get("choices")
    if isinstance(choices, list) and choices:
        first = _as_mapping(choices[0])
        if first is not None:
            message = first.get("message")
            if isinstance(message, dict):
                message["content"] = ""
                _clear_output_text(message.get("output"))

    messages = updated.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            if message.get("role") != "assistant":
                continue
            message["content"] = ""
            _clear_output_text(message.get("output"))
            embeds = message.setdefault("embeds", [])
            if isinstance(embeds, list) and html not in embeds:
                embeds.append(html)
            break

    if isinstance(updated.get("content"), str):
        updated["content"] = ""
    _clear_output_text(updated.get("output"))

    embeds = updated.setdefault("embeds", [])
    if isinstance(embeds, list) and html not in embeds:
        embeds.append(html)

    return updated
