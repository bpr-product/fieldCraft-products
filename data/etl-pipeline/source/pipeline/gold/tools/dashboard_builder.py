"""Reusable self-contained HTML dashboard/report builder for the Gold layer.

Renders a dashboard from a plain spec dict into a single .html file with inline
CSS + inline SVG charts (no external/CDN dependencies — fully self-contained and
offline-viewable). Used for prompt-driven Gold validation: each business prompt
becomes a spec, and whether the spec can be filled from Gold IS the validation.

Data access (`query_gold`) is read-only against the live Gold Cosmos cluster
(MONGO_URI). The renderer is data-source-agnostic — a spec can equally be filled
from loaders' build_documents() output.

Spec shape:
    {
      "title": str,
      "subtitle": str | None,
      "cards":  [{"label": str, "value": str, "sub": str | None, "tone": "ok|warn|bad|none"}],
      "charts": [{"title": str, "type": "bar|line", "labels": [str], "values": [float], "unit": str | None}],
      "tables": [{"title": str, "columns": [str], "rows": [[cell, ...]]}],
      "notes":  [str],          # caveats / gaps surfaced while building
    }

render_dashboard(spec) -> html str
write_dashboard(spec, out_path) -> path
query_gold(db, collection, pipeline) -> list[dict]   (read-only aggregation)
"""
from __future__ import annotations

import html
import os
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# --------------------------------------------------------------------------- #
# Data access (read-only)
# --------------------------------------------------------------------------- #
def query_gold(db: str, collection: str, pipeline: list[dict]) -> list[dict]:
    """Run a read-only aggregation against a live Gold collection."""
    from pymongo import MongoClient

    uri = os.environ.get("MONGO_URI")
    if not uri:
        raise RuntimeError("MONGO_URI not set (load .env.local)")
    cli = MongoClient(uri, serverSelectionTimeoutMS=15_000, appname="gold-dashboard")
    try:
        return list(cli[db][collection].aggregate(pipeline))
    finally:
        cli.close()


# --------------------------------------------------------------------------- #
# Rendering helpers (inline SVG — no external deps)
# --------------------------------------------------------------------------- #
def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _bar_chart_svg(labels: list[str], values: list[float], unit: str | None) -> str:
    if not values:
        return '<p class="empty">no data</p>'
    w, h, pad, top = 760, 260, 40, 20
    mx = max(values) or 1
    n = len(values)
    bw = (w - 2 * pad) / n * 0.62
    gap = (w - 2 * pad) / n
    bars, labs = [], []
    for i, (lb, v) in enumerate(zip(labels, values)):
        bh = (h - pad - top) * (v / mx)
        x = pad + i * gap + (gap - bw) / 2
        y = h - pad - bh
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" class="bar"/>'
            f'<text x="{x + bw/2:.1f}" y="{y - 6:.1f}" class="bval" text-anchor="middle">{_esc(_fmt(v))}</text>'
        )
        labs.append(
            f'<text x="{x + bw/2:.1f}" y="{h - pad + 16:.1f}" class="axl" text-anchor="middle">{_esc(lb)}</text>'
        )
    u = f' ({_esc(unit)})' if unit else ""
    return (
        f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">'
        f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" class="axis"/>'
        f'{"".join(bars)}{"".join(labs)}'
        f'<text x="{pad}" y="14" class="axl">{u}</text></svg>'
    )


def _line_chart_svg(labels: list[str], values: list[float], unit: str | None) -> str:
    if not values:
        return '<p class="empty">no data</p>'
    w, h, pad, top = 760, 260, 40, 20
    mx = max(values) or 1
    n = len(values)
    step = (w - 2 * pad) / max(n - 1, 1)
    pts = []
    for i, v in enumerate(values):
        x = pad + i * step
        y = h - pad - (h - pad - top) * (v / mx)
        pts.append((x, y))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" class="dot"/>' for x, y in pts)
    labs = "".join(
        f'<text x="{pad + i*step:.1f}" y="{h - pad + 16:.1f}" class="axl" text-anchor="middle">{_esc(lb)}</text>'
        for i, lb in enumerate(labels)
    )
    u = f' ({_esc(unit)})' if unit else ""
    return (
        f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">'
        f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" class="axis"/>'
        f'<polyline points="{poly}" class="line"/>{dots}{labs}'
        f'<text x="{pad}" y="14" class="axl">{u}</text></svg>'
    )


def _fmt(v: Any) -> str:
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if isinstance(v, int) and abs(v) >= 1000:
            return f"{v:,}"
        if isinstance(v, float):
            return f"{v:,.2f}"
        return f"{v:,}"
    return _esc(v)


_CSS = """
:root{--bg:#f6f8fa;--panel:#ffffff;--line:#d8dee4;--txt:#1f2328;--mut:#656d76;
--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e;--accent:#0969da}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:20px 28px;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:20px}.sub{color:var(--mut);margin-top:4px}
.wrap{padding:24px 28px;max-width:1100px}
.cards{display:flex;flex-wrap:wrap;gap:14px;margin-bottom:24px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:16px 18px;min-width:170px;flex:1}
.card .lab{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.card .val{font-size:26px;font-weight:600;margin-top:6px}
.card .sub{font-size:12px;color:var(--mut);margin-top:2px}
.card.ok .val{color:var(--ok)}.card.warn .val{color:var(--warn)}.card.bad .val{color:var(--bad)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:18px;margin-bottom:20px}.panel h2{margin:0 0 12px;font-size:15px}
.chart{width:100%;height:auto}.bar{fill:var(--accent)}.line{fill:none;stroke:var(--accent);stroke-width:2}
.dot{fill:var(--accent)}.axis{stroke:var(--line)}.axl{fill:var(--mut);font-size:11px}
.bval{fill:var(--txt);font-size:11px}.empty{color:var(--mut);font-style:italic}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600;text-transform:uppercase;font-size:11px;letter-spacing:.03em}
.card{box-shadow:0 1px 2px rgba(31,35,40,.06)}.panel{box-shadow:0 1px 2px rgba(31,35,40,.06)}
.notes{background:#fff8e6;border:1px solid #eac54f;border-left:3px solid var(--warn);
border-radius:8px;padding:12px 16px;color:#54470c;font-size:13px}
.notes b{color:var(--warn)}footer{color:var(--mut);font-size:12px;padding:0 28px 24px}
"""


def render_dashboard(spec: dict) -> str:
    title = _esc(spec.get("title", "Gold Dashboard"))
    sub = spec.get("subtitle")
    parts = [f"<header><h1>{title}</h1>"]
    if sub:
        parts.append(f'<div class="sub">{_esc(sub)}</div>')
    parts.append("</header><div class='wrap'>")

    cards = spec.get("cards") or []
    if cards:
        parts.append('<div class="cards">')
        for c in cards:
            tone = c.get("tone", "none")
            tone = tone if tone in ("ok", "warn", "bad") else ""
            sub2 = f'<div class="sub">{_esc(c.get("sub"))}</div>' if c.get("sub") else ""
            parts.append(
                f'<div class="card {tone}"><div class="lab">{_esc(c.get("label"))}</div>'
                f'<div class="val">{_esc(c.get("value"))}</div>{sub2}</div>'
            )
        parts.append("</div>")

    for ch in spec.get("charts") or []:
        svg = (_line_chart_svg if ch.get("type") == "line" else _bar_chart_svg)(
            ch.get("labels", []), ch.get("values", []), ch.get("unit")
        )
        parts.append(f'<div class="panel"><h2>{_esc(ch.get("title"))}</h2>{svg}</div>')

    for tb in spec.get("tables") or []:
        cols = tb.get("columns", [])
        head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
        body = "".join(
            "<tr>" + "".join(f"<td>{_esc(_fmt(cell))}</td>" for cell in row) + "</tr>"
            for row in (tb.get("rows") or [])
        ) or f'<tr><td colspan="{max(len(cols),1)}" class="empty">no rows</td></tr>'
        parts.append(
            f'<div class="panel"><h2>{_esc(tb.get("title"))}</h2>'
            f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
        )

    notes = spec.get("notes") or []
    if notes:
        items = "".join(f"<div>• {_esc(n)}</div>" for n in notes)
        parts.append(f'<div class="notes"><b>Validation notes</b>{items}</div>')

    parts.append("</div><footer>FIELDCRAFT Data Lake — Gold layer · decision-support, not a quote.</footer>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>{_CSS}</style></head><body>"
        + "".join(parts)
        + "</body></html>"
    )


def write_dashboard(spec: dict, out_path: str) -> str:
    Path(out_path).write_text(render_dashboard(spec), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    # Smoke demo (sample data) — proves the UI renders without any DB.
    demo = {
        "title": "DEMO — Executive AR (sample)",
        "subtitle": "tenant SYN-DEMO-001 · sample data, not from Gold",
        "cards": [
            {"label": "Outstanding", "value": "$1,204,000", "tone": "warn"},
            {"label": "Overdue invoices", "value": "14", "tone": "bad"},
            {"label": "Projects", "value": "824"},
            {"label": "At risk", "value": "0", "tone": "ok"},
        ],
        "charts": [{"title": "Outstanding by aging bucket", "type": "bar",
                    "labels": ["0-30", "31-60", "61-90", "90+"],
                    "values": [420000, 380000, 240000, 164000], "unit": "USD"}],
        "tables": [{"title": "Top overdue", "columns": ["Project", "Amount", "Days late"],
                    "rows": [["SYN-P-3100", 88000, 47], ["SYN-P-3345", 52000, 31]]}],
        "notes": ["Sample data. Live version reads gold_eden / gold_invoice_aging.",
                  "AR aging requires gold_invoice_aging (currently unbuilt, WI #22043)."],
    }
    out = write_dashboard(demo, str(Path(os.environ.get("TMP", "/tmp")) / "gold_dashboard_demo.html"))
    print("wrote", out)
