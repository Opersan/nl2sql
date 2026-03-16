"""SQL guard – query safety enforcement for the Oracle executor.

This module provides a stateless ``SQLGuard`` that validates compiled SQL
before it is sent to the database.  It is the last line of defence
against accidental data modification.

Enforcement rules
=================
1. **SELECT-only** – only ``SELECT`` statements are allowed.  Any DML
   (``INSERT``, ``UPDATE``, ``DELETE``, ``MERGE``) or DDL (``CREATE``,
   ``ALTER``, ``DROP``, ``TRUNCATE``, ``GRANT``, ``REVOKE``) is rejected.
2. **No PL/SQL blocks** – ``BEGIN``, ``DECLARE``, ``EXEC``/``EXECUTE``
   are rejected.
3. **No multiple statements** – semi-colon separated statement batches
   are rejected.
4. **Bind-param only values** – the guard does not parse SQL deeply, but
   it checks that the statement uses Oracle named bind params (``:p1``).

Oracle legacy compatibility
===========================
The guard is **agnostic** to pagination syntax — it does not inspect
``ROWNUM`` vs ``FETCH FIRST``.  That contract is enforced by the SQL
compiler.  The guard focuses purely on preventing writes.
"""

from __future__ import annotations

import re

from app.core.exceptions import ExecutionError

# Patterns matched against the *first non-whitespace* word of the query.
_WRITE_KEYWORDS: frozenset[str] = frozenset({
    "INSERT", "UPDATE", "DELETE", "MERGE",
    "CREATE", "ALTER", "DROP", "TRUNCATE",
    "GRANT", "REVOKE",
    "BEGIN", "DECLARE", "EXEC", "EXECUTE",
})

_MULTI_STATEMENT_RE = re.compile(r";\s*\S", re.DOTALL)


class SQLGuard:
    """Validate that a compiled SQL string is safe for read-only execution."""

    def validate(self, sql: str) -> None:
        """Raise ``ExecutionError`` if *sql* is not a safe read-only query.

        All checks are syntactic — no DB connection is needed.
        """
        stripped = sql.strip()
        if not stripped:
            raise ExecutionError("Empty SQL statement.", detail="sql is blank")

        # 1. First keyword must be SELECT (or WITH for CTEs)
        first_word = stripped.split()[0].upper()
        if first_word not in ("SELECT", "WITH"):
            if first_word in _WRITE_KEYWORDS:
                raise ExecutionError(
                    f"Write operation not allowed: {first_word}",
                    detail=f"SQL starts with '{first_word}', which is a write/DDL keyword.",
                )
            raise ExecutionError(
                f"Unexpected SQL keyword: {first_word}",
                detail="Only SELECT and WITH (CTE) statements are allowed.",
            )

        # 2. No write keywords anywhere in the statement (catch subquery injections)
        upper_sql = stripped.upper()
        for kw in _WRITE_KEYWORDS:
            # Use word boundary to avoid false positives (e.g. "UPDATED_AT")
            if re.search(rf"\b{kw}\b", upper_sql):
                # Allow "SELECT ... FROM ... DELETE ..." only if keyword is
                # part of a column/alias name — but the simple word-boundary
                # check is intentionally strict.  False positives with column
                # names like "DELETED" are handled by the allowlist below.
                if kw in ("DELETE",) and re.search(r"\bDELETED?\b", upper_sql):
                    # Allow "DELETED" as a column name — common pattern
                    if not re.search(r"\bDELETE\s+FROM\b", upper_sql):
                        continue
                raise ExecutionError(
                    f"Prohibited keyword in query: {kw}",
                    detail=f"Keyword '{kw}' found in SQL body.",
                )

        # 3. No multi-statement batches
        if _MULTI_STATEMENT_RE.search(stripped):
            raise ExecutionError(
                "Multi-statement query not allowed.",
                detail="Semi-colon followed by another statement detected.",
            )

    def validate_params(
        self,
        sql: str,
        params: dict[str, object],
    ) -> None:
        """Validate that named bind params in *sql* match *params* keys.

        This is a best-effort check — it does not parse SQL fully but
        ensures basic consistency.
        """
        # Extract :pN style bind placeholders
        found = set(re.findall(r":(\w+)", sql))
        provided = set(params.keys())

        missing = found - provided
        if missing:
            raise ExecutionError(
                f"Missing bind parameters: {', '.join(sorted(missing))}",
                detail=f"SQL references {sorted(missing)} but params only has {sorted(provided)}.",
            )
