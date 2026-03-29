"""Run a full local Open WebUI integration demo and save evidence."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from openwebui_runtime import (
    BACKEND_LOG,
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_BACKEND_PORT,
    DEFAULT_OPENWEBUI_PORT,
    OPENWEBUI_LOG,
    RESULTS_ROOT,
    ensure_openwebui_runtime,
    reset_demo_data,
    start_backend,
    start_openwebui,
    stop_processes,
)


OPENWEBUI_EVENT_RE = re.compile(
    r"\[openwebui\]\s+session=(?P<session>\S+)\s+status=(?P<status>\S+)\s+clarification_id=(?P<clarification>\S+)\s+message=(?P<message>.+)$"
)


def _parse_backend_events(log_path: Path) -> list[dict[str, str]]:
    if not log_path.exists():
        return []

    events: list[dict[str, str]] = []
    for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = OPENWEBUI_EVENT_RE.search(raw_line)
        if not match:
            continue
        event = match.groupdict()
        event["message"] = event["message"].strip()
        events.append(event)
    return events


def _write_report(
    *,
    report_path: Path,
    ui_result: dict[str, object],
    backend_events: list[dict[str, str]],
    backend_port: int,
    webui_port: int,
) -> None:
    sessions = sorted({event["session"] for event in backend_events})
    lines = [
        "# Open WebUI Demo Report",
        "",
        f"- Backend: http://127.0.0.1:{backend_port}",
        f"- Open WebUI: http://127.0.0.1:{webui_port}",
        f"- Query: {ui_result['query']}",
        f"- Parsed labels: {', '.join(ui_result.get('labels', []))}",
        f"- Label reply used: {ui_result.get('reply_label', '-')}",
        f"- Sessions seen in backend logs: {', '.join(sessions) if sessions else '-'}",
        f"- Session continuity preserved: {'yes' if len(sessions) == 1 and backend_events else 'no'}",
        "",
        "## Screenshots",
    ]
    for name, path in ui_result.get("screenshots", {}).items():
        lines.append(f"- {name}: {path}")

    lines.extend(
        [
            "",
            "## Backend Events",
        ]
    )
    for event in backend_events:
        lines.append(
            f"- session={event['session']} status={event['status']} "
            f"clarification_id={event['clarification']} message={event['message']}"
        )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_browser_demo(*, webui_port: int, artifact_dir: Path, headless: bool) -> dict[str, object]:
    command = [
        str(ensure_openwebui_runtime(with_playwright=True)),
        str(Path(__file__).resolve().parent / "openwebui_playwright_demo.py"),
        "--base-url",
        f"http://127.0.0.1:{webui_port}",
        "--email",
        DEFAULT_ADMIN_EMAIL,
        "--password",
        DEFAULT_ADMIN_PASSWORD,
        "--artifact-dir",
        str(artifact_dir),
    ]
    if headless:
        command.append("--headless")

    subprocess.run(command, check=True, cwd=str(Path(__file__).resolve().parents[1]))
    return json.loads((artifact_dir / "ui_result.json").read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Open WebUI clarification demo.")
    parser.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
    parser.add_argument("--webui-port", type=int, default=DEFAULT_OPENWEBUI_PORT)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    artifact_dir = RESULTS_ROOT / "artifacts"
    report_path = RESULTS_ROOT / "demo_report.md"
    backend = None
    openwebui = None

    reset_demo_data()
    ensure_openwebui_runtime(with_playwright=True)

    try:
        backend = start_backend(port=args.backend_port)
        openwebui = start_openwebui(backend_port=args.backend_port, port=args.webui_port)
        time.sleep(5)
        ui_result = _run_browser_demo(
            webui_port=args.webui_port,
            artifact_dir=artifact_dir,
            headless=args.headless,
        )
    finally:
        stop_processes(*(p for p in (openwebui, backend) if p is not None))

    backend_events = _parse_backend_events(BACKEND_LOG)
    _write_report(
        report_path=report_path,
        ui_result=ui_result,
        backend_events=backend_events,
        backend_port=args.backend_port,
        webui_port=args.webui_port,
    )

    print(f"Demo report: {report_path}")
    print(f"Backend log:  {BACKEND_LOG}")
    print(f"WebUI log:    {OPENWEBUI_LOG}")
    print(f"Artifacts:    {artifact_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
