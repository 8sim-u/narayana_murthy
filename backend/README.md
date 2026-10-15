# WAMSS Backend (Workplace Activity Monitoring & Surveillance System)

FastAPI-powered backend service for WAMSS. Ingests person-detection and tracking events from edge camera vision pipelines (e.g., Windows YOLOv8n / ByteTrack nodes), applies configurable workplace activity rules, persists events to PostgreSQL, and exposes query APIs for monitoring and future dashboard integrations.

---

## Architecture Overview

```
                      +---------------------------------------+
                      | Windows Vision Node (YOLOv8 + OpenCV) |
                      +---------------------------------------+
                                          |
                              HTTP POST /event (JSON)
                                          v
+---------------------------------------------------------------------------------+
| Mac Backend (0.0.0.0:8000)                                                      |
|                                                                                 |
|  +--------------------+        +---------------------+       +---------------+  |
|  |  FastAPI Endpoints | -----> |     RuleEngine      | ----> |  PostgreSQL   |  |
|  | /health, /event... |        | (app/rules.py)      |       | (SQLAlchemy)  |  |
|  +--------------------+        +---------------------+       +---------------+  |
|           |                                                                     |
|           v                                                                     |
|  +--------------------+                                                         |
|  | Dashboard Query API|                                                         |
|  |   GET /events      |                                                         |
|  +--------------------+                                                         |
+---------------------------------------------------------------------------------+
```

- **Framework**: FastAPI with asynchronous request processing
- **Validation**: Pydantic v2 schemas (`app/schemas.py`)
- **Persistence**: SQLAlchemy 2.0 ORM with PostgreSQL (`app/models.py`, `app/database.py`)
- **Rules Engine**: Decoupled, modular decision evaluator (`app/rules.py`)
- **Server**: Uvicorn ASGI server binding to `0.0.0.0` for local area network access

---

## Installation & Setup (macOS)

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.14)
- PostgreSQL running locally (port 5432)

### 2. Virtual Environment & Dependencies
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env` (never commit `.env` to Git):
```bash
cp .env.example .env
```
Ensure `DATABASE_URL` is set in `.env`:
```env
APP_NAME=narayana-murthy-surveillance-api
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg://surveillance_app:PASSWORD@localhost:5432/surveillance_dev
```

---

## Running the Server

To accept requests both locally and from external nodes (such as the Windows vision laptop) on the local Wi-Fi / LAN, bind to `0.0.0.0`:

```bash
cd backend
source .venv/bin/activate
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## API Endpoints

### 1. `GET /health`
Service liveness check.
- **Response `200 OK`**:
```json
{
  "status": "online",
  "service": "WAMSS backend",
  "environment": "development"
}
```

### 2. `GET /health/db`
Database connectivity verification (executes `SELECT 1`).
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "database": "connected"
}
```
- **Response `503 Service Unavailable`** (if database is down):
```json
{
  "status": "error",
  "database": "unavailable"
}
```

### 3. `POST /event`
Ingests detection events from vision nodes, runs rule evaluation, and persists to database.
- **Request Body**:
```json
{
  "camera_id": "CAM_01",
  "track_id": "12",
  "objects": ["person"],
  "confidence": 0.91,
  "zone": "RESTRICTED"
}
```
- **Response `201 Created` (Restricted Zone)**:
```json
{
  "received": true,
  "camera_id": "CAM_01",
  "alerts": ["RESTRICTED_ZONE_DETECTED"],
  "decision": "REVIEW_REQUIRED"
}
```
- **Response `201 Created` (Ordinary / Unknown Zone)**:
```json
{
  "received": true,
  "camera_id": "CAM_01",
  "alerts": [],
  "decision": "NO_RULE_TRIGGERED"
}
```
- **Validation Errors (`422 Unprocessable Entity`)**: Returned if confidence is outside `[0.0, 1.0]` or required fields are omitted.

### 4. `GET /events`
Query stored events in reverse chronological order with optional filtering and pagination.
- **Query Parameters**:
  - `limit` (default `50`, min `1`, max `1000`): Maximum events to return
  - `offset` (default `0`, min `0`): Pagination offset
  - `camera_id` (optional string): Filter by camera ID
  - `start_time` (optional ISO 8601 UTC): Filter events on or after timestamp
  - `end_time` (optional ISO 8601 UTC): Filter events on or before timestamp
- **Response `200 OK`**:
```json
[
  {
    "id": 11,
    "camera_id": "CAM_01",
    "track_id": "12",
    "objects": ["person"],
    "confidence": 0.91,
    "zone": "RESTRICTED",
    "decision": "REVIEW_REQUIRED",
    "alerts": ["RESTRICTED_ZONE_DETECTED"],
    "timestamp": "2026-10-14T00:25:08.975415Z"
  }
]
```

### 5. `GET /events/{event_id}`
Retrieves single event details. Returns `404 Not Found` if ID does not exist.

---

## Windows Vision Laptop Integration Guide

The Windows laptop running YOLOv8n / ByteTrack communicates with the Mac over the local Wi-Fi network.

### 1. Discover the Mac's Local IP Address
On the Mac terminal:
```bash
ipconfig getifaddr en0
```
*(Example: `10.5.102.16`)*

### 2. macOS Firewall & Network Check
1. Ensure macOS Firewall allows incoming connections to Python / Uvicorn:
   - System Settings > Network > Firewall > Options... Ensure Python is allowed.
2. Confirm both laptops are on the same Wi-Fi network (AP isolation disabled).
3. From Windows PowerShell, test network reachability:
   ```powershell
   Test-NetConnection -ComputerName "<MAC_IP>" -Port 8000
   ```

### 3. Testing from Windows PowerShell

#### Test Health Endpoint:
```powershell
Invoke-RestMethod -Uri "http://<MAC_IP>:8000/health" -Method Get
```

#### Post a Detection Event:
```powershell
Invoke-RestMethod `
  -Uri "http://<MAC_IP>:8000/event" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"camera_id":"CAM_01","track_id":"TRACK_01","objects":["person"],"confidence":0.91,"zone":"RESTRICTED"}'
```

#### Query Ingested Events:
```powershell
Invoke-RestMethod -Uri "http://<MAC_IP>:8000/events?limit=5" -Method Get
```

---

## Running the Test Suite

Execute the full automated test suite (including validation, rules, persistence, filtering, and error handling):

```bash
cd backend
source .venv/bin/activate
pytest -v
```

---

## Known Limitations & Next Steps

1. **Rule Configuration**: Rules currently evaluate `zone == "RESTRICTED"`. Future iterations can allow dynamic zone polygon checks and temporal threshold rules (e.g., loitering over N seconds).
2. **WebSocket Feed**: Future work can add a WebSocket endpoint `/ws/alerts` for live push alerts to a React dashboard.
3. **Database Migrations**: When schema changes occur, Alembic migrations should be integrated.
