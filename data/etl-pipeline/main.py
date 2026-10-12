"""
Entry point for the data pipeline control-plane service — loads env, puts the ported
Bronze/Silver/External pipeline packages (source/pipeline/{bronze,silver,
external,shared,dlq_watcher,ingestion,config}) on sys.path so their existing
top-level cross-imports (e.g. "from shared.alerting.notify import ...",
"from dlq_watcher.state import RedisState") keep resolving unchanged, then
starts the Uvicorn ASGI server.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_PIPELINE_ROOT = Path(__file__).parent / "source" / "pipeline"
sys.path.insert(0, str(_PIPELINE_ROOT))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    reload = (os.getenv("UVICORN_RELOAD", "false") or "").strip().lower() in ("1", "true", "yes", "on")
    uvicorn.run("start:app", host="0.0.0.0", port=8030, reload=reload, workers=1)
