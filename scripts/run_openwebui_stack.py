"""Start the local NL2SQL backend plus Open WebUI surface."""

from __future__ import annotations

import argparse
import time

from openwebui_runtime import (
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_BACKEND_PORT,
    DEFAULT_OPENWEBUI_PORT,
    BACKEND_LOG,
    OPENWEBUI_LOG,
    ensure_openwebui_runtime,
    start_backend,
    start_openwebui,
    stop_processes,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run NL2SQL + Open WebUI locally.")
    parser.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
    parser.add_argument("--webui-port", type=int, default=DEFAULT_OPENWEBUI_PORT)
    args = parser.parse_args()

    ensure_openwebui_runtime(with_playwright=False)
    backend = start_backend(port=args.backend_port)
    openwebui = start_openwebui(backend_port=args.backend_port, port=args.webui_port)

    print(f"Backend:    http://127.0.0.1:{args.backend_port}")
    print(f"Open WebUI: http://127.0.0.1:{args.webui_port}")
    print(f"Login:      {DEFAULT_ADMIN_EMAIL} / {DEFAULT_ADMIN_PASSWORD}")
    print(f"Logs:       {BACKEND_LOG}")
    print(f"            {OPENWEBUI_LOG}")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 130
    finally:
        stop_processes(openwebui, backend)


if __name__ == "__main__":
    raise SystemExit(main())
