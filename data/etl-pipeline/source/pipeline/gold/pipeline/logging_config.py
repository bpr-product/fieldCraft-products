"""Structured logging configuration for the Gold pipeline.

Public API
----------
configure_logging(level, *, use_colour)
    Call once at process start.  Installs a rich handler on the root logger.

set_execution_context(feature, tenant, step)
    Tag the current thread with up to three context fields.  Any combination
    of the three may be supplied; omitted fields are left unchanged.

clear_execution_context(*fields)
    Clear specific fields (or all fields when called with no arguments).

execution_context(feature, tenant, step)  — context manager
    Sets fields on entry, restores the previous values on exit.
    Compose at multiple levels:
        pipeline_runner  → execution_context(tenant=tenant)
        tenant_runner    → execution_context(step=name)        # nests inside tenant ctx
    Both levels produce a fully qualified [feature/tenant/step] tag in every
    log line emitted while the inner block is active.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Generator


# ── thread-local execution context ──────────────────────────────────────────

@dataclass
class _Ctx:
    feature: str = ""
    tenant: str = ""
    step: str = ""


_tl = threading.local()


def _get() -> _Ctx:
    if not hasattr(_tl, "ctx"):
        _tl.ctx = _Ctx()
    return _tl.ctx


def set_execution_context(
    feature: str = "",
    tenant: str = "",
    step: str = "",
) -> None:
    """Set one or more context fields on the calling thread.

    Only fields with non-empty values are updated; pass an explicit empty
    string to clear a field, or use clear_execution_context().
    """
    c = _get()
    if feature:
        c.feature = feature
    if tenant:
        c.tenant = tenant
    if step:
        c.step = step


def clear_execution_context(*fields: str) -> None:
    """Clear context fields.  No arguments clears all three fields."""
    c = _get()
    for f in (fields or ("feature", "tenant", "step")):
        setattr(c, f, "")


@contextmanager
def execution_context(
    feature: str = "",
    tenant: str = "",
    step: str = "",
) -> Generator[None, None, None]:
    """Context manager — sets fields on entry, restores previous values on exit."""
    c = _get()
    prev = asdict(c)
    if feature:
        c.feature = feature
    if tenant:
        c.tenant = tenant
    if step:
        c.step = step
    try:
        yield
    finally:
        c.feature = prev["feature"]
        c.tenant  = prev["tenant"]
        c.step    = prev["step"]


# ── logging filter: injects context into every LogRecord ────────────────────

class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        c = _get()
        record.ec_feature = c.feature  # type: ignore[attr-defined]
        record.ec_tenant  = c.tenant   # type: ignore[attr-defined]
        record.ec_step    = c.step     # type: ignore[attr-defined]
        return True


# ── formatter ────────────────────────────────────────────────────────────────

_COLOURS: dict[str, str] = {
    "DEBUG":    "\033[36m",   # cyan
    "INFO":     "\033[32m",   # green
    "WARNING":  "\033[33m",   # yellow
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[35m",   # magenta
}
_RST = "\033[0m"


class PipelineFormatter(logging.Formatter):
    """Single-line formatter with optional ANSI colour and execution context tag.

    Output layout:
        <timestamp>.<ms> | <LEVEL>   | <logger-name>                   | [ctx] <message>

    Example:
        2026-06-04 09:05:12.345 | INFO     | pipeline.tenant_runner       | [bpe/DEMO-001/load_estimate_snapshot] ✓ 2.4s
        2026-06-04 09:05:12.346 | DEBUG    | bpe.load_estimate_snapshot   | [bpe/DEMO-001/load_estimate_snapshot] querying silver.project

    The [ctx] tag is omitted when all three fields are empty.
    Stack traces are appended after the message line when exc_info is set.
    """

    def __init__(self, *, use_colour: bool = True) -> None:
        super().__init__()
        self._colour = use_colour

    def format(self, record: logging.LogRecord) -> str:
        ts  = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        ms  = f"{record.msecs:03.0f}"

        lvl = record.levelname
        lvl_s = (
            f"{_COLOURS.get(lvl, '')}{lvl:<8}{_RST}"
            if self._colour
            else f"{lvl:<8}"
        )

        # Strip leading "gold." to keep name columns narrow
        name = record.name[5:] if record.name.startswith("gold.") else record.name
        name_s = f"{name:<32}"

        # Build [feature/tenant/step] context tag — only include non-empty fields
        parts = [
            p for p in (
                getattr(record, "ec_feature", ""),
                getattr(record, "ec_tenant",  ""),
                getattr(record, "ec_step",    ""),
            )
            if p
        ]
        ctx = f"[{'/'.join(parts)}] " if parts else ""

        line = f"{ts}.{ms} | {lvl_s} | {name_s} | {ctx}{record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            line += "\n" + self.formatStack(record.stack_info)
        return line


# ── public setup ─────────────────────────────────────────────────────────────

def configure_logging(
    level: str | None = None,
    *,
    use_colour: bool | None = None,
) -> None:
    """Configure Gold pipeline logging.  Call once at process startup.

    May be called a second time with a new level (e.g. after CLI arg parsing)
    — it clears existing handlers before installing the new one.

    Args:
        level:      Log level string (DEBUG / INFO / WARNING / ERROR).
                    Falls back to LOG_LEVEL env var, then INFO.
        use_colour: Force colour on/off.  Auto-detected from stderr.isatty() when None.
    """
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")
    numeric = getattr(logging, level.upper(), logging.INFO)

    if use_colour is None:
        use_colour = sys.stderr.isatty()

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_ContextFilter())
    handler.setFormatter(PipelineFormatter(use_colour=use_colour))

    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(numeric)
