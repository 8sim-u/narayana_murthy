"""WAMSS detection health service (port 8001).

The vision pipeline (vision/multicamera.py) runs as a long-lived process
without its own HTTP surface. This small FastAPI service gives the edge
node a health check: GET /health answers 200 while the node is up, and
GET /status reports what the node can observe about itself: configured
cameras, backend reachability, YOLO weights presence, and the freshness
of the pipeline log when WAMSS_PIPELINE_LOG is set.

It does not stream video and never touches camera frames; it reads
process-level facts only. Start it with the same launcher pattern as the
backend: a run_detection.py launcher parses the vision env file
(run-vision.cmd) into os.environ, then runs uvicorn in-process on
127.0.0.1:8001.
"""
import json
import os
import time

import requests
from fastapi import FastAPI

SERVICE = "WAMSS detection"
_STARTED_AT = time.time()
_DEFAULT_MODEL = "yolov8n.pt"


def _backend_base() -> str:
    """Backend base URL derived from WAMSS_BACKEND_URL (strips /event)."""
    base = os.environ.get("WAMSS_BACKEND_URL", "").strip()
    if base.endswith("/event"):
        base = base[: -len("/event")]
    return base.rstrip("/")


def _cameras() -> dict:
    """Camera map from WAMSS_CAMERAS, or {} when unset or unparseable."""
    raw = os.environ.get("WAMSS_CAMERAS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _model_path() -> str:
    return os.environ.get("WAMSS_MODEL", "").strip() or _DEFAULT_MODEL


def _pipeline_log_facts() -> dict:
    """Size/mtime/age of the pipeline log when WAMSS_PIPELINE_LOG is set."""
    path = os.environ.get("WAMSS_PIPELINE_LOG", "").strip()
    if not path:
        return {}
    try:
        stat = os.stat(path)
    except OSError:
        return {"pipeline_log": path, "pipeline_log_exists": False}
    return {
        "pipeline_log": path,
        "pipeline_log_exists": True,
        "pipeline_log_bytes": stat.st_size,
        "pipeline_log_age_seconds": round(max(0.0, time.time() - stat.st_mtime), 1),
    }


app = FastAPI(title=SERVICE, version="1.0.0")


@app.get("/health")
def health():
    """Liveness probe: 200 while the detection node process is up."""
    return {"status": "online", "service": SERVICE}


@app.get("/status")
def status():
    """Observed node status: cameras, backend reachability, model, log age."""
    backend_base = _backend_base()
    backend_reachable = None
    if backend_base:
        try:
            response = requests.get(backend_base + "/health", timeout=1.5)
            backend_reachable = response.status_code == 200
        except requests.RequestException:
            backend_reachable = False
    model_path = _model_path()
    payload = {
        "status": "online",
        "service": SERVICE,
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        "cameras": _cameras(),
        "backend_url": backend_base or None,
        "backend_reachable": backend_reachable,
        "model_path": model_path,
        "model_present": os.path.exists(model_path),
        "headless": os.environ.get("WAMSS_HEADLESS", "") == "1",
        "privacy_mode": os.environ.get("WAMSS_PRIVACY_MODE", "") == "1",
    }
    payload.update(_pipeline_log_facts())
    return payload


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
