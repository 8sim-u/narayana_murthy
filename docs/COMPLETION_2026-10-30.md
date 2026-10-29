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

## Round 2 (2026-10-30): remaining completable work

- **Weekly digest live-data fetcher**: `backend/scripts/weekly_digest.py` now
  pulls real data for the non-demo path with the standard library only
  (urllib): `GET /activity`, `/activity/strain-summary`, `/focus/daily`,
  `/focus/streaks`, `/policy/violations`, `/security/alerts`, and
  `/presence`. New env vars `WAMSS_API_URL` (default
  http://localhost:8000) and `WAMSS_DIGEST_LOOKBACK_HOURS` (default 168),
  plus `--api-url` / `--lookback-hours` flags. Per-employee endpoint
  failures are best-effort; team-level failures exit 2 with a clear
  message. A new Security alerts section was added to the digest
  (plain + HTML), backward compatible. The direct-script entry point now
  runs `main()` (live data) instead of forcing `--demo`. Verified: 9
  digest tests pass (4 original + 5 new, HTTP layer mocked), plus a live
  end-to-end run against seeded data (focus scores, policy flags, and USB
  alerts all rendered). `--send` failure exits 1 with the SMTP error.
- **Simulation harness**: `shared/simulate.py` (stdlib only) seeds a
  running backend with realistic telemetry: `POST /event` (varied
  cameras/zones/confidence), `POST /activity` (batched records with apps,
  windows, input counts, idle periods, strain scores), and
  `POST /security/events` (USB, file-write bursts, after-hours). Flags:
  `--employees`, `--days`, `--api-url`, `--seed`, `--vision-per-day`,
  `--dry-run`. Verified: dry run plus a real seed (2 employees x 2 days,
  80 vision events, 88 activity records, 2 security events) against a live
  test backend, then the live digest read the seeded data back.
  Documented in `shared/README.md` and the root README demo section.
- **CI**: `.github/workflows/ci.yml` runs backend pytest on push/PR to
  main (Python 3.12, pip install from `backend/requirements.txt`).

Full backend suite after round 2: **57 passed** (52 + 5 new digest tests).

## Open items

- Live-camera verification not done (needs real cameras).
- No authentication on any endpoint. Adding auth is the recommended next
  step, but it was deliberately left out here: the vision client posts to
  `/event` with a fixed contract, and auth would need a coordinated change
  on both sides plus a key-distribution decision, which is a product call.
- No database migrations (CI is now in place).
- DB credential rotation still owed (no access to the credential store).
- BLE pairing, SMTP credentials, and zone-to-machine mapping need the
  user's real machine setup (documented in `agent/README.md` and the
  `.env.example` files).
- Windows WMI/USB/pywin32 paths and real USB hardware events untested on
  Linux.
- `vision/bridge.py` is deprecated in the README but kept; deleting it is
  a product decision for the repo owner.

## Open items from round 1 (kept)

- Live-camera verification not done.
- No authentication on any endpoint.
- No migrations/CI.
- Simulation harness (`shared/`) missing.
- DB credential rotation still owed.
- BLE pairing, SMTP credentials, and zone-to-machine mapping need the user's
  real machine setup (documented in `agent/README.md` and `.env.example` files).
- Windows WMI/USB/pywin32 paths and real USB hardware events untested on Linux.
- Weekly digest live-data fetcher (non-demo `--send` path) not yet wired.
