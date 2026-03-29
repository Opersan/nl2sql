"""Browser automation for the Open WebUI clarification demo."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from playwright.sync_api import Error, TimeoutError, sync_playwright


QUERY = "yonetici unvanli calisanlari goster"


def _body_text(page) -> str:
    return page.locator("body").inner_text(timeout=15_000)


def _wait_for_body_contains(page, needle: str, *, timeout_ms: int = 90_000) -> str:
    deadline = time.time() + (timeout_ms / 1000)
    last_text = ""
    while time.time() < deadline:
        try:
            last_text = _body_text(page)
        except Error:
            last_text = ""
        if needle in last_text:
            return last_text
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for {needle!r}")


def _wait_for_occurrence_growth(page, needle: str, previous_count: int, *, timeout_ms: int = 90_000) -> str:
    deadline = time.time() + (timeout_ms / 1000)
    last_text = ""
    while time.time() < deadline:
        last_text = _body_text(page)
        if last_text.count(needle) > previous_count:
            return last_text
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for new occurrence of {needle!r}")


def _wait_for_text_growth(page, previous_length: int, *, min_growth: int = 40, timeout_ms: int = 90_000) -> str:
    deadline = time.time() + (timeout_ms / 1000)
    stable_hits = 0
    last_text = _body_text(page)
    while time.time() < deadline:
        current = _body_text(page)
        if len(current) >= previous_length + min_growth:
            if len(current) == len(last_text):
                stable_hits += 1
                if stable_hits >= 2:
                    return current
            else:
                stable_hits = 0
        last_text = current
        time.sleep(1)
    raise TimeoutError("Timed out waiting for assistant response growth")


def _chat_input(page):
    selectors = [
        "textarea",
        "div[contenteditable='true']",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count():
            candidate = locator.last
            try:
                candidate.wait_for(state="visible", timeout=5_000)
                return candidate
            except TimeoutError:
                continue
    raise TimeoutError("Chat input not found")


def _send_message(page, text: str) -> None:
    box = _chat_input(page)
    box.click()
    try:
        box.fill(text)
    except Error:
        box.press("Control+A")
        box.press("Backspace")
        box.type(text)
    box.press("Enter")


def _parse_option_labels(text: str) -> list[str]:
    matches = re.findall(r"^\s*\d+\.\s+(.+?)\s*$", text, flags=re.MULTILINE)
    labels: list[str] = []
    for match in matches:
        cleaned = match.strip()
        if cleaned.lower() != "sen karar ver":
            labels.append(cleaned)
    return labels


def _maybe_login(page, *, base_url: str, email: str, password: str) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    time.sleep(2)
    text = _body_text(page)
    if "Sign In" not in text and "Login" not in text and "E-mail" not in text:
        return

    email_input = page.locator("input[type='email'], input[name='email']").first
    password_input = page.locator("input[type='password']").first
    email_input.fill(email)
    password_input.fill(password)

    for selector in (
        "button:has-text('Sign In')",
        "button:has-text('Login')",
        "button:has-text('Continue')",
        "button[type='submit']",
    ):
        button = page.locator(selector)
        if button.count():
            button.first.click()
            break

    _wait_for_body_contains(page, "New Chat", timeout_ms=90_000)


def _maybe_select_model(page, model_name: str) -> None:
    text = _body_text(page)
    if model_name in text:
        return
    for selector in (
        "button:has-text('Select a model')",
        "button:has-text('Select Model')",
        "[role='button']:has-text('Select a model')",
    ):
        trigger = page.locator(selector)
        if trigger.count():
            trigger.first.click()
            page.get_by_text(model_name, exact=True).click(timeout=20_000)
            time.sleep(1)
            return


def run_demo(*, base_url: str, email: str, password: str, artifact_dir: Path, headless: bool) -> dict[str, object]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    screenshots = {}

    with sync_playwright() as playwright:
        launch_kwargs = {"headless": headless}
        chrome_path = (
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        )
        for candidate in chrome_path:
            if candidate.exists():
                launch_kwargs["executable_path"] = str(candidate)
                break

        browser = playwright.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})

        _maybe_login(page, base_url=base_url, email=email, password=password)
        _maybe_select_model(page, "nl2sql")

        (artifact_dir / "00-home.txt").write_text(_body_text(page), encoding="utf-8")
        screenshots["home"] = "00-home.png"
        page.screenshot(path=str(artifact_dir / screenshots["home"]), full_page=True)

        first_text = _body_text(page)
        clarification_count = first_text.count("Sen karar ver")

        _send_message(page, QUERY)
        clarification_text = _wait_for_occurrence_growth(
            page,
            "Sen karar ver",
            clarification_count,
        )
        labels = _parse_option_labels(clarification_text)
        screenshots["clarification"] = "01-clarification.png"
        page.screenshot(path=str(artifact_dir / screenshots["clarification"]), full_page=True)

        previous_length = len(clarification_text)
        _send_message(page, "1")
        numeric_text = _wait_for_text_growth(page, previous_length)
        screenshots["numeric"] = "02-numeric-reply.png"
        page.screenshot(path=str(artifact_dir / screenshots["numeric"]), full_page=True)

        clarification_count = numeric_text.count("Sen karar ver")
        _send_message(page, QUERY)
        label_prompt_text = _wait_for_occurrence_growth(page, "Sen karar ver", clarification_count)

        reply_label = labels[1] if len(labels) > 1 else (labels[0] if labels else "1")
        previous_length = len(label_prompt_text)
        _send_message(page, reply_label)
        label_text = _wait_for_text_growth(page, previous_length)
        screenshots["label"] = "03-label-reply.png"
        page.screenshot(path=str(artifact_dir / screenshots["label"]), full_page=True)

        clarification_count = label_text.count("Sen karar ver")
        _send_message(page, QUERY)
        defer_prompt_text = _wait_for_occurrence_growth(page, "Sen karar ver", clarification_count)
        previous_length = len(defer_prompt_text)
        _send_message(page, "sen karar ver")
        defer_text = _wait_for_text_growth(page, previous_length)
        screenshots["defer"] = "04-sen-karar-ver.png"
        page.screenshot(path=str(artifact_dir / screenshots["defer"]), full_page=True)

        browser.close()

    result = {
        "query": QUERY,
        "labels": labels,
        "reply_label": reply_label,
        "screenshots": screenshots,
        "body_lengths": {
            "clarification": len(clarification_text),
            "numeric": len(numeric_text),
            "label": len(label_text),
            "defer": len(defer_text),
        },
    }
    (artifact_dir / "ui_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Open WebUI browser demo.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    run_demo(
        base_url=args.base_url,
        email=args.email,
        password=args.password,
        artifact_dir=Path(args.artifact_dir),
        headless=args.headless,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
