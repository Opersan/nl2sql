"""Helpers for running a local Open WebUI + NL2SQL demo stack."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPO_ROOT / "results" / "openwebui_demo"
OPENWEBUI_VENV = REPO_ROOT / ".openwebui-venv"
OPENWEBUI_DATA_DIR = RESULTS_ROOT / "open-webui-data"
BACKEND_LOG = RESULTS_ROOT / "backend.log"
OPENWEBUI_LOG = RESULTS_ROOT / "openwebui.log"

DEFAULT_BACKEND_PORT = 8000
DEFAULT_OPENWEBUI_PORT = 3000

DEFAULT_ADMIN_EMAIL = "admin@example.com"
DEFAULT_ADMIN_PASSWORD = "OpenWebUI123!"
DEFAULT_ADMIN_NAME = "NL2SQL Demo Admin"


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    log_path: Path
    log_handle: object

    def stop(self) -> None:
        if self.process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        self.log_handle.close()


def ensure_results_dir() -> Path:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    return RESULTS_ROOT


def repo_python() -> Path:
    return Path(sys.executable)


def openwebui_python() -> Path:
    if os.name == "nt":
        return OPENWEBUI_VENV / "Scripts" / "python.exe"
    return OPENWEBUI_VENV / "bin" / "python"


def openwebui_cli() -> Path:
    if os.name == "nt":
        return OPENWEBUI_VENV / "Scripts" / "open-webui.exe"
    return OPENWEBUI_VENV / "bin" / "open-webui"


def chrome_executable() -> str | None:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _run(command: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    subprocess.run(
        command,
        check=True,
        cwd=str(cwd or REPO_ROOT),
        env=env,
    )


def _module_available(python_path: Path, module_name: str) -> bool:
    result = subprocess.run(
        [
            str(python_path),
            "-c",
            (
                "import importlib.util, sys; "
                f"sys.exit(0 if importlib.util.find_spec('{module_name}') else 1)"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        cwd=str(REPO_ROOT),
    )
    return result.returncode == 0


def ensure_openwebui_runtime(*, with_playwright: bool) -> Path:
    ensure_results_dir()

    if not openwebui_python().exists():
        _run([str(repo_python()), "-m", "venv", str(OPENWEBUI_VENV)])
        _run([str(openwebui_python()), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])

    install_map = [("open_webui", "open-webui")]
    if with_playwright:
        install_map.append(("playwright", "playwright"))

    for module_name, package_name in install_map:
        if not _module_available(openwebui_python(), module_name):
            _run([str(openwebui_python()), "-m", "pip", "install", package_name])

    return openwebui_python()


def wait_for_url(url: str, *, timeout_s: int = 120, ok_statuses: set[int] | None = None) -> None:
    ok_statuses = ok_statuses or {200, 201, 204, 301, 302, 307, 308, 401, 403}
    deadline = time.time() + timeout_s
    last_error: str | None = None

    while time.time() < deadline:
        try:
            request = Request(url, headers={"User-Agent": "nl2sql-openwebui-demo"})
            with urlopen(request, timeout=5) as response:  # noqa: S310
                if response.status in ok_statuses:
                    return
                last_error = f"unexpected status {response.status}"
        except HTTPError as exc:
            if exc.code in ok_statuses:
                return
            last_error = f"http {exc.code}"
        except URLError as exc:
            last_error = str(exc.reason)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            last_error = str(exc)
        time.sleep(1)

    raise TimeoutError(f"Timed out waiting for {url}. Last error: {last_error}")


def _spawn(
    *,
    name: str,
    command: list[str],
    env: dict[str, str],
    log_path: Path,
) -> ManagedProcess:
    ensure_results_dir()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
    )
    return ManagedProcess(name=name, process=process, log_path=log_path, log_handle=log_handle)


def start_backend(*, port: int = DEFAULT_BACKEND_PORT) -> ManagedProcess:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "LLM_PROVIDER": "mock",
            "ENABLE_ORACLE_EXECUTOR": "false",
        }
    )
    command = [
        str(repo_python()),
        "-m",
        "uvicorn",
        "app.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    process = _spawn(name="backend", command=command, env=env, log_path=BACKEND_LOG)
    wait_for_url(f"http://127.0.0.1:{port}/health", timeout_s=120)
    return process


def start_openwebui(
    *,
    backend_port: int = DEFAULT_BACKEND_PORT,
    port: int = DEFAULT_OPENWEBUI_PORT,
    admin_email: str = DEFAULT_ADMIN_EMAIL,
    admin_password: str = DEFAULT_ADMIN_PASSWORD,
    admin_name: str = DEFAULT_ADMIN_NAME,
) -> ManagedProcess:
    ensure_openwebui_runtime(with_playwright=False)
    OPENWEBUI_DATA_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "DATA_DIR": str(OPENWEBUI_DATA_DIR),
            "ENABLE_PERSISTENT_CONFIG": "False",
            "PORT": str(port),
            "HOST": "127.0.0.1",
            "WEBUI_URL": f"http://127.0.0.1:{port}",
            "ENABLE_OPENAI_API": "True",
            "OPENAI_API_BASE_URLS": f"http://127.0.0.1:{backend_port}/v1",
            "OPENAI_API_KEYS": "EMPTY",
            "ENABLE_OLLAMA_API": "False",
            "BYPASS_MODEL_ACCESS_CONTROL": "True",
            "DEFAULT_MODELS": "nl2sql",
            "WEBUI_ADMIN_EMAIL": admin_email,
            "WEBUI_ADMIN_PASSWORD": admin_password,
            "WEBUI_ADMIN_NAME": admin_name,
        }
    )

    command = [str(openwebui_cli()), "serve"]
    process = _spawn(name="openwebui", command=command, env=env, log_path=OPENWEBUI_LOG)
    wait_for_url(f"http://127.0.0.1:{port}", timeout_s=180)
    return process


def stop_processes(*processes: ManagedProcess) -> None:
    for process in reversed(processes):
        process.stop()


def reset_demo_data() -> None:
    if RESULTS_ROOT.exists():
        shutil.rmtree(RESULTS_ROOT)
    ensure_results_dir()
