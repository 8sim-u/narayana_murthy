# narayana-murthy-surveillance

An AI-powered workspace monitoring prototype designed to track occupancy, activity, and productivity metrics across modern work environments. The platform employs a simulation-first approach to validate event pipelines, detection heuristics, and analytics flows prior to integrating live camera feeds and physical sensor hardware.

## Team Split

- **Person A**: Computer Vision (detection, tracking, camera pipelines, model inference)
- **Person B**: Backend, database, dashboard, and end-to-end integration

## Planned Stack

- **Backend**: FastAPI ✅ implemented
- **Database**: PostgreSQL ✅ implemented (SQLite supported for local development)
- **Frontend / Dashboard**: React + TypeScript ⬜ not started

## Folder Structure

```
narayana-murthy-surveillance/
├── backend/    # Server application, API routes, database models, and event handlers
├── frontend/   # Web dashboard and UI visualizations (React + TypeScript)
├── shared/     # Shared schemas, contracts, utilities, and simulation data
├── docs/       # Architecture documents, API specifications, and setup guides
├── vision/     # Edge-node computer vision pipeline (YOLOv8 + ByteTrack)
├── .gitignore  # Git ignore definitions
└── README.md   # Project overview and guidelines
```

---

## Quick Start

Runs on Windows, macOS, and Linux. **PostgreSQL is not required** for local development —
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

---

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

The edge node lives in `vision/`. It requires a separate, much heavier dependency set —
see [`vision/requirements.txt`](vision/requirements.txt):

```bash
pip install -r vision/requirements.txt
python vision/multicamera.py
```

> **Not yet verified against a live camera.** `vision/multicamera.py` currently contains
> hardcoded camera stream URLs and a hardcoded backend host that must be changed for your
> network. It also uses `cv2.imshow`, so it needs a desktop session. See
> [Known Gaps](#known-gaps) below.

---

## Architectural & Project Rules

1. **Dashboard Data Access**: The dashboard never talks to the database directly; all access is routed through backend APIs.
2. **Event Attribution**: Simulated events must be labelled with `source="simulation"` to maintain separation from real-world camera detections. *(not yet implemented — see Known Gaps)*
3. **Security & Secrets**: No secrets in git (passwords, tokens, and credentials must live in environment files or secret managers).

---

## Known Gaps

Read [`docs/FUTURE_ROADMAP.html`](docs/FUTURE_ROADMAP.html) for the full assessment. The
highest-priority items:

1. **A database credential was previously committed** in `backend/tests/conftest.py` and still
   exists in git history. It has been removed from the working tree, but **the password must be
   rotated** — removing it from the file does not invalidate it.
2. **No authentication on any endpoint.** Anyone who can reach the port can inject events and
   read the full detection history.
3. **Rule 2 above is unimplemented.** No `source` field exists in the schema, model, or database,
   so simulated events cannot be distinguished from real detections.
4. **`vision/bridge.py` duplicates the backend.** It re-implements `POST /event` with an
   in-memory buffer capped at 1000 events, and the edge node points at it rather than at the
   real backend. It should be removed in favour of a single ingestion path.
5. **`frontend/` and `shared/` are empty.** No dashboard exists yet, and there is no simulation
   harness despite the project being described as simulation-first.
6. **No migrations, CI, or containerisation.** Tables are created with `create_all`, so the first
   schema change will be a manual deploy.

## Status

Backend implemented and tested (12 tests passing). Vision pipeline prototyped and connected to
the backend. Dashboard, shared simulation harness, and documentation not yet started.
