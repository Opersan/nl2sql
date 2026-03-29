# Open WebUI Setup

This setup keeps Open WebUI as the conversation surface and leaves the NL2SQL backend as the pipeline engine.

## What This Adds

- Open WebUI talks to the existing backend through `/v1/chat/completions`
- clarification state still stays in the backend session engine
- Open WebUI can render clarification prompts as button cards instead of plain assistant chat text
- typed fallback still works: `1`, option label, `sen karar ver`

## 1. Start The Backend

From the repo root:

```powershell
$env:LLM_PROVIDER="mock"
$env:ENABLE_ORACLE_EXECUTOR="false"
$env:PYTHONUTF8="1"
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8010
```

## 2. Start Open WebUI

From the same repo root:

```powershell
$env:HF_HUB_OFFLINE="1"
$env:DATA_DIR="C:\Users\furkan.kiraz\Desktop\nl2sql\results\openwebui_manual\open-webui-data"
$env:WEBUI_AUTH="False"
$env:ENABLE_PERSISTENT_CONFIG="False"
$env:ENABLE_OPENAI_API="True"
$env:OPENAI_API_BASE_URLS="http://127.0.0.1:8010/v1"
$env:OPENAI_API_KEYS="EMPTY"
$env:ENABLE_OLLAMA_API="False"
$env:BYPASS_MODEL_ACCESS_CONTROL="True"
$env:DEFAULT_MODELS="nl2sql"

.\.openwebui-venv\Scripts\open-webui.exe serve --host 127.0.0.1 --port 3010
```

## 3. Install The Clarification Filter

The filter source is stored in:

- [openwebui/functions/nl2sql_clarification_buttons_filter.py](C:/Users/furkan.kiraz/Desktop/nl2sql/openwebui/functions/nl2sql_clarification_buttons_filter.py)

Preferred path: install it through the Open WebUI API with an admin bearer token.

```powershell
.\.venv\Scripts\python .\scripts\install_openwebui_filter.py --base-url http://127.0.0.1:3010 --token <OPENWEBUI_ADMIN_TOKEN>
```

What the installer does:

1. creates or updates the filter
2. enables it
3. marks it global
4. keeps enabled/global state idempotent on repeated runs

Manual fallback:

1. Open Open WebUI
2. Go to `Admin / Functions`
3. Create a new function
4. Paste the contents of [nl2sql_clarification_buttons_filter.py](C:/Users/furkan.kiraz/Desktop/nl2sql/openwebui/functions/nl2sql_clarification_buttons_filter.py)
5. Save it
6. Enable it
7. Toggle it global

After install or update:

1. hard refresh the browser with `Ctrl+F5`
2. if needed, restart the Open WebUI process once

## 4. Expected Behavior

In the same chat:

1. Ask `yonetici unvanli calisanlari goster`
2. Open WebUI should render a button card, not a plain numbered assistant message
3. Click an option button
4. The backend should resume the pending clarification in the same session
5. Final answer should return in the same conversation

Typed fallback should still work:

- `1`
- `Sistem Yoneticisi`
- `sen karar ver`

## 5. Verification

Backend log should still show the same session across ask and resume:

```text
[openwebui] session=owui-conv-... status=clarification clarification_id=...
[openwebui] session=owui-conv-... status=success clarification_id=- message='1'
```

Open WebUI should no longer rely on the plain clarification text block for the primary UX.

## 6. Notes

- The filter is additive and reversible. Disabling or deleting it returns Open WebUI to the existing text fallback.
- The backend NL2SQL planner, grounding, compiler, executor, narrator, and viewer are unchanged.
- The Open WebUI-installed filter is self-contained and does not depend on repo imports.
- [app/openwebui_clarification_ui.py](C:/Users/furkan.kiraz/Desktop/nl2sql/app/openwebui_clarification_ui.py) remains as the repo-side tested helper implementation and source of truth for parsing/rendering logic.
