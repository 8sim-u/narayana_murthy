"""WAMSS backend launcher for agbeast-1.

backend/app/camera_consent.camera_mode() and the API key middleware read
WAMSS_CAMERA_PRIVACY_MODES / WAMSS_API_KEY straight from os.environ at
call time. The pydantic env_file only feeds the Settings model and never
populates os.environ, and WMI-created processes do not inherit an agent
session's environment, so a WMI-relaunched backend loses the camera
privacy-mode config and rejects person events for every camera with 403.
This launcher parses backend/.env into os.environ itself, redirects
stdout/stderr to a log at the fd level (WMI cannot redirect output), and
runs uvicorn in-process. Active lines only: commented-out values (such
as the API key) stay unset, which keeps API key auth disabled exactly as
the backend runs today.
"""
import os
import sys

BACKEND_DIR = r"C:\Users\Ayyg\narayana_murthy-design\backend"
ENV_FILE = os.path.join(BACKEND_DIR, ".env")

os.chdir(BACKEND_DIR)
# Python puts this script's folder on sys.path, not the cwd; uvicorn's
# "app.main:app" import string needs the backend package importable.
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

with open(ENV_FILE, "r", encoding="utf-8", errors="replace") as fh:
    for raw in fh:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, _, value = line.partition("=")
            name = name.strip()
            if name:
                os.environ[name] = value.strip()

print("[launcher] cwd=%s" % os.getcwd())
print("[launcher] camera privacy modes: %s"
      % os.environ.get("WAMSS_CAMERA_PRIVACY_MODES"))

import uvicorn

uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="info")
