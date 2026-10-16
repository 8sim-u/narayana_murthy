# Local Setup — Verified Working

These are the exact steps that were run and verified on Windows (Python 3.13.14), 16 Oct 2026.
The backend installs, tests green, and serves live traffic. No PostgreSQL required for local work.

## 1. Environment

```bash
# Isolated virtual environment
"C:/Users/Ayyg/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe" -m venv "C:/Users/Ayyg/.workbuddy-ai/binaries/python/envs/wamss"

# Install backend dependencies (32 packages)
cd "D:/NARAYN MURTY/backend"
"C:/Users/Ayyg/.workbuddy-ai/binaries/python/envs/wamss/Scripts/python.exe" -m pip install -r requirements.txt
```

> If a package reports `Could not find a version ... (from versions: none)`, that is a transient
> index error, not a bad pin. Re-run the install — every version in `requirements.txt` resolves.

## 2. Database

PostgreSQL is the production target, but the API now boots without it.

`backend/.env` (already created, gitignored):

```env
APP_NAME=narayana-murthy-surveillance-api
ENVIRONMENT=development
DATABASE_URL=sqlite:///./wamss_dev.db
```

For PostgreSQL, swap the last line:

```env
DATABASE_URL=postgresql+psycopg://surveillance_app:PASSWORD@localhost:5432/surveillance_dev
```

`app/database.py` detects a `sqlite` URL and adds `check_same_thread=False`; the PostgreSQL path is
unchanged.

## 3. Tests

```bash
cd "D:/NARAYN MURTY/backend"
"C:/Users/Ayyg/.workbuddy-ai/binaries/python/envs/wamss/Scripts/python.exe" -m pytest -v
# 12 passed
```

Tests default to a throwaway SQLite database. `tests/conftest.py` no longer contains a hardcoded
credential — if `DATABASE_URL` is unset it falls back to `sqlite:///./test_wamss.db`.

## 4. Run the API

```bash
cd "D:/NARAYN MURTY/backend"
"C:/Users/Ayyg/.workbuddy-ai/binaries/python/envs/wamss/Scripts/python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive docs at `http://localhost:8000/docs`.

## 5. Smoke test

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/db
curl -X POST http://localhost:8000/event -H 'Content-Type: application/json' \
  -d '{"camera_id":"CAM_01","track_id":"12","objects":["person"],"confidence":0.91,"zone":"RESTRICTED"}'
curl "http://localhost:8000/events?limit=5"
```

Expected: `REVIEW_REQUIRED` with `RESTRICTED_ZONE_DETECTED` for the restricted-zone event, and the
row returned by `/events`.

## 6. The vision half — NOT installed

`vision/` requires `ultralytics`, `opencv-python`, `torch`, and `requests`. None of these are
declared in any requirements file, and importing the modules from a clean install fails with
`ModuleNotFoundError`. It was deliberately not installed here:

- the models and runtime are several GB;
- `multicamera.py` points at two hardcoded private-LAN phone streams and a hardcoded bridge host
  that are not reachable from this machine;
- it opens GUI windows via `cv2.imshow`, which needs a desktop session.

To reproduce it later, add a `vision/requirements.txt` and replace the hardcoded addresses in
`vision/multicamera.py` with configuration.

## Files changed during install

| File | Change |
|---|---|
| `backend/app/database.py` | SQLite support (`check_same_thread=False`); PostgreSQL path untouched |
| `backend/.env` | Created (gitignored) — SQLite local-dev URL |
| `backend/tests/conftest.py` | Removed hardcoded DB credential; tests default to SQLite |
| `docs/FUTURE_ROADMAP.html` | New — forward-looking assessment |
| `docs/LOCAL_SETUP.md` | New — this file |

No other source file was modified.
