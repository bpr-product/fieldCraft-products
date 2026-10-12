"""
Senior-review Priority 1 (2026-08-01): mechanical enforcement that
`project_health.margin`/`estimatedCost` can only ever be read from ONE
place -- `resolve_margin_response` in source/agents/insight_agent.py.

This is not a style preference. Before this consolidation, InsightAgent
and ReportingAgent each independently queried Mongo for margin and each
independently called `role_resolver.is_admin(...)` -- two copies of the
same security-critical gate, with nothing stopping a THIRD future
response path from querying `margin` directly and forgetting the check.
Python has no type-level way to forbid a second unguarded read site, so
this test is the enforcement: if anyone adds a new `"margin": 1` (or
`.get("margin")`) Mongo projection/read anywhere in source/agents/ outside
`resolve_margin_response`, this test fails the suite immediately, forcing
them to either call the shared function or consciously override this test
(which requires explaining why in the diff, not silently drifting).
"""
import re
from pathlib import Path

SOURCE_AGENTS_DIR = Path(__file__).parent.parent / "source" / "agents"

# The one function permitted to read `margin` from Mongo.
_BLESSED_FUNCTION = "resolve_margin_response"

# Matches a Mongo projection key or a dict .get() call for the margin
# field -- the two shapes every read site in this codebase actually uses.
_MARGIN_READ_PATTERN = re.compile(r'''["']margin["']\s*:\s*1|\.get\(\s*["']margin["']\s*\)''')


def _iter_function_bodies(source_text: str):
    """Yields (function_name, body_text) for every top-level or
    method-level `def` in the file, body text spanning from the `def` line
    to the next `def`/`class` at the same or shallower indent. Deliberately
    simple (regex/line-based, not an AST visitor) -- this only needs to
    answer "which named function does this line live inside," not build a
    general-purpose parser."""
    lines = source_text.splitlines()
    def_positions = []
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*)def\s+(\w+)\s*\(', line)
        if m:
            def_positions.append((i, len(m.group(1)), m.group(2)))
    for idx, (start_line, indent, name) in enumerate(def_positions):
        end_line = len(lines)
        for next_start, next_indent, _ in def_positions[idx + 1:]:
            if next_indent <= indent:
                end_line = next_start
                break
        yield name, "\n".join(lines[start_line:end_line])


def test_margin_is_only_read_inside_the_blessed_function():
    violations = []
    for py_file in SOURCE_AGENTS_DIR.glob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for func_name, body in _iter_function_bodies(text):
            if func_name == _BLESSED_FUNCTION:
                continue
            if _MARGIN_READ_PATTERN.search(body):
                violations.append(f"{py_file.name}::{func_name}")
    assert violations == [], (
        f"Found margin field read(s) outside {_BLESSED_FUNCTION}: {violations}. "
        f"Route margin access through resolve_margin_response instead of "
        f"querying it directly -- see that function's docstring for why "
        f"this is enforced mechanically, not just documented."
    )


def test_blessed_function_still_exists_and_is_importable():
    """Guards against the enforcement itself silently going stale if
    `resolve_margin_response` is ever renamed/removed without updating
    this test."""
    from source.agents.insight_agent import resolve_margin_response
    assert callable(resolve_margin_response)


def test_both_agents_delegate_to_the_shared_function():
    """Confirms InsightAgent and ReportingAgent's _margin_response methods
    are thin wrappers, not independent implementations -- the actual
    intent of the Priority-1 consolidation, not just "no direct Mongo
    read" (which the pattern-based test above already covers)."""
    import inspect

    from source.agents.insight_agent import InsightAgent
    from source.agents.reporting_agent import ReportingAgent

    insight_src = inspect.getsource(InsightAgent._margin_response)
    reporting_src = inspect.getsource(ReportingAgent._margin_response)
    assert "resolve_margin_response(" in insight_src
    assert "resolve_margin_response(" in reporting_src
