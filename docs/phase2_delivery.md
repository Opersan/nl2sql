# Phase 2 — Persistent Trace Store & Pipeline Live View

## 1. Architecture Decisions

- **SQLite via aiosqlite** — zero-infrastructure, async, no ORM (raw SQL).
- **DB file**: `data/run_store.db`, auto-created on startup via lifespan handler; WAL journal mode, NORMAL synchronous.
- **Canonical conversation_id**: internal UUID (`conv-{hex}`), distinct from pipeline `session_id` and Open WebUI `chat_id`.
- **Persistence is fail-open**: every persist call is individually wrapped in try/except; failures produce structured warnings via logger + trace events — never block the pipeline.
- **Pipeline Live View** is a read-only HTML inspector at `/pipeline/viewer`. It has NO chat input, NO send button, NO execution capability.
- **Viewer API** prefix: `/viewer/` — all endpoints are GET-only with pagination/limit caps.

## 2. Storage Model (6 Tables)

| Table | Purpose | Key Fields |
|---|---|---|
| `conversations` | Stable conversation identity | `conversation_id` (PK, UUID), `session_id` (unique), `openwebui_chat_id`, `user_id`, `title` |
| `messages` | User/assistant turns | `message_id`, `conversation_id` (FK), `role`, `source`, `content` |
| `runs` | Pipeline execution attempts | `run_id` (PK), `conversation_id` (FK), `parent_run_id` (FK, self-ref), `trace_id`, `run_type`, `status` |
| `run_stages` | Stage-level trace with group | `stage_id`, `run_id` (FK), `stage_name`, `stage_group`, `stage_order`, `payload_json`, `diff_json` |
| `clarifications` | Full clarification lifecycle | `clarification_id`, `run_id` (FK), `conversation_id` (FK), `status`, `options_json` |
| `run_events` | Optional event log per run | `event_id`, `run_id` (FK), `event_type`, `event_payload_json` |

### Identity Model

```
session_id (pipeline)  ──┐
                         ├──→  conversations.session_id (unique)
openwebui_chat_id ───────┤     conversations.conversation_id  = conv-{uuid}  ← canonical
                         └──→  conversations.openwebui_chat_id
```

- `session_id` = pipeline session key (from `SessionService`, Open WebUI header resolution, or `/chat` body).
- `conversation_id` = internal canonical UUID; used in all FK relationships.
- `openwebui_chat_id` = Open WebUI's own chat ID; extracted from `x-openwebui-chat-id` header and stored/updated on every request.

## 3. Data Flow

### Normal Message
```
POST /v1/chat/completions
  → resolve_conversation(session_id, openwebui_chat_id=header)
  → persist user message
  → create run (type=initial)
  → pipeline executes (planner → orchestrator → narrator)
  → _finalize_run:
      → persist assistant message
      → persist all stage events from TraceCollector.collected_events
      → persist clarification record (if applicable)
      → finish run (status=success|failed|clarification)
      → update conversation status
      → if warnings: emit persistence_warning trace event + persist as run_event
```

### Clarification Resume
```
POST /v1/chat/completions (with clarification reply)
  → resolve_conversation(session_id) — returns existing canonical ID
  → persist user reply as message
  → find parent run (status=clarification)
  → create child run (type=clarification_resume, parent_run_id=parent)
  → resolve clarification record (answered/delegated)
  → resume pipeline
  → _finalize_run (same as above)
```

## 4. Persistence Reliability

Previous behavior: all persistence in a single try/except, failure logged as `warning` and silently swallowed.

New behavior (this patch):
- Each persistence step (`persist_message`, `persist_stage[N]`, `persist_clarification`, `finish_run`, `update_conversation_status`) is wrapped individually.
- One failure does not skip subsequent steps.
- All warnings are:
  1. Logged at `WARNING` level with specific step name.
  2. Emitted as `persistence_warning` trace event (visible in live view SSE stream).
  3. Persisted as `run_event` with `event_type=persistence_warning` (if the run_store is still reachable).
- Init-phase failures (resolve_conversation / persist_message / create_run) also emit `persistence_warning` trace events.

## 5. Pagination & Limits

| Endpoint | Default | Max | Offset |
|---|---|---|---|
| `GET /viewer/conversations` | 50 | 200 | ✅ |
| `GET /viewer/conversations/search` | 20 | 100 | — |
| `GET /viewer/conversations/{id}/messages` | 500 | 500 | ✅ |
| `GET /viewer/conversations/{id}/runs` | 100 | 200 | ✅ |

RunStore methods also enforce caps internally (defense in depth) regardless of caller.

## 6. Viewer Behavior

3-panel read-only layout:
- **LEFT**: conversation search, conversation list, message list, run list
- **CENTER**: run header, lineage bar, clarification cards, stage timeline grouped by pipeline phase
- **RIGHT**: inspector panel (summary, SQL, payload, diff, full JSON)

What the viewer does NOT do:
- No chat input or send button
- No quick prompts
- No clarification answer controls
- No calls to `/chat`, `/chat/clarify`, `/chat/trace`
- No pipeline execution of any kind

## 7. Changed Files

| File | Change |
|---|---|
| `app/providers/run_store.py` | Added `session_id` column + migration, canonical UUID generation, `openwebui_chat_id` real mapping, COALESCE update, limit caps on all list methods, session_id in search |
| `app/services/orchestrator.py` | Added `openwebui_chat_id` to `handle_message` and `_handle_clarification_resume`, pass to `resolve_conversation`, granular try/except in `_finalize_run` with warning emission, removed silent catch from `_persist_stage_from_event` |
| `app/api/routes_chat.py` | Extract `x-openwebui-chat-id` header in OAI endpoint, pass to `handle_message` |
| `app/api/routes_viewer.py` | Added `limit`/`offset` query params to messages and runs endpoints |
| `app/tests/test_run_store.py` | All tests updated for canonical conv_id, added `test_openwebui_chat_id_mapping` |
| `app/tests/test_viewer_api.py` | All tests updated for canonical conv_id + session_id assertions |

## 8. Verification

```
pytest app/tests/test_run_store.py    — 10 passed
pytest app/tests/test_viewer_api.py   —  4 passed
0 lint/type errors across all changed files
```

## 9. What Was NOT Done

- No schema migration framework (still `CREATE IF NOT EXISTS` + `ALTER TABLE` fail-safe)
- No authentication on viewer API
- No backfill of old conversations (orphaned rows with NULL session_id are expected)
- No full repo test suite run
- No Oracle integration tests
- No pipeline redesign

## 10. Risks & Follow-Ups

- **Auth**: Viewer API has no authentication — add before external exposure.
- **Orphaned rows**: Existing conversations pre-patch have `session_id = NULL` and won't be resolved. Migration script can backfill `session_id = conversation_id` for these if needed.
- **WAL cleanup**: May need periodic `PRAGMA wal_checkpoint` in long-running production.
- **Concurrent writes**: aiosqlite serializes; acceptable at current scale.
