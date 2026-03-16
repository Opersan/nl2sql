"""Oracle executor — production-ready adapter for read-only query execution.

This module provides the ``OracleExecutor`` class that connects to an Oracle
database via ``oracledb`` (the official Python driver, successor to cx_Oracle).

Credentials are loaded exclusively from environment variables / .env file
via ``Settings``.  **No credentials are stored in source code.**

Oracle compatibility
====================
* **ROWNUM pagination only** — the SQL compiler already produces
  ``WHERE ROWNUM <= :pN`` subquery wrapping.
* **Named bind parameters** — ``:p1``, ``:p2``, etc. passed directly to the
  driver; values are never interpolated into the SQL string.
* **Read-only enforcement** — every query passes through ``SQLGuard`` before
  execution.

Security contracts
==================
* SQL is logged at DEBUG level; bind-parameter *values* are never logged.
* ``SQLGuard`` rejects any non-SELECT / multi-statement input.
* ``settings.max_row_limit`` caps every ``fetchmany`` call.
* ``asyncio.wait_for`` enforces ``settings.oracle_timeout`` per query.

Configuration (via environment / .env)
========================================
* ``ORACLE_DSN``
* ``ORACLE_USER``
* ``ORACLE_PASSWORD``
* ``ORACLE_TIMEOUT``   (seconds, default 30)
* ``ENABLE_ORACLE_EXECUTOR`` (must be true to init pool)
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.config import settings
from app.core.exceptions import ExecutionError
from app.core.logging import get_logger
from app.domain.execution_models import (
    CompiledQuery,
    ExecutionResult,
    ExecutionStatus,
)
from app.providers.executor.base import ExecutorProvider
from app.providers.executor.sql_guard import SQLGuard

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Oracle error classifier
# ---------------------------------------------------------------------------

def _classify_oracle_error(detail: str) -> str:
    """Map an Oracle error message to a short diagnostic category.

    Used to surface actionable sub-types in eval reports without exposing
    raw ORA codes to end users.
    """
    d = detail.upper()
    if any(code in d for code in ("ORA-00900", "ORA-00907", "ORA-00911", "ORA-01756", "ORA-00904")):
        # ORA-00904 = invalid identifier (bad column name)
        return "oracle_syntax_error"
    if "ORA-00942" in d or "ORA-01031" in d:
        return "permission_error"
    if any(code in d for code in ("ORA-01858", "ORA-01861", "ORA-01830", "ORA-01839")):
        # Date format / conversion errors — most common after date-bind hardening
        return "invalid_date_value"
    if "ORA-01403" in d:
        return "no_data_found"
    if any(code in d for code in ("ORA-12541", "ORA-12154", "ORA-12170", "DPY-")):
        return "connection_error"
    if "timeout" in detail.lower():
        return "timeout_error"
    return "unknown_execution_error"


class OracleExecutor(ExecutorProvider):
    """Oracle database executor.

    All queries are validated by ``SQLGuard`` before execution.
    Row results are capped at ``settings.max_row_limit``.
    Credentials are read from ``Settings``; no defaults are hard-coded.
    """

    def __init__(
        self,
        *,
        dsn: str | None = None,
        user: str | None = None,
        password: str | None = None,
        timeout: int | None = None,
    ) -> None:
        # Credentials come from settings (environment / .env).
        # Explicit constructor args override settings only for testing.
        self._dsn = dsn if dsn is not None else settings.oracle_dsn
        self._user = user if user is not None else settings.oracle_user
        self._password = password if password is not None else settings.oracle_password
        self._timeout = timeout if timeout is not None else settings.oracle_timeout
        self._guard = SQLGuard()
        self._pool: Any = None          # oracledb.ConnectionPool (sync, thick mode)
        self._thread_pool = ThreadPoolExecutor(max_workers=5)

    # ------------------------------------------------------------------
    # Pool lifecycle
    # ------------------------------------------------------------------

    async def init_pool(self, *, thick_mode_lib_dir: str | None = None) -> None:
        """Create the Oracle connection pool.

        Must be called once before ``execute`` is used in production.
        Raises ``ExecutionError`` if credentials are missing.

        python-oracledb thick mode does NOT support asyncio; queries are
        executed in a ``ThreadPoolExecutor`` and awaited via
        ``loop.run_in_executor``.

        Parameters
        ----------
        thick_mode_lib_dir:
            Path to Oracle Instant Client directory.  When provided (or when
            ``ORACLE_CLIENT_LIB_DIR`` env var is set), thick mode is activated
            before pool creation.  Thick mode is required for Oracle databases
            that use 11g-era password verifiers (DPY-3015 in thin mode).
            ``init_oracle_client`` must be called AT MOST ONCE per process.
        """
        if not self._dsn or not self._user or not self._password:
            raise ExecutionError(
                "Oracle executor not configured.",
                detail=(
                    "ORACLE_DSN, ORACLE_USER and ORACLE_PASSWORD must be set "
                    "in environment or .env before initialising the pool."
                ),
            )
        try:
            import oracledb  # deferred — optional dependency
        except ImportError as exc:
            raise ExecutionError(
                "oracledb package not installed.",
                detail="Install it with: pip install oracledb",
            ) from exc

        # Activate thick mode (required for Oracle 11g password verifiers).
        # init_oracle_client must be called AT MOST ONCE per process; a second
        # call (even after a failure) corrupts internal state → DPY-2053.
        # NOTE: python-oracledb thick mode does NOT support asyncio; all DB
        # operations are executed synchronously inside ThreadPoolExecutor.
        lib_dir = thick_mode_lib_dir or os.environ.get("ORACLE_CLIENT_LIB_DIR")
        try:
            if lib_dir:
                oracledb.init_oracle_client(lib_dir=lib_dir)
                logger.info("Oracle thick mode enabled (lib_dir=%s).", lib_dir)
            else:
                oracledb.init_oracle_client()
                logger.info("Oracle thick mode enabled (PATH-based client).")
        except Exception as exc:  # already initialised or client not found
            logger.debug("Oracle thick mode init skipped: %s", exc)

        # Synchronous connection pool — required for thick mode.
        self._pool = oracledb.create_pool(
            dsn=self._dsn,
            user=self._user,
            password=self._password,
            min=1,
            max=5,
            increment=1,
        )
        logger.info("Oracle connection pool initialised (min=1, max=5, dsn=***masked***).")

    async def execute(self, compiled_query: CompiledQuery) -> ExecutionResult:
        """Execute *compiled_query* against Oracle.

        Steps
        -----
        1. Validate SQL via ``SQLGuard`` (SELECT-only, no multi-statement).
        2. Validate bind-param completeness.
        3. Log SQL at DEBUG level — params are intentionally omitted.
        4. Acquire connection from pool (or raise if not initialised).
        5. Run sync DB call in ThreadPoolExecutor with timeout enforcement.
        6. Fetch up to ``settings.max_row_limit`` rows.
        7. Map ``oracledb.DatabaseError`` → ``ExecutionError``.
        """
        # 1-2. Guard checks (raises ExecutionError on violation)
        self._guard.validate(compiled_query.sql)
        self._guard.validate_params(compiled_query.sql, dict(compiled_query.params))

        # 3. Safe logging — SQL only, param values intentionally omitted
        logger.debug(
            "Oracle execute: table=%s params_count=%d sql=%s",
            compiled_query.table,
            len(compiled_query.params),
            compiled_query.sql,
        )

        # 4. Pool check
        if self._pool is None:
            logger.warning(
                "Oracle executor called but connection pool is not initialised. "
                "Call init_pool() after setting ORACLE_DSN, ORACLE_USER and ORACLE_PASSWORD."
            )
            raise ExecutionError(
                "Oracle executor not configured.",
                detail=(
                    "No Oracle connection pool is available. "
                    "Call init_pool() after setting ORACLE_DSN, ORACLE_USER "
                    "and ORACLE_PASSWORD."
                ),
            )

        # 5-7. Real execution — sync call wrapped in thread pool
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(self._thread_pool, self._sync_query, compiled_query),
                timeout=float(self._timeout),
            )
        except asyncio.TimeoutError as exc:
            raise ExecutionError(
                "Query timeout exceeded.",
                detail=f"Query did not complete within {self._timeout}s.",
            ) from exc

    def _sync_query(self, compiled_query: CompiledQuery) -> ExecutionResult:
        """Synchronous Oracle query — runs inside ``ThreadPoolExecutor``."""
        import oracledb  # already imported by init_pool

        row_limit = min(
            compiled_query.debug_plan.limit if compiled_query.debug_plan else settings.default_row_limit,
            settings.max_row_limit,
        )
        try:
            with self._pool.acquire() as conn:
                with conn.cursor() as cur:
                    cur.execute(compiled_query.sql, compiled_query.params)
                    columns: list[str] = (
                        [d[0].lower() for d in cur.description]
                        if cur.description
                        else []
                    )
                    raw_rows = cur.fetchmany(row_limit)
                    result_rows = [dict(zip(columns, row)) for row in raw_rows]

                    status = ExecutionStatus.SUCCESS if result_rows else ExecutionStatus.EMPTY
                    logger.info(
                        "Oracle query returned %d row(s) (table=%s).",
                        len(result_rows),
                        compiled_query.table,
                    )
                    return ExecutionResult(
                        status=status,
                        columns=columns,
                        rows=result_rows,
                        row_count=len(result_rows),
                    )
        except oracledb.DatabaseError as exc:
            detail = str(exc)
            error_class = _classify_oracle_error(detail)
            logger.error(
                "Oracle DatabaseError on table=%s class=%s: %s",
                compiled_query.table,
                error_class,
                exc,
            )
            raise ExecutionError(
                f"Database error during query execution [{error_class}].",
                detail=detail,
            ) from exc

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the connection pool gracefully."""
        if self._pool is not None:
            self._pool.close()
            self._pool = None
            logger.info("Oracle connection pool closed.")
        self._thread_pool.shutdown(wait=False)
