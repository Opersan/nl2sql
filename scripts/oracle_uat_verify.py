"""Oracle UAT connectivity + data verification — q_107 and q_109.

Bu script üç şeyi doğrular:
  1. TCP reachability (bestdbuat.besttransformer.com:1541)
  2. OracleExecutor.init_pool() — bağlantı havuzu açılıyor mu
  3. q_107 ve q_109 için gerçek verinin UAT'ta var olup olmadığı

.env dosyasındaki ORACLE_DSN / ORACLE_USER / ORACLE_PASSWORD kullanılır.
Çalıştırmadan önce .env ayarlarının doğru olduğundan emin olun.

Usage:
    .\.venv\Scripts\python scripts\oracle_uat_verify.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# pydantic-settings handles .env loading via model_config in Settings.
from app.core.config import settings
from app.core.exceptions import ExecutionError
from app.providers.executor.oracle_executor import OracleExecutor


# ---------------------------------------------------------------------------
# Step 1: TCP reachability
# ---------------------------------------------------------------------------

def _check_tcp(host: str, port: int, timeout: int = 5) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception as exc:
        print(f"  TCP UNREACHABLE: {exc}")
        return False


def _parse_host_port(dsn: str) -> tuple[str, int] | None:
    """Best-effort extraction of HOST and PORT from Oracle DSN string."""
    import re
    host_m = re.search(r"HOST\s*=\s*([^\s)]+)", dsn, re.IGNORECASE)
    port_m = re.search(r"PORT\s*=\s*(\d+)", dsn, re.IGNORECASE)
    if host_m and port_m:
        return host_m.group(1), int(port_m.group(1))
    return None


# ---------------------------------------------------------------------------
# Verification queries
# ---------------------------------------------------------------------------

# q_107: PO_DISTRIBUTIONS_ALL — verify table exists and has rows
_Q107_PROBE = "SELECT COUNT(*) AS cnt FROM PO_DISTRIBUTIONS_ALL WHERE ROWNUM <= :p1"
_Q107_PARAMS = {"p1": 1}

# q_109: PO_HEADERS_ALL — verify rows exist within last 30 days
_Q109_PROBE = (
    "SELECT COUNT(*) AS cnt "
    "FROM PO_HEADERS_ALL "
    "WHERE creation_date >= TRUNC(SYSDATE) - 30 "
    "AND ROWNUM <= :p1"
)
_Q109_PARAMS = {"p1": 1}

# Fallback for q_109: total row count (data may be older than 30 days in UAT)
_Q109_FALLBACK = "SELECT COUNT(*) AS cnt FROM PO_HEADERS_ALL WHERE ROWNUM <= :p1"
_Q109_FALLBACK_PARAMS = {"p1": 1}


async def _run_probe(executor: OracleExecutor, sql: str, params: dict) -> Any:
    """Execute a raw probe query via the sync pool wrapped in a thread."""
    import asyncio as _asyncio
    import oracledb

    def _sync_probe() -> Any:
        try:
            with executor._pool.acquire() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    raw = cur.fetchone()
                    return raw[0] if raw else None
        except oracledb.DatabaseError as exc:
            raise ExecutionError("Probe query failed.", detail=str(exc)) from exc

    loop = _asyncio.get_event_loop()
    try:
        return await _asyncio.wait_for(
            loop.run_in_executor(executor._thread_pool, _sync_probe),
            timeout=float(executor._timeout),
        )
    except _asyncio.TimeoutError as exc:
        raise ExecutionError("Probe query timed out.", detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("\n  Oracle UAT Verification")
    print("  " + "=" * 60)

    # ── Credential check ───────────────────────────────────────────
    dsn = settings.oracle_dsn
    user = settings.oracle_user
    password = settings.oracle_password

    if not dsn or not user or not password:
        print("\n  [FAIL] ORACLE_DSN / ORACLE_USER / ORACLE_PASSWORD not set in .env")
        sys.exit(1)

    print(f"\n  DSN    : {dsn[:60]}...")
    print(f"  USER   : {user}")
    print(f"  TIMEOUT: {settings.oracle_timeout}s")

    # ── Step 1: TCP ────────────────────────────────────────────────
    print("\n  [1/3] TCP connectivity check")
    hp = _parse_host_port(dsn)
    if hp:
        host, port = hp
        print(f"        Connecting to {host}:{port} ...")
        if _check_tcp(host, port):
            print(f"        PASS — {host}:{port} reachable")
        else:
            print(f"        FAIL — cannot reach {host}:{port}. Check VPN / network.")
            sys.exit(1)
    else:
        print("        SKIP — could not parse HOST:PORT from DSN")

    # ── Step 2: init_pool ──────────────────────────────────────────
    print("\n  [2/3] OracleExecutor.init_pool()")
    executor = OracleExecutor()
    try:
        import os as _os
        import struct as _struct

        def _oci_bits(path: str) -> int:
            """Return PE machine bits (32 or 64) or 0 on error."""
            try:
                with open(path, "rb") as f:
                    if f.read(2) != b"MZ":
                        return 0
                    f.seek(0x3C)
                    pe_off = _struct.unpack("<I", f.read(4))[0]
                    f.seek(pe_off)
                    if f.read(4) != b"PE\x00\x00":
                        return 0
                    machine = _struct.unpack("<H", f.read(2))[0]
                    return 64 if machine == 0x8664 else 32
            except Exception:
                return 0

        # Ordered candidates — explicit 64-bit Oracle Instant Client or full client paths.
        # Do NOT call oracledb.init_oracle_client here — let executor handle it
        # (calling it twice corrupts internal state → DPY-2053).
        _INSTANT_CLIENT_CANDIDATES = [
            r"C:\app\furkan.kiraz\product\21c\dbhomeXE\bin",
            r"C:\instantclient_21_11",
            r"C:\instantclient_21_3",
            r"C:\oracle\instantclient",
        ]
        found_ic_dir = ""
        for ic_dir in _INSTANT_CLIENT_CANDIDATES:
            dll = _os.path.join(ic_dir, "oci.dll")
            bits = _oci_bits(dll)
            if bits == 64:
                found_ic_dir = ic_dir
                print(f"        INFO  — 64-bit Oracle client found at {ic_dir}")
                break
            elif bits == 32:
                print(f"        INFO  — skipping {ic_dir} (32-bit oci.dll, Python is 64-bit)")
        if not found_ic_dir:
            print("        INFO  — no 64-bit Instant Client dir found; executor will try PATH-based thick mode")

        await executor.init_pool(thick_mode_lib_dir=found_ic_dir or None)
        print("        PASS — connection pool initialised")
    except ExecutionError as exc:
        print(f"        FAIL — {exc}  detail: {exc.detail}")
        sys.exit(1)
    except Exception as exc:
        print(f"        FAIL — unexpected error: {exc}")
        sys.exit(1)

    # ── Step 3: UAT data probes ────────────────────────────────────
    print("\n  [3/3] UAT data verification")

    # q_107 probe
    print("\n  q_107 — PO_DISTRIBUTIONS_ALL row count probe:")
    try:
        cnt = await _run_probe(executor, _Q107_PROBE, _Q107_PARAMS)
        if cnt and int(cnt) > 0:
            print(f"        PASS — PO_DISTRIBUTIONS_ALL has data (probe returned {cnt}+ row)")
            print("        q_107 (4-table JOIN, distribution amount): READY for real execution")
        else:
            print("        WARN — PO_DISTRIBUTIONS_ALL is empty in UAT")
            print("        q_107 will return 0 rows — empty result, not an error")
    except ExecutionError as exc:
        print(f"        FAIL — {exc.args[0]}  detail: {exc.detail}")

    # q_109 probe — last 30 days
    print("\n  q_109 — PO_HEADERS_ALL last-30-days row count probe:")
    try:
        cnt = await _run_probe(executor, _Q109_PROBE, _Q109_PARAMS)
        if cnt and int(cnt) > 0:
            print(f"        PASS — rows found with creation_date >= TRUNC(SYSDATE)-30")
            print("        q_109 (son 30 gün filter): READY")
        else:
            print("        WARN — no PO_HEADERS_ALL rows within last 30 days in UAT")
            # Fallback: check if table has any data at all
            cnt_all = await _run_probe(executor, _Q109_FALLBACK, _Q109_FALLBACK_PARAMS)
            if cnt_all and int(cnt_all) > 0:
                print(f"        INFO  — table has data but none in last 30 days (UAT freeze)")
                print("        q_109 will execute without error but return empty result")
            else:
                print("        WARN  — PO_HEADERS_ALL is completely empty in UAT")
    except ExecutionError as exc:
        print(f"        FAIL — {exc.args[0]}  detail: {exc.detail}")

    await executor.close()
    print("\n  Verification complete.")
    print("  " + "=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
