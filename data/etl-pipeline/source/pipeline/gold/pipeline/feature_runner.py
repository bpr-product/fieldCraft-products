"""Per-feature Gold pipeline subprocess entry point.

Used by ``run_pipeline.py --isolate-features`` to run each feature in an
isolated subprocess, so a crash or OOM in one feature cannot abort others.

Usage (direct):
  python -m gold.pipeline.feature_runner --feature bpe --all-tenants
  python -m gold.pipeline.feature_runner --feature eden --tenant DEMO-001 --dry-run

The module simply delegates to ``gold.run_pipeline.main`` with any arguments
passed on the command line. Keeping it as a thin wrapper means a subprocess can
be spawned with ``sys.executable + ["-m", "gold.pipeline.feature_runner"] + argv``
and the exit code propagates back to the parent exactly like run_pipeline.py.

Exit codes mirror run_pipeline.py:
  0  all loaders for the feature succeeded
  1  one or more loaders failed
  2  configuration / connection error
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED = str(_REPO_ROOT / "shared")
for _p in (str(_REPO_ROOT), _SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gold.run_pipeline import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
