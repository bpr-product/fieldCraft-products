import logging
import os
import sys
import uvicorn

sys.path.append("source/.")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

if __name__ == "__main__":
    reload_flag = (os.getenv("UVICORN_RELOAD", "false") or "").strip().lower() in ("1", "true", "yes", "on")
    uvicorn.run(
        "source.api.MeasurementServiceAPI:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5001")),
        reload=reload_flag,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_config=None,
    )



