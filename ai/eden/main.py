import logging
import os

from dotenv import load_dotenv

# Must load before any project import — source.api.navigation_api pulls in
# numpy/OpenBLAS at import time (navigation_agent's TF-IDF vectorizer), which
# reads OPENBLAS_NUM_THREADS/OMP_NUM_THREADS from the environment at that
# point. Loading .env after that import is too late for those two vars.
load_dotenv()

from source.utils.startup_guards import (
    check_cors_wildcard_guard,
    check_mongo_placeholder_guard,
    check_rbac_bypass_guard,
)

check_rbac_bypass_guard()
check_cors_wildcard_guard()
check_mongo_placeholder_guard()

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from source.api.navigation_api import router
from source.utils.security_headers import SecurityHeadersMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s"
)

app = FastAPI(
    title="EDEN Navigation Agent",
    description="FIELDCRAFT Cloud AI Navigation Agent — POC",
    version="0.1.0"
)

# Real bug, fixed: the comment below previously claimed "CORS added first
# ends up outermost," but Starlette's add_middleware() INSERTS at the front
# of the list each time, so the LAST-added middleware ends up outermost --
# verified empirically by walking the actual built middleware stack.
# SecurityHeadersMiddleware.dispatch() returns a raw 413 directly (before
# calling call_next) when the body exceeds MAX_BODY_BYTES -- with the old
# order, that response never reached CORSMiddleware (which sat inside it),
# so an oversized-body rejection shipped with no CORS header at all and a
# cross-origin caller saw an opaque network failure instead of a readable
# 413. Add SecurityHeadersMiddleware FIRST so CORSMiddleware (added second)
# actually ends up outermost, matching the comment's original intent.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["POST", "GET"],
    allow_headers=["*"]
)

app.include_router(router)


@app.on_event("startup")
async def warm_up_llm():
    """Preload the LLM in the background so the first user message never
    pays the cold-start cost (local models unload when idle)."""
    import threading
    from source.utils.llm_client import warm_up
    threading.Thread(target=warm_up, daemon=True).start()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "eden-navigation-agent"}


if __name__ == "__main__":
    # reload defaults OFF: uvicorn's stat-watcher resolves every .py file each
    # cycle and, under heavy local CPU-LLM load, exhausts Windows file handles
    # (WinError 1450) and kills the server. Restart manually after code edits,
    # or set APP_RELOAD=true if you have resources to spare.
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("APP_PORT", "8020")),
        reload=os.getenv("APP_RELOAD", "false").lower() == "true",
    )
