# Completed 2026-10-30 (WAMSS)

## What was done

- **Backend re-verified on Linux**: installed from `backend/requirements.txt` in a fresh
  venv (Python 3.12.3) and re-ran the pytest suite, 12 passed. The count matches the
  existing README statement; final numbers are being reconciled by the coordinator.
- **Frontend dashboard built**: React + TypeScript with Vite. It includes a backend health
  panel, a live events table with polling, restricted-zone alert highlighting, a camera
  filter, and a test-event form, all wired to the real backend API. The API base URL is
  configurable via `VITE_API_URL` (`frontend/.env.example`).
- **Vision config hardened**: camera stream URLs, bridge/backend host, model path, and
  event interval moved to `WAMSS_*` environment variables (no hardcoded addresses left in
  `vision/multicamera.py`); `cv2.imshow` is now headless-safe. Live-camera verification
  is still not done.
- **Docs updated**: README planned stack, status, and vision sections; `docs/LOCAL_SETUP.md`
  Linux re-verification line; this note.

### Workstation activity module (8 features)

- **Activity agent** (`agent/`): smartwatch-style always-on logger (Windows first,
  Linux best-effort). Records active app/window/process, start/end timestamps,
  input counts (never keystroke content), idle periods, machine/employee ids.
  Local JSONL spool, batched sync with exponential backoff, Windows Scheduled
  Task / startup / systemd install docs. Verified: 12 strain unit tests pass,
  40 s dry run spooled 8 records with lossless backoff retry.
- **Strain / tension detection**: heuristic score from continuous active minutes
  (default 90), input intensity bursts, app-switch rate, and work-hours
  compliance. Break reminder (toast + snooze) at score >= 70, `break_suggested`
  event logged, `HIGH_STRAIN_DETECTED` after 3 consecutive batches. Verified:
  mocked trigger fired once, snooze suppressed the second.
- **BLE smartwatch** (`agent/wearable_ble.py`, optional): `bleak` heart-rate
  stream (service 0x180D), parsed 8/16-bit BPM, blended into strain (max +10
  points). Graceful fallback to heuristic-only when no device. 9 unit tests
  pass; pairing must happen on a real machine.
- **Camera + computer correlation**: `GET /presence` derives
  PRESENT_AND_ACTIVE, PRESENT_BUT_IDLE, ACTIVE_BUT_ABSENT (anomaly), ABSENT
  from vision events + activity logs with a configurable zone-to-machine map.
  10 unit tests pass.
- **Focus score + gamification**: `GET /focus/daily` and `/focus/streaks`,
  documented 0-100 formula, streak badges. 8 unit tests pass.
- **Security / DLP**: `agent/usb_monitor.py` (USB insert/remove, file-write byte
  counts only, never content), `POST /security/events` + `GET /security/alerts`.
  10 unit tests pass; Windows WMI path and real USB hardware untested here.
- **Policy engine + privacy**: `GET /policy/rules` + `/policy/violations`
  against `backend/policy.example.yaml` (lunch window, no work after 21:00,
  max 120 min continuous, max 10 daily hours). First-run consent gate
  (`agent/consent.py`), agent-side exclusion list, 90-day retention purger.
  8 unit tests pass.
- **Team dashboards + weekly digest**: Team tab (focus avg, strain distribution,
  break compliance, presence), `backend/scripts/weekly_digest.py` with dry-run
  default and stdlib SMTP. 4 digest tests pass.

Full backend suite: **52 passed**. Frontend `npm run build` passes with all
seven dashboard tabs. All new routers are mounted in `main.py`; the agent
integrates consent, the privacy exclusion list, and optional BLE heart rate.
The strain/focus scores are heuristic workload signals, not medical measures.

**Employee notice and consent:** deployments of the workstation agent must
include employee notice and consent per applicable workplace law.

## Open items

- Live-camera verification not done.
- No authentication on any endpoint.
- No migrations/CI.
- Simulation harness (`shared/`) missing.
- DB credential rotation still owed.
- BLE pairing, SMTP credentials, and zone-to-machine mapping need the user's
  real machine setup (documented in `agent/README.md` and `.env.example` files).
- Windows WMI/USB/pywin32 paths and real USB hardware events untested on Linux.
- Weekly digest live-data fetcher (non-demo `--send` path) not yet wired.
