"""Integration test script — hits the LIVE /api/v1/agent/navigate endpoint.

Run after starting the API (`python main.py`):
    python test/run_integration_tests.py [base_url]

Unlike test_navigation_agent.py (unit tests, mocked dependencies), this exercises
the real HTTP layer, real orchestrator dispatch, and whatever LLM/DB backends are
actually configured in .env. Tests requiring real project data (disambiguation,
entity resolution) are expected to report the graceful fallback, not a match,
until MONGODB_URI points at real data — see docs/poc/TESTING.md.
"""
import json
import sys
import urllib.request

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8020"
ENDPOINT = f"{BASE_URL}/api/v1/agent/navigate"

CASES = [
    # (utterance, currentRoute, expected type, description)
    ("take me to invoices", "/dashboard", "navigate", "keyword nav"),
    ("show me the schedules", "/dashboard", "navigate", "keyword nav, plural"),
    ("open purchase orders", "/dashboard", "navigate", "keyword nav, multi-word"),
    ("show me reports", "/dashboard", None, "must NOT be Purchase Orders (substring bug)"),
    ("where are my tools", "/dashboard", "navigate", "must NOT be Invoices (substring bug)"),
    ("hello there", "/dashboard", "guidance", "greeting, no LLM needed"),
    ("thanks so much", "/dashboard", "guidance", "thanks treated as greeting"),
    ("hey take me to invoices", "/dashboard", "navigate", "greeting word + real request"),
    ("what can i do here", "/invoice", "guidance", "on-page guidance question"),
    ("where is the export button", "/invoice", "guidance", "on-page feature lookup"),
    ("where can i export invoices", "/dashboard", "fallback", "cross-page feature lookup"),
    ("what is the margin on this job", "/dashboard", "fallback", "data question -> bubbles"),
    ("take me to payroll", "/dashboard", "fallback", "not in registry"),
    ("show invoices", "/invoice", "guidance", "already on page"),
    ("schedule", "/dashboard", "fallback", "entity route, no project named"),
    ("open invocies", "/dashboard", "fallback", "typo -> did-you-mean suggestion"),

    # --- Added 2026-07-08: registry expansion (54 routes) + bugfix regression cases ---
    ("where's the edit vendor button", "/vendormanagement", "guidance",
     "locate-array fix: vendor edit icon (was empty locate, unfindable)"),
    ("where's the delete vendor button", "/vendormanagement", "guidance",
     "locate-array fix: vendor delete icon (was empty locate, unfindable)"),
    ("where are the bids out", "/dashboard", "guidance",
     "keyword fix: dashboard card (dropped bare 'bids'/'sold' keywords that over-matched)"),
    ("where is hold project", "/soldproduction/projectinformation", "guidance",
     "route-matching bugfix: most-specific-pattern selection (was misidentified as sold_production)"),
    ("where are the working days checkboxes", "/tenantconfiguration", "guidance",
     "locate-array fix: working days checkboxes retargeted to Sun-Sat; General "
     "Configuration is a Settings tab, not the orphaned /Generalconfiguration URL"),
    ("where are the audit rows per page options", "/materialmart/audit/SKU-1/mat-1", "guidance",
     "route-matching bugfix: inventory_audit vs inventory prefix collision"),
    ("take me to the admin panel", "/dashboard", "navigate", "new route: admin_home"),
    ("vendor bill", "/dashboard", "navigate", "new route: bill_form"),
    ("material transaction log", "/dashboard", "navigate",
     "bugfix: inventory_audit's unresolved :param pattern now redirects to /inventory instead of a broken literal URL"),
    ("start a bid wizard", "/dashboard", "navigate",
     "bugfix (2026-07-08): dispatch() now compares feature-lookup vs keyword-match scores "
     "instead of feature always winning by default — navigates straight to /createbid"),
    ("upload image", "/dashboard", "guidance",
     "AB#30856 fix: /imageupload was a dead, unreachable legacy page removed from the "
     "registry -- 'upload image' now resolves to Document Management's real upload feature"),
    ("open chat assistant", "/dashboard", "navigate",
     "same bugfix: chat_ui's own 3-word intent keyword now correctly outscores the shorter "
     "'open chat' feature match, navigating straight to /chatui instead of a cross-page suggestion"),

    # --- Added 2026-07-08: global_features (not tied to any single route) ---
    ("open cart", "/dashboard", "guidance",
     "global feature: personal Cart & Drafts (Topbar icon, available on every page)"),
    ("open admin cart", "/invoice", "guidance",
     "global feature: Admin Cart / Sales Cart List; regression case for a keyword "
     "collision with 'open cart' that briefly returned the wrong cart"),
    ("show me the completed tasks", "/soldproduction", "guidance",
     "global feature: Completed Task Feed tab (GoPAL sidebar, available on every page) "
     "— previously misfired as 'You're already on Common Dashboard' via a bad LLM guess"),

    # --- Added 2026-07-08: feature-vs-keyword precedence bugfix ---
    ("show me the customers list", "/dashboard", "navigate",
     "bugfix: admin_home's 'Customers tab' feature and customer_table's own intent "
     "keyword both match 'customer list' with an identical score — ties must go to "
     "the direct navigation (/customertable), not the cross-page feature suggestion"),

    # --- Added 2026-07-08: Upload Bill global feature ---
    ("i need to upload a bill", "/dashboard", "guidance",
     "new global feature: Upload Bill (Topbar action) — previously incorrectly "
     "matched Document Management's generic 'uploads' keyword instead"),
    ("upload vendor bill", "/dashboard", "guidance",
     "regression case: must not tie with bill_form's 'vendor bill' intent keyword "
     "and navigate to the wrong (system-generated bill) page instead"),

    # --- Added 2026-07-08: Bill list/review page_feature + global-vs-page scoring bugfix ---
    ("where can i see uploaded bills", "/soldproduction/projectinformation", "guidance",
     "bugfix: global features used to win outright over any current-page match — "
     "this incorrectly landed on the global 'Upload Bill' Topbar action instead of "
     "the page's own more specific 'Vendor / Subcontractor Bill list' feature"),
]


def post(utterance: str, current_route: str) -> dict:
    body = json.dumps({
        "utterance": utterance,
        "currentRoute": current_route,
        "userId": "SYN-integration-user",
        "tenantId": "SYN-integration-tenant",
    }).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read())


def main() -> int:
    passed = failed = 0
    for utterance, route, expected_type, description in CASES:
        try:
            result = post(utterance, route)
        except Exception as e:
            print(f"FAIL  {description:45s} '{utterance}' -> request error: {e}")
            failed += 1
            continue

        got_type = result.get("type")
        ok = (expected_type is None and got_type != "navigate") or got_type == expected_type
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"{status}  {description:45s} '{utterance}' -> {got_type}"
              f"{'' if ok else f' (expected {expected_type})'}")

    total = passed + failed
    print(f"\n{passed}/{total} passed ({passed / total:.0%})")
    print("Go/No-Go target per docs/poc/REQUIREMENTS.md: >= 80% resolution rate")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
