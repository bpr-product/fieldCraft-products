"""Prompt-to-dashboard web UI for Gold-layer validation.

A tiny stdlib HTTP server (no FastAPI/uvicorn needed): open the page, type a
business prompt, get a self-contained HTML dashboard rendered from the live Gold
collections. Each prompt is one consumer-validation test — the dashboard's
"Validation notes" panel states whether Gold served it or exposed a gap.

Prompt interpretation is a DETERMINISTIC intent-router (v1): keyword -> a builder
that runs a read-only Gold aggregation and fills a dashboard spec. It is
structured so a Claude-backed NL->spec interpreter can replace `route()` later
without changing the renderer or the builders.

Run:   python gold/tools/dashboard_app.py            # serves http://localhost:8800
Env:   MONGO_URI (from .env.local), DASH_PORT (default 8800)

!!! SECURITY — NO APPLICATION-LAYER AUTHORIZATION (finding 31, PR #7999) !!!
This tool has NO auth, session, role, or tenant-membership check anywhere in
this file. `tenant` is taken verbatim from an unauthenticated query-string
param (`Handler.do_GET` -> `/build?...&tenant=...`), and the NL interpreter
path (`route()`) can additionally EXTRACT and SILENTLY OVERRIDE the tenant
from free-text `prompt` — neither path verifies the caller is entitled to
that tenant's data. The builders above query gold_eden.invoice_summary,
project_health, workforce_status, etc. — sensitive, PII/financial Gold
collections — with zero scoping enforcement beyond whatever tenant string
was handed to it.

The ONLY current mitigation is binding the HTTP server to 127.0.0.1 (see
`main()` below) — i.e. "don't expose the port," not an application control.
This is a known, explicitly out-of-scope-for-this-PR gap (documented
follow-up). DO NOT deploy this off-loopback, put it behind a reverse proxy
that forwards arbitrary tenants, or wire it into any shared/public host
without FIRST adding an authenticated-caller -> allowed-tenant(s) check
(e.g. validate a session/JWT and intersect the requested tenant against
that caller's permitted tenant set) before any Gold query runs. Treat any
change that exposes this server beyond 127.0.0.1 without that check as a
security regression.
"""
from __future__ import annotations

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Load .env.local if present so MONGO_URI resolves in local dev.
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env.local")
except Exception:
    pass

from gold.tools.dashboard_builder import render_dashboard, query_gold  # noqa: E402
from gold.tools import nl_interpreter  # noqa: E402

EDEN_DB = os.environ.get("GOLD_EDEN_DB", "gold_eden")


# --------------------------------------------------------------------------- #
# Builders — each maps a business intent to a Gold query + dashboard spec.
# All read-only. Empty Gold renders gracefully (zeros / "no rows").
# --------------------------------------------------------------------------- #
def _match(tenant: str | None) -> dict:
    return {"tenantId": tenant} if tenant else {}


def build_executive(tenant):
    rows = query_gold(EDEN_DB, "tenant_dashboard",
                      [{"$match": _match(tenant)}, {"$limit": 5}])
    projs = sum(int(r.get("projectCount") or 0) for r in rows)
    risk = sum(int(r.get("atRiskCount") or 0) for r in rows)
    out = sum(float(r.get("totalOutstanding") or 0) for r in rows)
    active = (rows[0].get("activeProjects") if rows else []) or []
    table_rows = [[p.get("orderNo"), p.get("status"), p.get("completionPct"),
                   p.get("overdueTaskCount"), p.get("outstandingAmt")] for p in active[:20]]
    return {
        "title": "Executive / Tenant Overview",
        "subtitle": f"tenant {tenant or 'ALL'} · source gold_eden.tenant_dashboard",
        "cards": [{"label": "Projects", "value": projs},
                  {"label": "At risk", "value": risk, "tone": "bad" if risk else "ok"},
                  {"label": "Outstanding", "value": f"${out:,.0f}", "tone": "warn" if out else "none"}],
        "tables": [{"title": "Active projects", "columns": ["Order #", "Status", "Completion %", "Overdue tasks", "Outstanding"],
                    "rows": table_rows}],
        "notes": ["Cross-project rollup served from one Gold collection ✓.",
                  "Workforce/schedule/customer rollups not in tenant_dashboard yet (navigation gap, WI #22037)."],
    }


def build_ar(tenant):
    rows = query_gold(EDEN_DB, "invoice_summary",
                      [{"$match": _match(tenant)},
                       {"$project": {"projectId": 1, "totalBilled": 1, "totalReceived": 1,
                                     "totalOutstanding": 1, "overdueInvoices": 1}}])
    billed = sum(float(r.get("totalBilled") or 0) for r in rows)
    recv = sum(float(r.get("totalReceived") or 0) for r in rows)
    out = sum(float(r.get("totalOutstanding") or 0) for r in rows)
    top = sorted(rows, key=lambda r: float(r.get("totalOutstanding") or 0), reverse=True)[:15]
    return {
        "title": "Financial / Accounts Receivable",
        "subtitle": f"tenant {tenant or 'ALL'} · source gold_eden.invoice_summary",
        "cards": [{"label": "Billed", "value": f"${billed:,.0f}"},
                  {"label": "Received", "value": f"${recv:,.0f}", "tone": "ok"},
                  {"label": "Outstanding", "value": f"${out:,.0f}", "tone": "warn" if out else "none"}],
        "tables": [{"title": "Top outstanding by project",
                    "columns": ["Project", "Outstanding", "Overdue invoices"],
                    "rows": [[r.get("projectId"), r.get("totalOutstanding"),
                              len(r.get("overdueInvoices") or [])] for r in top]}],
        "notes": ["Per-project AR served ✓.",
                  "AR AGING BUCKETS (current/30/60/90+) need gold_invoice_aging — UNBUILT (WI #22043).",
                  "Tenant-level financial rollup KPI needs gold_kpi — UNBUILT (WI #22042)."],
    }


def build_workforce(tenant):
    rows = query_gold(EDEN_DB, "workforce_status", [{"$match": _match(tenant)}])
    head = sum(int(r.get("headcount") or 0) for r in rows)
    hrs = sum(float(r.get("hoursLogged") or 0) for r in rows)
    w2 = sum(int(r.get("w2Count") or 0) for r in rows)
    con = sum(int(r.get("contractorCount") or 0) for r in rows)
    utils = [float(r["utilizationPct"]) for r in rows if r.get("utilizationPct") is not None]
    avg_u = round(sum(utils) / len(utils), 1) if utils else None
    top = sorted(rows, key=lambda r: int(r.get("headcount") or 0), reverse=True)[:15]
    return {
        "title": "Workforce / Labor",
        "subtitle": f"tenant {tenant or 'ALL'} · source gold_eden.workforce_status",
        "cards": [{"label": "Headcount", "value": head},
                  {"label": "Hours logged", "value": f"{hrs:,.0f}"},
                  {"label": "Avg utilization", "value": f"{avg_u}%" if avg_u is not None else "—"},
                  {"label": "W2 / 1099", "value": f"{w2} / {con}"}],
        "charts": [{"title": "Headcount by project (top)", "type": "bar",
                    "labels": [str(r.get("projectId")) for r in top],
                    "values": [int(r.get("headcount") or 0) for r in top]}],
        "tables": [{"title": "Workforce by project",
                    "columns": ["Project", "Headcount", "Hours logged", "Utilization %", "Assignments"],
                    "rows": [[r.get("projectId"), r.get("headcount"), r.get("hoursLogged"),
                              r.get("utilizationPct"), r.get("scheduledAssignments")] for r in top]}],
        "notes": ["Served from gold_eden.workforce_status (resource/resource_schedule/timelog) ✓.",
                  "utilization is approximate (duration vs time_spent unit alignment unconfirmed)."],
    }


def build_project_health(tenant):
    rows = query_gold(EDEN_DB, "project_health", [{"$match": _match(tenant)}])
    comps = [float(r["completionPct"]) for r in rows if r.get("completionPct") is not None]
    avg_c = round(sum(comps) / len(comps), 1) if comps else None
    at_risk = sum(1 for r in rows if r.get("scheduleRisk") == "at_risk")
    top = sorted(rows, key=lambda r: int(r.get("slipDays") or 0), reverse=True)[:15]
    return {
        "title": "Project Health",
        "subtitle": f"tenant {tenant or 'ALL'} · source gold_eden.project_health",
        "cards": [{"label": "Projects", "value": len(rows)},
                  {"label": "Avg completion", "value": f"{avg_c}%" if avg_c is not None else "—"},
                  {"label": "At-risk (schedule)", "value": at_risk, "tone": "bad" if at_risk else "ok"}],
        "tables": [{"title": "Projects by slip days",
                    "columns": ["Project", "Completion %", "Schedule risk", "Slip days", "Overdue tasks"],
                    "rows": [[r.get("projectId"), r.get("completionPct"), r.get("scheduleRisk"),
                              r.get("slipDays"), r.get("overdueTaskCount")] for r in top]}],
        "notes": ["Per-project health served ✓.", "Completion-over-time trend needs history — NONE (WI #22044)."],
    }


def build_procurement(tenant):
    rows = query_gold(EDEN_DB, "procurement_status", [{"$match": _match(tenant)}])
    bom = sum(float(r.get("bomTotalValue") or 0) for r in rows)
    unord = sum(float(r.get("unorderedValue") or 0) for r in rows)
    top = sorted(rows, key=lambda r: float(r.get("unorderedValue") or 0), reverse=True)[:15]
    return {
        "title": "Procurement Status",
        "subtitle": f"tenant {tenant or 'ALL'} · source gold_eden.procurement_status",
        "cards": [{"label": "BOM value", "value": f"${bom:,.0f}"},
                  {"label": "Unordered value", "value": f"${unord:,.0f}", "tone": "warn" if unord else "ok"}],
        "tables": [{"title": "Unordered by project", "columns": ["Project", "BOM value", "Unordered value"],
                    "rows": [[r.get("projectId"), r.get("bomTotalValue"), r.get("unorderedValue")] for r in top]}],
        "notes": ["Served ✓. Vendor-level drill needs a vendorId link (not present today)."],
    }


def build_tasks(tenant):
    rows = query_gold(EDEN_DB, "task_intelligence", [{"$match": _match(tenant)}])
    overdue = sum(int(r.get("overdueTaskCount") or 0) for r in rows)
    unassigned = sum(int(r.get("unassignedTaskCount") or 0) for r in rows)
    top = sorted(rows, key=lambda r: int(r.get("overdueTaskCount") or 0), reverse=True)[:15]
    return {
        "title": "Task Intelligence",
        "subtitle": f"tenant {tenant or 'ALL'} · source gold_eden.task_intelligence",
        "cards": [{"label": "Overdue tasks", "value": overdue, "tone": "bad" if overdue else "ok"},
                  {"label": "Unassigned tasks", "value": unassigned, "tone": "warn" if unassigned else "ok"}],
        "tables": [{"title": "Most overdue by project", "columns": ["Project", "Overdue", "Unassigned", "Completion %"],
                    "rows": [[r.get("projectId"), r.get("overdueTaskCount"),
                              r.get("unassignedTaskCount"), r.get("completionPct")] for r in top]}],
        "notes": ["Overdue/unassigned served ✓.",
                  "Milestones / critical-path are NULL stubs — need gold_eden.schedule_overview (WI #22036)."],
    }


def build_bid(tenant):
    rows = query_gold(EDEN_DB, "bid_context", [{"$match": _match(tenant)}])
    answered = [r for r in rows if int(r.get("totalQuestions") or 0) > 0]
    top = sorted(answered, key=lambda r: int(r.get("totalQuestions") or 0), reverse=True)[:15]
    return {
        "title": "Bid / Q&A Completeness",
        "subtitle": f"tenant {tenant or 'ALL'} · source gold_eden.bid_context",
        "cards": [{"label": "Projects with a template", "value": len(answered)}],
        "tables": [{"title": "Completeness by project", "columns": ["Project", "Answered", "Total", "Status"],
                    "rows": [[r.get("projectId"), r.get("answeredCount"),
                              r.get("totalQuestions"), r.get("status")] for r in top]}],
        "notes": ["Served ✓ (templates resolved from provider-owned question_template)."],
    }


def build_gap(kind: str, _tenant=None):
    g = {
        "kpi": ("KPI Scorecard", "gold_kpi is UNBUILT (WI #22042). No KPI collection exists, so this dashboard cannot be served from Gold."),
        "trend": ("Trend / Period-over-Period", "Gold stores last-state only — NO history (WI #22044). Trend/period-over-period cannot be served. This is a P0 gap."),
        "customer": ("Customer 360", "No customer entity exists in Silver, so no customer Gold collection (WI #22038). Blocked at the source."),
        "aging": ("AR Aging", "gold_invoice_aging is UNBUILT (WI #22043). Aging buckets cannot be served; use the AR dashboard for raw outstanding."),
    }
    title, msg = g.get(kind, ("Unsupported", "No Gold collection serves this prompt."))
    return {"title": f"GAP — {title}", "subtitle": "consumer-validation result: NOT served by Gold",
            "cards": [{"label": "Status", "value": "GAP", "tone": "bad"}],
            "notes": [msg]}


_HELP = {"title": "FIELDCRAFT Gold — Prompt to Dashboard",
         "subtitle": "Type a business prompt. Each dashboard is one Gold consumer-validation test.",
         "notes": ["Try: executive overview · AR / outstanding · workforce utilization · "
                   "project health · procurement gaps · task intelligence · bid completeness.",
                   "Gap probes (prove what Gold can't serve yet): KPI · trend · customer · AR aging."]}


# intent key -> builder. Shared by the keyword router and the Claude NL
# interpreter (which only chooses a key — it never builds a query).
INTENTS: dict[str, Any] = {
    "executive":      build_executive,
    "ar":             build_ar,
    "workforce":      build_workforce,
    "project_health": build_project_health,
    "procurement":    build_procurement,
    "tasks":          build_tasks,
    "bid":            build_bid,
    "gap_kpi":        lambda t: build_gap("kpi", t),
    "gap_aging":      lambda t: build_gap("aging", t),
    "gap_trend":      lambda t: build_gap("trend", t),
    "gap_customer":   lambda t: build_gap("customer", t),
}

# keyword tuples -> intent key (order matters: 'aging' before 'ar').
_KEYWORDS = [
    (("executive", "overview", "tenant dashboard", "at risk", "at-risk", "navigation"), "executive"),
    (("aging",), "gap_aging"),
    (("ar", "receivable", "outstanding", "invoice", "finance", "billed"), "ar"),
    (("workforce", "labor", "utilization", "crew", "headcount", "resource"), "workforce"),
    (("project health", "completion", "health", "slip", "schedule risk"), "project_health"),
    (("procurement", "bom", "purchase", "ordered", "vendor"), "procurement"),
    (("task", "overdue", "unassigned"), "tasks"),
    (("bid", "q&a", "qa", "questionnaire", "answers"), "bid"),
    (("kpi", "metric", "scorecard"), "gap_kpi"),
    (("trend", "over time", "month over month", "history", "period"), "gap_trend"),
    (("customer", "client"), "gap_customer"),
]


def _keyword_intent(prompt: str) -> str | None:
    """Deterministic intent from keywords (whole-word match)."""
    p = (prompt or "").lower().strip()
    if not p:
        return None
    for keys, key in _KEYWORDS:
        # whole-word match so short tokens (e.g. "ar") don't match inside
        # other words ("scorec-ar-d", "are-a", "qu-ar-ter").
        if any(re.search(r"\b" + re.escape(k) + r"\b", p) for k in keys):
            return key
    return None


def _use_nl() -> bool:
    """DASH_NL: 0/1 to force off/on; 'auto' (default) uses NL when available."""
    flag = os.environ.get("DASH_NL", "auto").lower()
    if flag in ("0", "false", "off", "no"):
        return False
    if flag in ("1", "true", "on", "yes"):
        return True
    return nl_interpreter.available()


def route(prompt: str, tenant: str | None) -> dict:
    """Resolve a prompt to a dashboard spec.

    Uses the Claude NL interpreter when available (free-form prompts), falling
    back to the deterministic keyword router. The LLM only picks an intent key
    + tenant; the actual Gold query is always a pre-built, tenant-scoped builder.
    """
    p = (prompt or "").strip()
    if not p:
        return _HELP

    intent: str | None = None
    nl_tenant: str | None = None
    rationale: str | None = None
    used_nl = False

    if _use_nl():
        try:
            decision = nl_interpreter.interpret(p)
            intent = decision.get("intent")
            nl_tenant = (decision.get("tenant") or "").strip() or None
            rationale = decision.get("rationale")
            used_nl = True
        except Exception as exc:
            log.warning("NL interpreter failed (%s); using keyword router", exc)

    if not intent or intent == "none":
        kw = _keyword_intent(p)
        if kw:
            intent, used_nl = kw, False  # keyword resolved it, not the LLM

    builder = INTENTS.get(intent) if intent else None
    if builder is None:
        return {**_HELP, "title": "No matching Gold consumer",
                "subtitle": f"prompt: {p}",
                "notes": ["No intent matched. " + _HELP["notes"][0]]}

    try:
        spec = builder(nl_tenant or tenant)
    except Exception as exc:  # surface query errors as a validation note
        return {"title": "Error building dashboard", "subtitle": p[:200],
                "cards": [{"label": "Status", "value": "ERROR", "tone": "bad"}],
                "notes": [f"{type(exc).__name__}: {exc}"]}

    src = "Claude NL interpreter (claude-opus-4-8)" if used_nl else "keyword router"
    note = f"Routed via {src} → intent '{intent}'"
    if used_nl and rationale:
        note += f": {rationale}"
    spec.setdefault("notes", []).insert(0, note)
    return spec


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #
_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>FIELDCRAFT Gold — Prompt to Dashboard</title>
<style>
:root{--bg:#f6f8fa;--panel:#ffffff;--line:#d8dee4;--txt:#1f2328;--mut:#656d76;--accent:#0969da}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:16px 24px;border-bottom:1px solid var(--line)}h1{margin:0;font-size:18px}
.bar{display:flex;gap:10px;padding:16px 24px;flex-wrap:wrap;align-items:center}
input{background:var(--panel);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:10px 12px;font-size:14px}
#prompt{flex:1;min-width:280px}#tenant{width:170px}
button{background:var(--accent);border:0;color:#fff;font-weight:600;border-radius:8px;padding:10px 18px;cursor:pointer}
.chips{padding:0 24px 12px;display:flex;gap:8px;flex-wrap:wrap}
.chip{background:var(--panel);border:1px solid var(--line);color:var(--mut);border-radius:20px;padding:5px 12px;font-size:12px;cursor:pointer}
.chip:hover{color:var(--txt);border-color:var(--accent)}
iframe{width:100%;height:calc(100vh - 190px);border:0;border-top:1px solid var(--line);background:#f6f8fa}
</style></head><body>
<header><h1>FIELDCRAFT Gold — Prompt to Dashboard <span style="color:var(--mut);font-weight:400">· consumer-driven validation</span></h1></header>
<div class="bar">
  <input id="tenant" placeholder="tenant (optional)">
  <input id="prompt" placeholder="ask in plain language, e.g. 'how is labor tracking on tenant X' or 'what's outstanding'" autofocus>
  <button onclick="go()">Build</button>
</div>
<div class="chips" id="chips"></div>
<iframe id="out"></iframe>
<script>
const SUG=["executive overview","AR outstanding","workforce utilization","project health",
           "procurement gaps","task intelligence","bid completeness","KPI scorecard","trend last month","customer 360"];
const chips=document.getElementById('chips');
SUG.forEach(s=>{const c=document.createElement('span');c.className='chip';c.textContent=s;
  c.onclick=()=>{document.getElementById('prompt').value=s;go();};chips.appendChild(c);});
async function go(){
  const p=document.getElementById('prompt').value, t=document.getElementById('tenant').value;
  const r=await fetch('/build?prompt='+encodeURIComponent(p)+'&tenant='+encodeURIComponent(t));
  document.getElementById('out').srcdoc=await r.text();
}
document.getElementById('prompt').addEventListener('keydown',e=>{if(e.key==='Enter')go();});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, body: str, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(_PAGE)
        if u.path == "/build":
            # !!! NO AUTH/TENANT CHECK — see module docstring (finding 31,
            # PR #7999). `tenant` here is a raw, unauthenticated query-string
            # value, and `route()` below can override it again from
            # NL-extracted text in `prompt` — neither is checked against the
            # caller's actual tenant membership before the Gold query below
            # runs. Loopback-only binding (see main()) is the sole guard;
            # do not expose this handler off 127.0.0.1 without adding a
            # real authenticated-caller -> allowed-tenant(s) check first.
            q = parse_qs(u.query)
            prompt = (q.get("prompt") or [""])[0]
            tenant = (q.get("tenant") or [""])[0].strip() or None
            spec = route(prompt, tenant)
            return self._send(render_dashboard(spec))
        if u.path == "/health":
            return self._send(json.dumps({"ok": True}), "application/json")
        self.send_response(404)
        self.end_headers()


def main() -> int:
    port = int(os.environ.get("DASH_PORT", "8800"))
    if not os.environ.get("MONGO_URI"):
        print("WARNING: MONGO_URI not set — dashboards will error on query "
              "(set it / ensure .env.local present).", file=sys.stderr)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Gold dashboard UI on http://localhost:{port}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
