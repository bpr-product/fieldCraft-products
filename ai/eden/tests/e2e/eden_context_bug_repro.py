"""Diagnostic repro for the EDEN follow-up context-resolution bug.

Logs in (reusing the cached auth state from eden_chat_smoke.py), opens the
widget, and sends two turns: one that names a real project, then a bare
follow-up that names none. Captures the raw request/response JSON for both
/api/v1/agent/navigate calls so we can see exactly what the backend
resolved, without needing backend log access.
"""

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

APP_URL = os.environ.get("EDEN_APP_URL", "http://localhost:3000")
TEST_EMAIL = os.environ.get("EDEN_TEST_EMAIL", "")
TEST_PASSWORD = os.environ.get("EDEN_TEST_PASSWORD", "")
STORAGE_STATE_PATH = Path(__file__).parent / ".auth_state.json"
FIRST_MESSAGE = os.environ.get("EDEN_FIRST_MESSAGE", "what's the schedule for Buma P?")
SECOND_MESSAGE = os.environ.get("EDEN_SECOND_MESSAGE", "what is the schedule")

CHAT_FAB_SELECTOR = ".fabChat"
CHAT_INPUT_SELECTOR = "#chat-textfield-type-a-message"
SEND_BUTTON_SELECTOR = "button[aria-label='send']"
TYPING_INDICATOR_SELECTOR = ".chat-msg-container .typing-row"

captured = []


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        storage_state = str(STORAGE_STATE_PATH) if STORAGE_STATE_PATH.exists() else None
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()

        def on_response(response):
            if "/api/v1/agent/navigate" in response.url:
                try:
                    req_body = response.request.post_data
                    resp_body = response.json()
                    captured.append({"request": json.loads(req_body) if req_body else None, "response": resp_body})
                except Exception as e:
                    captured.append({"error": str(e), "url": response.url})

        page.on("response", on_response)

        page.goto(APP_URL, wait_until="domcontentloaded")
        try:
            page.get_by_role("button", name="Login").click(timeout=5000)
            page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

        if "b2clogin.com" in page.url:
            page.locator("#email").fill(TEST_EMAIL)
            page.locator("#password").fill(TEST_PASSWORD)
            page.locator("#next").click()
            page.wait_for_url(lambda u: "b2clogin.com" not in u, timeout=30000)

        page.wait_for_selector(CHAT_FAB_SELECTOR, timeout=30000)
        page.locator(CHAT_FAB_SELECTOR).click()
        page.wait_for_selector(CHAT_INPUT_SELECTOR, timeout=10000)

        for msg in (FIRST_MESSAGE, SECOND_MESSAGE):
            chat_input = page.locator(CHAT_INPUT_SELECTOR)
            chat_input.click()
            chat_input.fill(msg)
            page.locator(SEND_BUTTON_SELECTOR).click()
            try:
                page.wait_for_selector(TYPING_INDICATOR_SELECTOR, timeout=3000)
            except Exception:
                pass
            try:
                page.wait_for_selector(TYPING_INDICATOR_SELECTOR, state="detached", timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            # Reopen widget in case a navigate-intent turn collapsed it
            if not page.locator(CHAT_INPUT_SELECTOR).is_visible():
                page.locator(CHAT_FAB_SELECTOR).click()
                page.wait_for_selector(CHAT_INPUT_SELECTOR, timeout=10000)

        context.storage_state(path=str(STORAGE_STATE_PATH))
        context.close()
        browser.close()

    print(json.dumps(captured, indent=2))


if __name__ == "__main__":
    main()
