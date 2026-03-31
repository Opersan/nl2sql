"""Install or update the NL2SQL Open WebUI clarification filter via API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
FILTER_ID = "nl2sql_clarification_buttons_filter"
FILTER_NAME = "NL2SQL Clarification Buttons"
FILTER_DESC = "Render NL2SQL clarification replies as Open WebUI button cards."
FILTER_FILE = REPO_ROOT / "openwebui" / "functions" / f"{FILTER_ID}.py"


def _request(url: str, *, token: str, method: str, payload: dict | None = None) -> dict:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with HTTP {exc.code}: {detail}") from exc


def install_filter(
    *,
    base_url: str,
    token: str,
    filter_id: str,
    filter_name: str,
    filter_file: Path,
    filter_desc: str,
) -> None:
    content = filter_file.read_text(encoding="utf-8")
    payload = {
        "id": filter_id,
        "name": filter_name,
        "meta": {"description": filter_desc},
        "content": content,
    }

    api_root = base_url.rstrip("/") + "/api/v1/functions"

    try:
        _request(
            f"{api_root}/id/{filter_id}",
            token=token,
            method="GET",
        )
        _request(
            f"{api_root}/id/{filter_id}/update",
            token=token,
            method="POST",
            payload=payload,
        )
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        _request(f"{api_root}/create", token=token, method="POST", payload=payload)

    current = _request(
        f"{api_root}/id/{filter_id}",
        token=token,
        method="GET",
    )
    if not current.get("is_active", False):
        _request(f"{api_root}/id/{filter_id}/toggle", token=token, method="POST")

    current = _request(
        f"{api_root}/id/{filter_id}",
        token=token,
        method="GET",
    )
    if not current.get("is_global", False):
        _request(f"{api_root}/id/{filter_id}/toggle/global", token=token, method="POST")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3010")
    parser.add_argument("--token", required=True, help="Open WebUI admin bearer token")
    parser.add_argument("--id", default=FILTER_ID, help="Open WebUI function id")
    parser.add_argument("--name", default=FILTER_NAME, help="Open WebUI function name")
    parser.add_argument("--description", default=FILTER_DESC, help="Open WebUI function description")
    parser.add_argument(
        "--file",
        default=str(FILTER_FILE),
        help="Path to the filter .py file",
    )
    args = parser.parse_args()

    filter_file = Path(args.file)
    if not filter_file.is_absolute():
        filter_file = REPO_ROOT / filter_file

    install_filter(
        base_url=args.base_url,
        token=args.token,
        filter_id=args.id,
        filter_name=args.name,
        filter_file=filter_file,
        filter_desc=args.description,
    )
    print(f"Installed Open WebUI filter: {args.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
