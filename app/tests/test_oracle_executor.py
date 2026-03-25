"""Tests for SQLGuard and OracleExecutor safety contracts."""

from __future__ import annotations

import asyncio
import types

import pytest

from app.core.exceptions import ExecutionError
from app.providers.executor.sql_guard import SQLGuard


@pytest.fixture
def guard() -> SQLGuard:
    return SQLGuard()


# ---------------------------------------------------------------------------
# Valid SELECT queries
# ---------------------------------------------------------------------------


class TestValidQueries:
    def test_simple_select(self, guard: SQLGuard) -> None:
        guard.validate("SELECT reg_no FROM employee WHERE ROWNUM <= :p1")

    def test_select_with_subquery(self, guard: SQLGuard) -> None:
        guard.validate(
            "SELECT * FROM (SELECT reg_no FROM employee ORDER BY reg_no) WHERE ROWNUM <= :p1"
        )

    def test_with_cte(self, guard: SQLGuard) -> None:
        """WITH (CTE) queries should be allowed."""
        guard.validate(
            "WITH active AS (SELECT * FROM employee WHERE quit_date IS NULL) "
            "SELECT * FROM active WHERE ROWNUM <= :p1"
        )

    def test_select_with_join(self, guard: SQLGuard) -> None:
        guard.validate(
            "SELECT e.reg_no, d.dept_name FROM employee e "
            "JOIN department d ON e.dept_id = d.dept_id WHERE ROWNUM <= :p1"
        )


# ---------------------------------------------------------------------------
# Blocked write operations
# ---------------------------------------------------------------------------


class TestBlockedWrites:
    def test_insert_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("INSERT INTO employee (reg_no) VALUES (1)")

    def test_update_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("UPDATE employee SET salary = 0")

    def test_delete_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("DELETE FROM employee WHERE reg_no = 1")

    def test_merge_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("MERGE INTO employee USING ...")

    def test_drop_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("DROP TABLE employee")

    def test_truncate_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("TRUNCATE TABLE employee")

    def test_create_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("CREATE TABLE test (id NUMBER)")

    def test_alter_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("ALTER TABLE employee ADD col NUMBER")

    def test_grant_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("GRANT SELECT ON employee TO user1")

    def test_revoke_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("REVOKE SELECT ON employee FROM user1")


# ---------------------------------------------------------------------------
# PL/SQL blocks
# ---------------------------------------------------------------------------


class TestPLSQLBlocked:
    def test_begin_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("BEGIN NULL; END;")

    def test_declare_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("DECLARE v NUMBER; BEGIN v := 1; END;")

    def test_execute_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Write operation"):
            guard.validate("EXECUTE some_proc")


# ---------------------------------------------------------------------------
# Multi-statement
# ---------------------------------------------------------------------------


class TestMultiStatement:
    def test_multi_statement_blocked(self, guard: SQLGuard) -> None:
        """Two SELECT statements separated by ';' should be rejected."""
        with pytest.raises(ExecutionError, match="Multi-statement"):
            guard.validate("SELECT 1 FROM dual; SELECT 2 FROM dual")

    def test_write_keyword_in_second_statement(self, guard: SQLGuard) -> None:
        """A write keyword after ';' is caught by keyword check first."""
        with pytest.raises(ExecutionError, match="Prohibited keyword"):
            guard.validate("SELECT 1 FROM dual; DROP TABLE employee")


# ---------------------------------------------------------------------------
# Empty SQL
# ---------------------------------------------------------------------------


class TestEmptySQL:
    def test_empty_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Empty SQL"):
            guard.validate("")

    def test_whitespace_blocked(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Empty SQL"):
            guard.validate("   ")


# ---------------------------------------------------------------------------
# Bind parameter validation
# ---------------------------------------------------------------------------


class TestBindParams:
    def test_valid_params(self, guard: SQLGuard) -> None:
        guard.validate_params(
            "SELECT * FROM emp WHERE reg_no = :p1 AND ROWNUM <= :p2",
            {"p1": 1001, "p2": 100},
        )

    def test_missing_param(self, guard: SQLGuard) -> None:
        with pytest.raises(ExecutionError, match="Missing bind"):
            guard.validate_params(
                "SELECT * FROM emp WHERE reg_no = :p1 AND ROWNUM <= :p2",
                {"p1": 1001},  # p2 missing
            )

    def test_extra_param_ok(self, guard: SQLGuard) -> None:
        """Extra params not referenced in SQL are harmless."""
        guard.validate_params(
            "SELECT * FROM emp WHERE ROWNUM <= :p1",
            {"p1": 100, "p2": "unused"},
        )


# ---------------------------------------------------------------------------
# OracleExecutor safety
# ---------------------------------------------------------------------------


class TestOracleExecutorSafety:
    @pytest.mark.asyncio
    async def test_oracle_executor_validates_before_error(self) -> None:
        """OracleExecutor should validate SQL before raising not-configured."""
        from app.domain.execution_models import CompiledQuery
        from app.providers.executor.oracle_executor import OracleExecutor

        executor = OracleExecutor()

        with pytest.raises(ExecutionError, match="Write operation"):
            await executor.execute(
                CompiledQuery(
                    sql="DELETE FROM employee",
                    table="XXBT_PDKS_PER_DETAILS_V",
                )
            )

    @pytest.mark.asyncio
    async def test_oracle_executor_not_configured(self) -> None:
        """Valid SELECT should fail with not-configured (no pool)."""
        from app.domain.execution_models import CompiledQuery
        from app.providers.executor.oracle_executor import OracleExecutor

        executor = OracleExecutor()

        with pytest.raises(ExecutionError, match="not configured"):
            await executor.execute(
                CompiledQuery(
                    sql="SELECT reg_no FROM employee WHERE ROWNUM <= :p1",
                    params={"p1": 100},
                    table="XXBT_PDKS_PER_DETAILS_V",
                    selected_columns=["reg_no"],
                )
            )

    @pytest.mark.asyncio
    async def test_oracle_executor_not_configured_sets_subtype(self) -> None:
        from app.domain.execution_models import CompiledQuery
        from app.providers.executor.oracle_executor import OracleExecutor

        executor = OracleExecutor()

        with pytest.raises(ExecutionError) as exc_info:
            await executor.execute(
                CompiledQuery(
                    sql="SELECT reg_no FROM employee WHERE ROWNUM <= :p1",
                    params={"p1": 100},
                    table="XXBT_PDKS_PER_DETAILS_V",
                    selected_columns=["reg_no"],
                )
            )

        assert exc_info.value.execution_error_subtype == "executor_unavailable"
        assert exc_info.value.execution_error_message_normalized == "oracle_executor_not_configured"

    @pytest.mark.asyncio
    async def test_oracle_executor_async_timeout_sets_timeout_subtype(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.domain.execution_models import CompiledQuery
        from app.providers.executor.oracle_executor import OracleExecutor

        executor = OracleExecutor(timeout=1)

        async def _raise_timeout(awaitable, timeout):  # type: ignore[no-untyped-def]
            raise asyncio.TimeoutError()

        monkeypatch.setattr("app.providers.executor.oracle_executor.asyncio.wait_for", _raise_timeout)
        monkeypatch.setattr(executor, "_pool", object())

        with pytest.raises(ExecutionError) as exc_info:
            await executor.execute(
                CompiledQuery(
                    sql="SELECT reg_no FROM employee WHERE ROWNUM <= :p1",
                    params={"p1": 100},
                    table="XXBT_PDKS_PER_DETAILS_V",
                    selected_columns=["reg_no"],
                )
            )

        assert exc_info.value.execution_error_subtype == "timeout"
        assert exc_info.value.execution_error_message_normalized == "query_timeout_1s"

    def test_oracle_sync_query_sets_call_timeout_and_fetch_hints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.domain.execution_models import CompiledQuery
        from app.providers.executor.oracle_executor import OracleExecutor

        class _Cursor:
            def __init__(self) -> None:
                self.description = [("REG_NO",)]
                self.arraysize = 0
                self.prefetchrows = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def execute(self, sql, params) -> None:
                self.sql = sql
                self.params = params

            def fetchmany(self, amount):
                return [(1001,)]

        class _Connection:
            def __init__(self) -> None:
                self.call_timeout = 0
                self.cursor_instance = _Cursor()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def cursor(self):
                return self.cursor_instance

        class _Pool:
            def __init__(self) -> None:
                self.connection = _Connection()

            def acquire(self):
                return self.connection

        fake_oracledb = types.SimpleNamespace(DatabaseError=RuntimeError)
        monkeypatch.setitem(__import__("sys").modules, "oracledb", fake_oracledb)

        executor = OracleExecutor(timeout=2)
        pool = _Pool()
        executor._pool = pool

        result = executor._sync_query(
            CompiledQuery(
                sql="SELECT REG_NO FROM employee WHERE ROWNUM <= :p1",
                params={"p1": 1},
                table="XXBT_PDKS_PER_DETAILS_V",
                selected_columns=["REG_NO"],
            )
        )

        assert pool.connection.call_timeout == 2000
        assert pool.connection.cursor_instance.arraysize == 100
        assert pool.connection.cursor_instance.prefetchrows == 100
        assert result.execution_time_ms is not None
        assert result.execution_time_ms >= 0
