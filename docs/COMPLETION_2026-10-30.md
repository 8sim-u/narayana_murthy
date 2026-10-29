# Completed 2026-10-30 (WAMSS)

## What was done

- **Backend re-verified on Linux**: installed from `backend/requirements.txt` in a fresh
  venv (Python 3.12.3) and re-ran the pytest suite, 12 passed. The count matches the
  existing README statement; final numbers are being reconciled by the coordinator.
- **Frontend dashboard built**: React + TypeScript with Vite. It includes a backend health
  panel, a live events table with polling, restricted-zone alert highlighting, a camera
  filter, and a test-event form, all wired to the real backend API. The API base URL is
  configurable via `VITE_API_URL` (`frontend/.env.example`).
- **Vision config hardened**: camera stream URLs, backend host, model path, and
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
- `vision/bridge.py` (the deprecated standalone bridge) was **deleted on 2026-10-30
  (round 3)**. The vision pipeline posts directly to the real backend `/event` endpoint,
  leaving a single ingestion path.

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

## Round 3 (2026-10-30): auth, Docker, alerts, analytics, multi-office

Built by five parallel agents and merged in one push.

- **API key auth**: `backend/app/auth.py` middleware accepts `X-API-Key`
  from `WAMSS_API_KEY`; disabled with a startup warning when unset.
  `/health`, `/health/db`, and docs paths are exempt. All clients updated
  (`vision/multicamera.py`, `agent/activity_agent.py`, `shared/simulate.py`,
  `weekly_digest.py`). Tests in `backend/tests/test_auth.py`. Design and key
  distribution in `docs/AUTH.md`. Note: the browser dashboard cannot hold a
  shared secret; serving the dashboard behind auth needs an API proxy
  (follow-up).
- **Cleanup**: `vision/bridge.py` deleted; all references removed from code
  and docs.
- **Docker**: `backend/Dockerfile`, `frontend/Dockerfile` (multi-stage +
  nginx), `docker-compose.yml` (backend + frontend, optional postgres
  profile), `.dockerignore` files, root `.env.example`. Deploy guide in
  `docs/DEPLOY.md`; one-command `docker compose up --build`. Compose YAML
  validated by parsing (no Docker daemon on the build machine).
- **Real-time alerts**: `backend/app/alerts.py` dispatches on
  `HIGH_STRAIN_DETECTED` and security anomalies via SMTP email, Telegram
  bot, or a generic webhook (covers WhatsApp via provider). All channels
  optional, failures logged not fatal. Tests mocked. Setup in
  `docs/ALERTS.md`.
- **Exports + analytics**: `GET /exports/events.csv|activity.csv|focus.csv`
  (same filters as JSON endpoints), `GET /analytics/trends?metric=focus|strain&weeks=N`,
  `backend/scripts/weekly_report_pdf.py` (fpdf2). Dashboard gained a Trends
  tab and CSV Export buttons on Events and Activity. Docs in
  `docs/ANALYTICS.md`.
- **Multi-office + responsive**: `office_id` on activity and security
  records (default "hq"), `office_id` filter on `/activity`, `/presence`,
  `/focus/daily`, `/security/alerts`, dashboard office selector, responsive
  CSS for mobile. Docs in `docs/MULTI_OFFICE.md`.

## Open items after round 3

- Live-camera verification, BLE pairing, SMTP credentials, zone-to-machine
  mapping, Windows WMI/USB paths: need the user's real hardware (documented).
- DB credential rotation still owed (no access to the credential store).
- Dashboard-behind-auth needs an API proxy (recorded in `docs/AUTH.md`).

## Round 4: literature-driven build + deployment pack (2026-10-30)

Built from the deep-research implementation recipes (Pepa 2021, Nagelin
2023, Hibbeln 2017, Banholzer 2021, Koldijk 2018, Acien 2022, Epp 2011).

### ML stress dynamics engine (Agent A)

- `agent/ml_stress.py` (new): 27 literature-ranked features per 60 s
  non-overlapping window (15 keyboard timing features from anonymized
  press/release pairs, 12 mouse dynamics features from `{ts, x, y, kind}`
  events), zero-filled empty windows, per-user Welford z-score
  `RollingBaseline` (cold-start safe), `unsupervised_strain_score` 0-100,
  `SupervisedStressModel` (joblib load/predict). stdlib-only at runtime.
  Privacy absolute: timing aggregates only, never key identities/content.
- `agent/train.py` (new): `python -m agent.train --csv labeled.csv --out
  models/stress_rf.joblib`; stratified k-fold CV printed, balanced
  RandomForest refit, joblib dict artifact. SWELL-KW column mapping notes
  in `agent/README.md`.
- Tests: `agent/tests/test_ml_stress.py`, 17/17 green, including a
  synthetic calm-vs-stressed end-to-end assertion.
- Needs real machines: pynput listeners must emit anonymized timings for
  full fidelity (interval synthesis works meanwhile); baselines need
  multi-day real data; supervised training needs labeled data.

### Fatigue detection + fusion + strain wiring (Agent B)

- `agent/fatigue.py` (new): `FatigueTracker`, k-NN (k=3) session distance
  per Acien 2022, robust z > 2.5 with 4+ prior sessions raises
  `fatigue_suspected` events (moderate/high).
- `agent/fusion.py` (new): early (feature-level) fusion when HR is fresh,
  late (decision-level) fusion via `mode="late"`, missing-modality masking
  (HR stale > 120 s falls back to behavioral-only).
- `agent/strain.py`: additive round-4 section (`AnonymizedTimingBuffer`,
  guarded `ml_stress` import, `enrich_strain_score()`); existing heuristic
  untouched. `agent/wearable_ble.py` gained a `latest_sample()` accessor.
- `agent/activity_agent.py`: wiring applied behind `WAMSS_STRAIN_PIPELINE=1`
  (default 0 = heuristic-only behavior bit-identical); anonymized timing
  buffers, fatigue events spooled as `fatigue_suspected` records,
  `ml_stress_score` posted on activity records.
- Tests: 38 new (fatigue, fusion, strain wiring), all green.

### Privacy-preserving presence mode (Agent C, vision)

- `WAMSS_PRIVACY_MODE=1` in `vision/multicamera.py`: frames in memory
  only, person-count/pose aggregates only, identity/gaze paths explicitly
  refused, alert snapshots face-blurred (MediaPipe) or refused when
  MediaPipe is absent, no preview windows, 5 s occupancy debounce.
- MediaPipe is an optional dependency with YOLO fallback; non-privacy
  path unchanged.
- Tests: `vision/tests/test_privacy.py`, 29/29 green (synthetic frames).
- Still needs real cameras: debounce tuning, pose heuristics, snapshot
  guarantees, end-to-end privacy events.

### USB anomaly upgrade + hours baselining (Agent D)

- `agent/usb_monitor.py`: `UsbAnomalyDetector` (first-seen serials per
  user in `~/.wamss/usb_seen.json`, allowlist file, BadUSB HID-spoof
  heuristic, off-hours mounts). New event types `usb_first_seen`,
  `usb_unauthorized`, `usb_hid_spoof_suspected`, `usb_offhours_mount`
  accepted by `POST /security/events` and mapped in `flag_anomalies()`.
- `backend/app/hours.py` (new, mounted in `main.py`):
  `GET /hours/baseline` (30-day 15-min-bin histogram, 5th/95th percentile
  normal window) and `GET /hours/anomaly` (z-score vs personal norm,
  after-hours flag, burstiness). See `docs/HOURS_API.md`.
- Tests: 10 USB + 11 hours tests, all green.

### Backend integration (coordinator)

- `ActivityLog.ml_stress_score` (nullable Float) added; accepted on
  ingest, returned by `GET /activity`, surfaced in
  `GET /activity/strain-summary` (lights up the dashboard's ML score
  element). Existing databases need
  `ALTER TABLE activity_logs ADD COLUMN ml_stress_score FLOAT;`.
- `GET /activity` event-type filter now lists `fatigue_suspected`.

### Deployment pack (Agent F)

- `deploy/` (new): `deploy.sh`/`deploy.ps1` (one-command stack deploy,
  first-run `.env` + generated `WAMSS_API_KEY`, health-gated, idempotent),
  `preflight.sh`/`preflight.ps1` (readiness checklist), `install-agent.sh`
  / `install-agent.ps1` (workstation installers with uninstall),
  `backup.sh`/`restore.sh` (timestamped DB archives).
- `docs/DEPLOYMENT_PACK.md` (new): ordered 8-step runbook plus
  troubleshooting. Root README gained a `## Deploy` section.
- Verified: `bash -n` on all scripts, `--help` dry runs, live preflight
  (correctly fails without docker), secret-generation logic tested in
  isolation. `.ps1` syntax best-effort only (no pwsh here); images not
  built (no docker here).

### Test totals

Backend suite and agent suite re-run by the coordinator after the merge;
counts recorded below. Frontend `npm run build` clean.

### Still open

- Real-machine validation: cameras, BLE pairing, Windows USB/WMI paths,
  `.ps1` smoke run, docker image builds, SMTP credentials, zone mapping.
- DB credential rotation still owed (no access).
- Dashboard-behind-auth API proxy follow-up (recorded in `docs/AUTH.md`).
