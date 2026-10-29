# narayana-murthy-surveillance

An AI-powered workspace monitoring prototype designed to track occupancy, activity, and productivity metrics across modern work environments. The platform employs a simulation-first approach to validate event pipelines, detection heuristics, and analytics flows prior to integrating live camera feeds and physical sensor hardware.

## Team Split

- **Person A**: Computer Vision (detection, tracking, camera pipelines, model inference)
- **Person B**: Backend, database, dashboard, and end-to-end integration

## Planned Stack

- **Backend**: FastAPI ✅ implemented
- **Database**: PostgreSQL ✅ implemented (SQLite supported for local development)
- **Frontend / Dashboard**: React + TypeScript ✅ implemented 2026-10-30 (Vite)

## Folder Structure

```
narayana-murthy-surveillance/
├── agent/      # Workstation activity logger (smartwatch-style, always-on service)
├── backend/    # Server application, API routes, database models, and event handlers
├── frontend/   # Web dashboard and UI visualizations (React + TypeScript)
├── shared/     # Shared schemas, contracts, utilities, and simulation data
├── docs/       # Architecture documents, API specifications, and setup guides
├── vision/     # Edge-node computer vision pipeline (YOLOv8 + ByteTrack)
├── .gitignore  # Git ignore definitions
└── README.md   # Project overview and guidelines
```

---

## What was completed 2026-10-30

- **Backend**: tests re-run 2026-10-30 on Linux, all passing (12 passed, the count already recorded in Quick Start step 4). Final numbers are being reconciled by the coordinator.
- **Frontend / Dashboard**: React + TypeScript dashboard built with Vite (see `frontend/README.md`): backend health panel, live events table with polling, restricted-zone alert highlighting, camera filter, and a test-event form. Wired to the real backend API; the API base URL is configurable via `VITE_API_URL` (`frontend/.env.example`).
- **Vision config**: camera stream URLs, backend host, model path, and event interval moved from hardcoded values to `WAMSS_*` environment variables; `cv2.imshow` is now headless-safe. Not yet verified against a live camera.
- **Simulation harness**: `shared/simulate.py` seeds the backend with realistic demo data (see the Deploy section).
- **Workstation activity module** (new 2026-10-30): a smartwatch-style always-on logger in `agent/` records active app/window/process, start/end timestamps, input counts (never keystroke content), idle periods, and a heuristic strain score, with local JSONL spooling and batched sync with backoff. Optional BLE smartwatch heart rate feeds the strain score. First-run consent gate, privacy exclusion list, USB/file-write DLP signals, break reminders with snooze. Backend gained `POST/GET /activity`, `GET /activity/strain-summary`, `GET /presence` (camera + workstation correlation), `GET /focus/daily`, `GET /focus/streaks` (focus score + gamification streaks), `POST /security/events`, `GET /security/alerts`, `GET /policy/rules`, `GET /policy/violations`, plus a weekly digest script (`backend/scripts/weekly_digest.py`). The dashboard has new tabs: Workstation Activity, Presence, Focus Score, Security, Policy, Team. Full pytest suite: 52 passed. See `agent/README.md` and `docs/COMPLETION_2026-10-30.md`.
- **Employee notice and consent:** deployments of the workstation agent must include employee notice and consent per applicable workplace law before installing it on any workstation.
- **Round 4 (2026-10-30, literature-driven build):** ML stress dynamics engine (`agent/ml_stress.py`, 27 keystroke/mouse features, 60 s windows, per-user baselines, unsupervised score + supervised training CLI `agent/train.py`), session fatigue detection (`agent/fatigue.py`, k-NN per Acien 2022), behavioral/HR fusion (`agent/fusion.py`, early + late with missing-modality masking) wired into the agent behind `WAMSS_STRAIN_PIPELINE=1`, privacy-preserving vision mode (`WAMSS_PRIVACY_MODE`, in-memory frames, aggregates only, optional MediaPipe), USB anomaly upgrade (first-seen serials, allowlist, BadUSB heuristic, off-hours mounts) plus CERT-style hours baselining (`GET /hours/baseline`, `GET /hours/anomaly`), and a full deployment pack (`deploy/`: one-command deploy, agent installers, preflight, backup/restore, runbook in `docs/DEPLOYMENT_PACK.md`). Backend also gained `ml_stress_score` on activity records and `GET /activity/strain-summary`.

## Quick Start

Runs on Windows, macOS, and Linux. **PostgreSQL is not required** for local development -
the API falls back to SQLite.

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/8sim-u/narayana_murthy.git
cd narayana_murthy/backend

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure the environment

```bash
cp .env.example .env
```

The default `.env.example` already points at SQLite, so no further edits are needed to get
started. To use PostgreSQL instead, set:

```env
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/DBNAME
```

`.env` is gitignored. Never commit real credentials.

### 4. Run the tests

```bash
pytest -v
```

Expect **12 passed**. The suite uses a throwaway SQLite database and needs no services running.

### 5. Start the API

```bash
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive API docs: <http://localhost:8000/docs>

### 6. Smoke test

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/event \
  -H 'Content-Type: application/json' \
  -d '{"camera_id":"CAM_01","track_id":"12","objects":["person"],"confidence":0.91,"zone":"RESTRICTED"}'

curl "http://localhost:8000/events?limit=5"
```

The restricted-zone event should return `REVIEW_REQUIRED` with `RESTRICTED_ZONE_DETECTED`.

### 7. Demo without hardware (optional)

No cameras or workstation agents needed. Seed the running backend with
realistic simulated telemetry, then explore the full dashboard:

```bash
python3 shared/simulate.py --dry-run   # preview, sends nothing
python3 shared/simulate.py             # seed 5 employees x 7 workdays
```

Point the dashboard at the same API (`VITE_API_URL` in `frontend/.env`).
Build a Monday summary from the seeded data with
`python3 backend/scripts/weekly_digest.py`. See `shared/README.md`.

---

## Deploy

One-command production-style deploy (FastAPI backend + React dashboard via
Docker). See `docs/DEPLOYMENT_PACK.md` for the full runbook.

```bash
bash deploy/preflight.sh   # readiness checklist (or .\deploy\preflight.ps1)
bash deploy/deploy.sh      # build, start, wait for health  (or .\deploy\deploy.ps1)
```

On the first run, `deploy.sh` creates `.env` from `.env.example` and
generates a random `WAMSS_API_KEY` (printed once; saved in `.env`, never
committed). Re-runs are safe: they rebuild and restart the stack while
preserving `.env` and the data volumes.

Then:

- Dashboard: http://localhost:8080
- Backend: http://localhost:8000 (`/health`, `/docs`)

Verify with a seeded demo (uses `WAMSS_API_KEY` from `.env` automatically):

```bash
export WAMSS_API_KEY=$(grep '^WAMSS_API_KEY=' .env | cut -d= -f2)
python3 shared/simulate.py --api-url http://localhost:8000 --employees 5 --days 7
```

Install the workstation agent (Linux / Windows, admin):

```bash
bash deploy/install-agent.sh
```

```powershell
.\deploy\install-agent.ps1
```

Back up the database nightly:

```bash
bash deploy/backup.sh                 # timestamped archive to ./backups/
bash deploy/restore.sh <archive>      # restore (replaces current data)
```

More detail: `docs/DEPLOY.md` (Docker reference), `docs/AUTH.md`,
`docs/ALERTS.md`, `agent/README.md` (consent, BLE pairing).

## Architecture

```
Windows Edge Node (YOLOv8 + ByteTrack)
        |
        |  HTTP POST /event (JSON)
        v
FastAPI Backend  -->  RuleEngine  -->  PostgreSQL
  /health  /event  /events  /events/{id}
```

- **Framework**: FastAPI with asynchronous request processing
- **Validation**: Pydantic v2 schemas (`app/schemas.py`)
- **Persistence**: SQLAlchemy 2.0 ORM (`app/models.py`, `app/database.py`)
- **Rules Engine**: decoupled decision evaluator (`app/rules.py`)
- **Server**: Uvicorn ASGI, binds `0.0.0.0` for LAN access from edge nodes

Full API reference: [`backend/README.md`](backend/README.md)

---

## Vision Pipeline (Person A)

The edge node lives in `vision/`. It requires a separate, much heavier dependency set -
see [`vision/requirements.txt`](vision/requirements.txt):

```bash
pip install -r vision/requirements.txt
python vision/multicamera.py
```

> **Not yet verified against a live camera.** Camera stream URLs, the backend host,
> model path, and timing are now read from `WAMSS_*` environment variables (see
> `vision/.env.example`) and `cv2.imshow` is headless-safe, but the pipeline has never run
> against real hardware. See [Known Gaps](#known-gaps) below.

---

## Architectural & Project Rules

1. **Dashboard Data Access**: The dashboard never talks to the database directly; all access is routed through backend APIs.
2. **Event Attribution**: Simulated events must be labelled with `source="simulation"` to maintain separation from real-world camera detections. *(not yet implemented - see Known Gaps)*
3. **Security & Secrets**: No secrets in git (passwords, tokens, and credentials must live in environment files or secret managers).

---

## Known Gaps

Read [`docs/FUTURE_ROADMAP.html`](docs/FUTURE_ROADMAP.html) for the full assessment. The
highest-priority items:

1. **A database credential was previously committed** in `backend/tests/conftest.py` and still
   exists in git history. It has been removed from the working tree, but **the password must be
   rotated** - removing it from the file does not invalidate it.
2. **API key auth is implemented** (`X-API-Key` header, `WAMSS_API_KEY`; see `docs/AUTH.md`), but there is no per-user auth or role model. Anyone with the key has full access.
3. **Rule 2 above is unimplemented.** No `source` field exists in the schema, model, or database,
   so simulated events cannot be distinguished from real detections.
4. **`shared/` simulation harness exists** (`shared/simulate.py`), but the project has no
   recorded/archived demo dataset checked in.
5. **No migrations.** Tables are created with `create_all`, so schema changes need manual
   `ALTER TABLE` on existing databases (documented per change). CI (pytest on push/PR) and
   Docker packaging (`docker-compose.yml`) are in place.

## Status (updated 2026-10-30)

- Backend implemented and tested: full pytest suite green (93 passed after round 3; round 4 added ML/fatigue/fusion/USB/hours modules with new tests, see `docs/COMPLETION_2026-10-30.md`).
- Vision pipeline prototyped and configurable via `WAMSS_*` environment variables (no hardcoded camera URLs), with an optional privacy-preserving mode (`WAMSS_PRIVACY_MODE`), but **never verified against a live camera**.
- Frontend dashboard built (React + TypeScript, Vite) and wired to the real backend API: health panel, live events table with polling, restricted-zone alert highlighting, camera filter, test-event form, plus tabs for Workstation Activity, Presence, Focus Score, Security, Policy, Team, and Analytics.
- Workstation activity module (`agent/`): smartwatch-style always-on logger with heuristic strain, break reminders, optional BLE heart rate, consent gate, privacy exclusion list, USB/file-write DLP signals; round 4 added an opt-in ML pipeline (`WAMSS_STRAIN_PIPELINE=1`): literature-based keystroke/mouse dynamics (`agent/ml_stress.py`), session fatigue detection, behavioral/HR fusion.
- Deployment: one-command Docker deploy (`deploy/deploy.sh` / `.ps1`), workstation installers, preflight checks, backup/restore, full runbook in `docs/DEPLOYMENT_PACK.md`.
- Open items: per-user auth/roles, no DB migrations (manual ALTER TABLE documented), DB credential rotation still owed, live-camera verification, BLE pairing and SMTP setup on a real machine, Windows WMI/USB paths untested on this Linux VM.
