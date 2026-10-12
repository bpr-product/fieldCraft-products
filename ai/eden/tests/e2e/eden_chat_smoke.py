"""Playwright smoke test for the EDEN chat widget (fieldcraft-tenant-web).

Drives the real browser against a locally running frontend
(default http://localhost:3000) talking to the EDEN backend on
http://localhost:8020. Logs in through Azure AD B2C, opens the EDEN
assistant widget, sends a message, and asserts a response renders.

Usage:
    pip install playwright
    playwright install chromium

    set EDEN_TEST_EMAIL=shan.admin@thefieldcraft.com
    set EDEN_TEST_PASSWORD=***          (do not hardcode; export as an env var)
    python test/e2e/eden_chat_smoke.py

Env vars:
    EDEN_APP_URL        frontend base URL (default http://localhost:3000)
    EDEN_TEST_EMAIL     B2C sign-in email
    EDEN_TEST_PASSWORD  B2C sign-in password
    EDEN_CHAT_MESSAGE   message to send (default "Where do you want to go?")
    EDEN_STORAGE_STATE  path to a storage_state JSON to reuse/save the
                        authenticated session (default .auth_state.json in
                        this directory) so subsequent runs skip the B2C
                        login form entirely.
    EDEN_HEADLESS       "true"/"false" (default "false" so you can watch it)
"""

import json
import os
import sys

# Windows consoles default to a legacy codepage (e.g. cp1252) that can't
# encode emoji EDEN's own responses/route icons contain (e.g. the calendar
# icon in a Gantt Charts suggestion) -- without this, a print()/log() of
# that text crashes the whole run instead of just being a cosmetic mangle.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

APP_URL = os.environ.get("EDEN_APP_URL", "http://localhost:3000")
TEST_EMAIL = os.environ.get("EDEN_TEST_EMAIL", "")
TEST_PASSWORD = os.environ.get("EDEN_TEST_PASSWORD", "")
CHAT_MESSAGE = os.environ.get("EDEN_CHAT_MESSAGE", "Where do you want to go?")
HEADLESS = os.environ.get("EDEN_HEADLESS", "false").lower() == "true"
STORAGE_STATE_PATH = Path(
    os.environ.get("EDEN_STORAGE_STATE", str(Path(__file__).parent / ".auth_state.json"))
)
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
RESULTS_PATH = Path(__file__).parent / "eden_chat_smoke_results.md"

CHAT_FAB_SELECTOR = ".fabChat"
CHAT_INPUT_SELECTOR = "#chat-textfield-type-a-message"
SEND_BUTTON_SELECTOR = "button[aria-label='send']"
RESET_BUTTON_SELECTOR = "button[aria-label='Reset chat']"
FIELDCRAFT_MESSAGE_SELECTOR = ".chat-msg-container .message.FIELDCRAFT"
YOU_MESSAGE_SELECTOR = ".chat-msg-container .message.You"
TYPING_INDICATOR_SELECTOR = ".chat-msg-container .typing-row"


def log(msg: str) -> None:
    print(f"[eden-chat-smoke] {msg}", flush=True)


def do_b2c_login(page, email: str, password: str) -> None:
    """Fill Azure AD B2C's hosted sign-in form.

    B2C custom-policy pages commonly use #signInName/#email and #password
    with a #next/#continue submit button, but label text is the most
    stable cross-policy anchor, so try that first and fall back to ids.
    """
    email_field = None
    for locator in (
        page.locator("#email"),
        page.locator("#signInName"),
        page.get_by_placeholder("Email Address"),
        page.locator("input[type='email']"),
    ):
        try:
            locator.wait_for(state="visible", timeout=4000)
            email_field = locator
            break
        except PWTimeoutError:
            continue
    if email_field is None:
        raise RuntimeError("Could not find B2C email field — page markup may have changed")
    email_field.fill(email)

    password_field = None
    for locator in (page.locator("#password"), page.get_by_placeholder("Password")):
        try:
            locator.wait_for(state="visible", timeout=4000)
            password_field = locator
            break
        except PWTimeoutError:
            continue
    if password_field is None:
        raise RuntimeError("Could not find B2C password field — page markup may have changed")
    password_field.fill(password)

    for locator in (page.locator("#next"), page.get_by_role("button", name="Sign in")):
        try:
            locator.click(timeout=4000)
            return
        except PWTimeoutError:
            continue
    raise RuntimeError("Could not find B2C sign-in submit button")


def run() -> dict:
    result = {
        "app_url": APP_URL,
        "steps": [],
        "passed": False,
        "response_text": None,
        "error": None,
    }
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        storage_state = str(STORAGE_STATE_PATH) if STORAGE_STATE_PATH.exists() else None
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()

        try:
            log(f"Navigating to {APP_URL}")
            page.goto(APP_URL, wait_until="domcontentloaded")

            login_button = page.get_by_role("button", name="Login")
            try:
                login_button.wait_for(state="visible", timeout=5000)
                log("Landing page shown — clicking Login to start OIDC redirect")
                login_button.click()
                page.wait_for_load_state("domcontentloaded")
                result["steps"].append("Clicked landing-page Login button")
            except PWTimeoutError:
                pass

            if "b2clogin.com" in page.url:
                log("Redirected to Azure B2C login — filling credentials")
                if not TEST_EMAIL or not TEST_PASSWORD:
                    raise RuntimeError(
                        "Not authenticated and EDEN_TEST_EMAIL/EDEN_TEST_PASSWORD are unset"
                    )
                do_b2c_login(page, TEST_EMAIL, TEST_PASSWORD)
                page.wait_for_url(lambda u: "b2clogin.com" not in u, timeout=30000)
                result["steps"].append("Logged in via Azure B2C")
            else:
                result["steps"].append("Reused existing authenticated session")

            log("Waiting for app shell to load")
            page.wait_for_selector(CHAT_FAB_SELECTOR, timeout=30000)
            result["steps"].append("App shell loaded (chat FAB visible)")

            log("Opening EDEN chat widget")
            fab = page.locator(CHAT_FAB_SELECTOR)
            fab.wait_for(state="visible", timeout=10000)
            fab.click()
            try:
                page.wait_for_selector(CHAT_INPUT_SELECTOR, timeout=6000)
            except PWTimeoutError:
                log("Chat input not visible after first click — retrying once")
                fab.click()
                page.wait_for_selector(CHAT_INPUT_SELECTOR, timeout=10000)
            result["steps"].append("Chat widget opened")

            # Let any initial greeting/history load settle before baselining
            # message counts -- otherwise the greeting's own async render can
            # land AFTER before_count is captured and get mistaken for the
            # reply to what we're about to send. If the LAST turn in this
            # account's real history was a "navigate" turn, replaying it on
            # mount can auto-collapse the widget the same way a live
            # navigate response does -- reopen if that happens.
            page.wait_for_timeout(1500)
            try:
                page.wait_for_selector(TYPING_INDICATOR_SELECTOR, state="detached", timeout=5000)
            except PWTimeoutError:
                pass
            if not page.locator(CHAT_INPUT_SELECTOR).is_visible():
                log("Widget collapsed while history/greeting settled — reopening")
                fab.click()
                page.wait_for_selector(CHAT_INPUT_SELECTOR, timeout=10000)

            log(f"Sending message: {CHAT_MESSAGE!r}")
            chat_input = page.locator(CHAT_INPUT_SELECTOR)
            before_fieldcraft_count = page.locator(FIELDCRAFT_MESSAGE_SELECTOR).count()
            before_you_count = page.locator(YOU_MESSAGE_SELECTOR).count()
            chat_input.click()
            chat_input.fill(CHAT_MESSAGE)
            if chat_input.input_value() != CHAT_MESSAGE:
                raise RuntimeError(
                    f"Input value after fill() was {chat_input.input_value()!r}, "
                    f"expected {CHAT_MESSAGE!r} -- fill did not stick"
                )
            send_button = page.locator(SEND_BUTTON_SELECTOR)
            send_button.wait_for(state="visible", timeout=5000)
            if send_button.is_disabled():
                raise RuntimeError("Send button still disabled after filling the message")
            send_button.click()
            # Confirm the message actually got submitted (a "You" bubble
            # rendered) before waiting on any reply -- otherwise a swallowed
            # click looks identical to a slow reply until it times out.
            page.wait_for_function(
                """([sel, prevCount]) => document.querySelectorAll(sel).length > prevCount""",
                arg=[YOU_MESSAGE_SELECTOR, before_you_count],
                timeout=5000,
            )
            result["steps"].append("Message sent")
            page.screenshot(path=str(SCREENSHOT_DIR / "after_send.png"))

            try:
                page.wait_for_selector(TYPING_INDICATOR_SELECTOR, timeout=3000)
            except PWTimeoutError:
                pass
            page.wait_for_selector(TYPING_INDICATOR_SELECTOR, state="detached", timeout=45000)

            # EDEN either (a) renders a text/option-card reply in the chat, or
            # (b) resolves a navigation intent and collapses the widget after
            # routing the app elsewhere (see Chat.tsx's onNavigated comment) —
            # both are a successful turn, so accept whichever happens first.
            url_before = page.url
            try:
                page.wait_for_function(
                    """([sel, prevCount]) => document.querySelectorAll(sel).length > prevCount""",
                    arg=[FIELDCRAFT_MESSAGE_SELECTOR, before_fieldcraft_count],
                    timeout=20000,
                )
                last_response = page.locator(FIELDCRAFT_MESSAGE_SELECTOR).last
                response_text = last_response.inner_text().strip()
                result["response_text"] = response_text
                result["steps"].append("EDEN response received (text reply)")
                log(f"Response: {response_text[:200]!r}")
            except PWTimeoutError:
                if page.url != url_before:
                    result["response_text"] = f"[navigated to {page.url}]"
                    result["steps"].append("EDEN response received (navigation intent, widget collapsed)")
                    log(f"EDEN navigated the app to: {page.url}")
                else:
                    raise

            screenshot_path = SCREENSHOT_DIR / "chat_response.png"
            page.screenshot(path=str(screenshot_path))
            result["screenshot"] = str(screenshot_path)

            context.storage_state(path=str(STORAGE_STATE_PATH))
            result["passed"] = True

        except Exception as exc:  # noqa: BLE001 - top-level smoke test, report and re-raise-safe
            result["error"] = str(exc)
            try:
                page.screenshot(path=str(SCREENSHOT_DIR / "failure.png"))
                result["screenshot"] = str(SCREENSHOT_DIR / "failure.png")
            except Exception:
                pass
        finally:
            context.close()
            browser.close()

    return result


def write_results_md(result: dict) -> None:
    lines = [
        "# EDEN Chat Widget — Playwright Smoke Test Results",
        "",
        f"- **App URL:** {result['app_url']}",
        f"- **Outcome:** {'PASS' if result['passed'] else 'FAIL'}",
        "",
        "## Steps",
        "",
    ]
    for step in result["steps"]:
        lines.append(f"- [x] {step}")
    if result.get("response_text"):
        lines += ["", "## EDEN Response", "", "```", result["response_text"], "```"]
    if result.get("screenshot"):
        lines += ["", f"Screenshot: `{result['screenshot']}`"]
    if result.get("error"):
        lines += ["", "## Error", "", "```", result["error"], "```"]
    RESULTS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"Results written to {RESULTS_PATH}")


if __name__ == "__main__":
    outcome = run()
    write_results_md(outcome)
    print(json.dumps(outcome, indent=2))
    sys.exit(0 if outcome["passed"] else 1)
