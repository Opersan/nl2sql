"""Durable run/trace store backed by SQLite (aiosqlite).

Provides persistent storage for conversations, messages, runs, run_stages,
clarifications, and run_events.  This is the source of truth for the
Pipeline Live View (read-only) and for auditing pipeline history.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.core.logging import get_logger

logger = get_logger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id   TEXT PRIMARY KEY,
    session_id        TEXT,
    openwebui_chat_id TEXT,
    user_id           TEXT,
    title             TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    latest_status     TEXT
);
CREATE INDEX IF NOT EXISTS idx_conv_openwebui ON conversations(openwebui_chat_id);
CREATE INDEX IF NOT EXISTS idx_conv_updated   ON conversations(updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    message_id           TEXT PRIMARY KEY,
    conversation_id      TEXT NOT NULL,
    role                 TEXT NOT NULL,
    source               TEXT NOT NULL DEFAULT 'pipeline',
    content              TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    openwebui_message_id TEXT,
    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS runs (
    run_id                TEXT PRIMARY KEY,
    conversation_id       TEXT NOT NULL,
    source_message_id     TEXT,
    parent_run_id         TEXT,
    trace_id              TEXT,
    run_type              TEXT NOT NULL DEFAULT 'initial',
    intent_route          TEXT,
    status                TEXT NOT NULL DEFAULT 'running',
    started_at            TEXT NOT NULL,
    finished_at           TEXT,
    final_response_source TEXT,
    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
    FOREIGN KEY (parent_run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_run_conv   ON runs(conversation_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_trace  ON runs(trace_id);
CREATE INDEX IF NOT EXISTS idx_run_parent ON runs(parent_run_id);

CREATE TABLE IF NOT EXISTS run_stages (
    stage_id    TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    stage_name  TEXT NOT NULL,
    stage_group TEXT,
    stage_order INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',
    started_at  TEXT,
    finished_at TEXT,
    elapsed_ms  INTEGER,
    summary     TEXT,
    payload_json TEXT,
    diff_json   TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_stage_run ON run_stages(run_id, stage_order);

CREATE TABLE IF NOT EXISTS clarifications (
    clarification_id     TEXT PRIMARY KEY,
    run_id               TEXT NOT NULL,
    conversation_id      TEXT NOT NULL,
    reason_code          TEXT,
    question_text        TEXT,
    options_json         TEXT,
    selected_option      TEXT,
    selected_label       TEXT,
    delegated_to_system  INTEGER NOT NULL DEFAULT 0,
    resolved_value       TEXT,
    status               TEXT NOT NULL DEFAULT 'pending',
    created_at           TEXT NOT NULL,
    answered_at          TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_clar_run  ON clarifications(run_id);
CREATE INDEX IF NOT EXISTS idx_clar_conv ON clarifications(conversation_id);

CREATE TABLE IF NOT EXISTS run_events (
    event_id          TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    event_ts          TEXT NOT NULL,
    event_payload_json TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_evt_run ON run_events(run_id, event_ts);
"""

# Stage name → group mapping for the pipeline live view.
_STAGE_GROUPS: dict[str, str] = {
    "question": "input",
    "runtime_context": "input",
    "metadata_catalog_load": "preparation",
    "semantic_registry_load": "preparation",
    "policy_guard": "planning",
    "intent_guard": "planning",
    "query_understanding": "planning",
    "schema_retrieval": "planning",
    "document_retrieval": "planning",
    "retrieval_assessment": "planning",
    "prompt_assembly": "planning",
    "planner_llm_request": "planning",
    "planner_llm_response": "planning",
    "planner_parsed_plan": "planning",
    "normalize": "grounding",
    "repair": "grounding",
    "semantic": "grounding",
    "canonicalize": "grounding",
    "filter_column_resolution": "grounding",
    "filter_value_resolution": "grounding",
    "final_plan": "grounding",
    "followup_context_merge": "grounding",
    "pending_clarification_created": "clarification",
    "clarification_reply_received": "clarification",
    "user_selected_candidate": "clarification",
    "user_deferred_to_system": "clarification",
    "pipeline_resumed_after_clarification": "clarification",
    "validation": "execution",
    "compile": "execution",
    "execute": "execution",
    "narrator_prompt": "narration",
    "narrator_llm_response": "narration",
    "narrator_sanitize": "narration",
    "narrator_final_response": "narration",
    "final_verdict": "verdict",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _json_dumps(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"_serialization_error": True})


def _json_loads(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


class RunStore:
    """Async SQLite-backed durable store for pipeline runs and traces."""

    def __init__(self, db_path: str = "data/run_store.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the database and ensure schema exists."""
        import os
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA_SQL)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.commit()
        await self._migrate_schema()
        logger.info("[run-store] initialized at %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _migrate_schema(self) -> None:
        """Add columns missing from earlier schema versions (fail-safe)."""
        assert self._db is not None
        try:
            await self._db.execute(
                "ALTER TABLE conversations ADD COLUMN session_id TEXT"
            )
            await self._db.commit()
        except Exception:
            pass  # Column already exists
        await self._db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id)"
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def resolve_conversation(
        self,
        session_id: str,
        *,
        openwebui_chat_id: str | None = None,
        user_id: str | None = None,
        title: str | None = None,
    ) -> str:
        """Get or create a conversation by session_id.

        Generates an internal canonical conversation_id (UUID) distinct
        from the pipeline session_id.  Maps openwebui_chat_id as a real
        field when provided (e.g. from the ``x-openwebui-chat-id`` header).
        """
        assert self._db is not None
        now = _now_iso()
        row = await self._db.execute_fetchall(
            "SELECT conversation_id FROM conversations WHERE session_id = ?",
            (session_id,),
        )
        if row:
            conv_id = row[0]["conversation_id"]
            await self._db.execute(
                "UPDATE conversations SET updated_at = ?, openwebui_chat_id = COALESCE(?, openwebui_chat_id) WHERE conversation_id = ?",
                (now, openwebui_chat_id, conv_id),
            )
            await self._db.commit()
            return conv_id

        conv_id = f"conv-{_new_id()}"
        await self._db.execute(
            """INSERT INTO conversations
               (conversation_id, session_id, openwebui_chat_id, user_id, title, created_at, updated_at, latest_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active')""",
            (conv_id, session_id, openwebui_chat_id, user_id, title, now, now),
        )
        await self._db.commit()
        return conv_id

    async def update_conversation_status(self, conversation_id: str, status: str) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE conversations SET latest_status = ?, updated_at = ? WHERE conversation_id = ?",
            (status, _now_iso(), conversation_id),
        )
        await self._db.commit()

    async def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation and ALL related data (cascade)."""
        assert self._db is not None
        # Check existence
        rows = await self._db.execute_fetchall(
            "SELECT conversation_id FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        )
        if not rows:
            return False
        # Collect run_ids for this conversation
        run_rows = await self._db.execute_fetchall(
            "SELECT run_id FROM runs WHERE conversation_id = ?",
            (conversation_id,),
        )
        run_ids = [r["run_id"] for r in run_rows]
        # Delete child tables referencing runs
        for rid in run_ids:
            await self._db.execute("DELETE FROM run_stages WHERE run_id = ?", (rid,))
            await self._db.execute("DELETE FROM run_events WHERE run_id = ?", (rid,))
            await self._db.execute("DELETE FROM clarifications WHERE run_id = ?", (rid,))
        # Delete runs
        await self._db.execute("DELETE FROM runs WHERE conversation_id = ?", (conversation_id,))
        # Delete messages
        await self._db.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        # Delete conversation
        await self._db.execute("DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,))
        await self._db.commit()
        logger.info("[run-store] deleted conversation %s (%d runs)", conversation_id, len(run_ids))
        return True

    async def list_conversations(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        assert self._db is not None
        capped_limit = min(limit, 200)
        rows = await self._db.execute_fetchall(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (capped_limit, offset),
        )
        return [dict(r) for r in rows]

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        )
        return dict(rows[0]) if rows else None

    async def search_conversations(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        assert self._db is not None
        pattern = f"%{query}%"
        capped_limit = min(limit, 100)
        rows = await self._db.execute_fetchall(
            """SELECT DISTINCT c.* FROM conversations c
               LEFT JOIN messages m ON m.conversation_id = c.conversation_id
               WHERE c.title LIKE ? OR c.session_id LIKE ? OR c.conversation_id LIKE ? OR m.content LIKE ?
               ORDER BY c.updated_at DESC LIMIT ?""",
            (pattern, pattern, pattern, pattern, capped_limit),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def persist_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        source: str = "pipeline",
        openwebui_message_id: str | None = None,
    ) -> str:
        assert self._db is not None
        msg_id = _new_id()
        await self._db.execute(
            """INSERT INTO messages
               (message_id, conversation_id, role, source, content, created_at, openwebui_message_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, conversation_id, role, source, content, _now_iso(), openwebui_message_id),
        )
        await self._db.commit()
        return msg_id

    async def list_messages(self, conversation_id: str, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        assert self._db is not None
        capped_limit = min(limit, 500)
        rows = await self._db.execute_fetchall(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ? OFFSET ?",
            (conversation_id, capped_limit, offset),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def create_run(
        self,
        conversation_id: str,
        *,
        source_message_id: str | None = None,
        parent_run_id: str | None = None,
        trace_id: str | None = None,
        run_type: str = "initial",
        intent_route: str | None = None,
    ) -> str:
        assert self._db is not None
        run_id = _new_id()
        await self._db.execute(
            """INSERT INTO runs
               (run_id, conversation_id, source_message_id, parent_run_id,
                trace_id, run_type, intent_route, status, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
            (run_id, conversation_id, source_message_id, parent_run_id,
             trace_id, run_type, intent_route, _now_iso()),
        )
        await self._db.commit()
        return run_id

    async def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        final_response_source: str | None = None,
    ) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE runs SET status = ?, finished_at = ?, final_response_source = ? WHERE run_id = ?",
            (status, _now_iso(), final_response_source, run_id),
        )
        await self._db.commit()

    async def list_runs(self, conversation_id: str, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        assert self._db is not None
        capped_limit = min(limit, 200)
        rows = await self._db.execute_fetchall(
            "SELECT * FROM runs WHERE conversation_id = ? ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (conversation_id, capped_limit, offset),
        )
        return [dict(r) for r in rows]

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,),
        )
        return dict(rows[0]) if rows else None

    # ------------------------------------------------------------------
    # Run Stages
    # ------------------------------------------------------------------

    async def persist_stage(
        self,
        run_id: str,
        *,
        stage_name: str,
        stage_order: int,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        elapsed_ms: int | None = None,
        summary: str | None = None,
        payload: Any = None,
        diff: Any = None,
    ) -> str:
        assert self._db is not None
        stage_id = _new_id()
        group = _STAGE_GROUPS.get(stage_name, "other")
        await self._db.execute(
            """INSERT INTO run_stages
               (stage_id, run_id, stage_name, stage_group, stage_order, status,
                started_at, finished_at, elapsed_ms, summary, payload_json, diff_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (stage_id, run_id, stage_name, group, stage_order, status,
             started_at, finished_at, elapsed_ms, summary,
             _json_dumps(payload), _json_dumps(diff)),
        )
        await self._db.commit()
        return stage_id

    async def list_stages(self, run_id: str) -> list[dict[str, Any]]:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT * FROM run_stages WHERE run_id = ? ORDER BY stage_order ASC",
            (run_id,),
        )
        result = []
        for r in rows:
            d = dict(r)
            d["payload"] = _json_loads(d.pop("payload_json", None))
            d["diff"] = _json_loads(d.pop("diff_json", None))
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Clarifications
    # ------------------------------------------------------------------

    async def persist_clarification(
        self,
        run_id: str,
        conversation_id: str,
        *,
        clarification_id: str | None = None,
        reason_code: str | None = None,
        question_text: str | None = None,
        options: Any = None,
        status: str = "pending",
    ) -> str:
        assert self._db is not None
        clar_id = clarification_id or _new_id()
        await self._db.execute(
            """INSERT INTO clarifications
               (clarification_id, run_id, conversation_id, reason_code,
                question_text, options_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (clar_id, run_id, conversation_id, reason_code,
             question_text, _json_dumps(options), status, _now_iso()),
        )
        await self._db.commit()
        return clar_id

    async def resolve_clarification(
        self,
        clarification_id: str,
        *,
        selected_option: str | None = None,
        selected_label: str | None = None,
        delegated_to_system: bool = False,
        resolved_value: str | None = None,
        status: str = "answered",
    ) -> None:
        assert self._db is not None
        await self._db.execute(
            """UPDATE clarifications
               SET selected_option = ?, selected_label = ?,
                   delegated_to_system = ?, resolved_value = ?,
                   status = ?, answered_at = ?
               WHERE clarification_id = ?""",
            (selected_option, selected_label,
             1 if delegated_to_system else 0, resolved_value,
             status, _now_iso(), clarification_id),
        )
        await self._db.commit()

    async def list_clarifications(self, conversation_id: str) -> list[dict[str, Any]]:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT * FROM clarifications WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        )
        result = []
        for r in rows:
            d = dict(r)
            d["options"] = _json_loads(d.pop("options_json", None))
            d["delegated_to_system"] = bool(d.get("delegated_to_system"))
            result.append(d)
        return result

    async def get_clarification(self, clarification_id: str) -> dict[str, Any] | None:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT * FROM clarifications WHERE clarification_id = ?",
            (clarification_id,),
        )
        if not rows:
            return None
        d = dict(rows[0])
        d["options"] = _json_loads(d.pop("options_json", None))
        d["delegated_to_system"] = bool(d.get("delegated_to_system"))
        return d

    async def get_clarifications_for_run(self, run_id: str) -> list[dict[str, Any]]:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT * FROM clarifications WHERE run_id = ? ORDER BY created_at ASC",
            (run_id,),
        )
        result = []
        for r in rows:
            d = dict(r)
            d["options"] = _json_loads(d.pop("options_json", None))
            d["delegated_to_system"] = bool(d.get("delegated_to_system"))
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Run Events
    # ------------------------------------------------------------------

    async def persist_event(
        self,
        run_id: str,
        *,
        event_type: str,
        payload: Any = None,
    ) -> str:
        assert self._db is not None
        event_id = _new_id()
        await self._db.execute(
            """INSERT INTO run_events
               (event_id, run_id, event_type, event_ts, event_payload_json)
               VALUES (?, ?, ?, ?, ?)""",
            (event_id, run_id, event_type, _now_iso(), _json_dumps(payload)),
        )
        await self._db.commit()
        return event_id

    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY event_ts ASC",
            (run_id,),
        )
        result = []
        for r in rows:
            d = dict(r)
            d["payload"] = _json_loads(d.pop("event_payload_json", None))
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Aggregate helpers for viewer
    # ------------------------------------------------------------------

    async def get_run_detail(self, run_id: str) -> dict[str, Any] | None:
        """Full run detail including stages, clarifications, events."""
        run = await self.get_run(run_id)
        if not run:
            return None
        run["stages"] = await self.list_stages(run_id)
        run["clarifications"] = await self.get_clarifications_for_run(run_id)
        run["events"] = await self.list_events(run_id)

        # Find child runs (clarification resumes)
        assert self._db is not None
        children = await self._db.execute_fetchall(
            "SELECT run_id, run_type, status, started_at, finished_at FROM runs WHERE parent_run_id = ? ORDER BY started_at",
            (run_id,),
        )
        run["child_runs"] = [dict(c) for c in children]
        return run

    async def get_run_detail_unified(self, run_id: str) -> dict[str, Any] | None:
        """Unified pipeline view: merge parent + all child runs into one.

        Returns a single run-like dict with stages/events/clarifications
        from the entire chain (root → child1 → child2 …) in order.
        """
        # Find root: walk up parent_run_id chain
        root_id = run_id
        assert self._db is not None
        while True:
            row = await self._db.execute_fetchall(
                "SELECT parent_run_id FROM runs WHERE run_id = ?", (root_id,)
            )
            if not row or not row[0]["parent_run_id"]:
                break
            root_id = row[0]["parent_run_id"]

        root = await self.get_run(root_id)
        if not root:
            return None

        # Collect all run_ids in chain order: root, then children by started_at
        chain_ids = [root_id]
        children = await self._db.execute_fetchall(
            "SELECT run_id FROM runs WHERE parent_run_id = ? ORDER BY started_at",
            (root_id,),
        )
        for c in children:
            chain_ids.append(c["run_id"])

        # Merge stages, events, clarifications from all runs
        all_stages: list[dict[str, Any]] = []
        all_events: list[dict[str, Any]] = []
        all_clarifications: list[dict[str, Any]] = []
        last_status = root.get("status", "running")
        last_finished = root.get("finished_at")

        for rid in chain_ids:
            stages = await self.list_stages(rid)
            for s in stages:
                s["_source_run_id"] = rid
            all_stages.extend(stages)

            events = await self.list_events(rid)
            for e in events:
                e["_source_run_id"] = rid
            all_events.extend(events)

            cl = await self.get_clarifications_for_run(rid)
            all_clarifications.extend(cl)

            r = await self.get_run(rid)
            if r:
                last_status = r.get("status", last_status)
                last_finished = r.get("finished_at") or last_finished

        # Build unified result using root as base
        unified = dict(root)
        unified["stages"] = all_stages
        unified["events"] = all_events
        unified["clarifications"] = all_clarifications
        unified["child_runs"] = []  # Flattened: no separate children
        unified["status"] = last_status
        unified["finished_at"] = last_finished
        unified["run_chain"] = chain_ids
        unified["is_unified"] = True
        return unified

    async def get_conversation_detail(self, conversation_id: str) -> dict[str, Any] | None:
        """Full conversation including messages and run summaries."""
        conv = await self.get_conversation(conversation_id)
        if not conv:
            return None
        conv["messages"] = await self.list_messages(conversation_id)
        runs = await self.list_runs(conversation_id)
        # Include stage count per run for summary
        for run in runs:
            assert self._db is not None
            rows = await self._db.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM run_stages WHERE run_id = ?",
                (run["run_id"],),
            )
            run["stage_count"] = rows[0]["cnt"] if rows else 0
        conv["runs"] = runs
        conv["clarifications"] = await self.list_clarifications(conversation_id)
        return conv
