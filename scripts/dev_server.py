"""Development server wrapper for Windows-friendly uvicorn reload.

Why this exists
---------------
On some Windows setups, ``uvicorn --reload`` can leave the reloader process
attached to the terminal even after the worker prints "Finished server process".
This wrapper runs uvicorn in a child process, enables polling-based reload on
Windows by default, and tears down the full process tree on Ctrl+C.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time


def _build_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        args.app,
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if args.reload:
        command.append("--reload")
    return command


def _terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the NL2SQL API dev server.")
    parser.add_argument("--app", default="app.api.main:app", help="ASGI app import path")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable uvicorn reload mode",
    )
    args = parser.parse_args()
    args.reload = not args.no_reload

    env = os.environ.copy()
    if os.name == "nt" and args.reload:
        env.setdefault("WATCHFILES_FORCE_POLLING", "true")
        print("[dev-server] WATCHFILES_FORCE_POLLING=true", flush=True)

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    command = _build_command(args)
    print(f"[dev-server] starting: {' '.join(command)}", flush=True)

    proc = subprocess.Popen(command, env=env, creationflags=creationflags)
    try:
        while True:
            return_code = proc.poll()
            if return_code is not None:
                return return_code
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[dev-server] Ctrl+C received, stopping uvicorn process tree...", flush=True)
        _terminate_process_tree(proc)
        return 130
    finally:
        _terminate_process_tree(proc)


if __name__ == "__main__":
    raise SystemExit(main())