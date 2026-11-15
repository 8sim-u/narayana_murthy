# WAMSS Workstation Activity Agent

Smartwatch-style, always-on workstation activity logger. It records **what the
workstation was doing**, never **what was typed**: per activity segment it logs
the active application name, window title, process name, start/end timestamps,
input activity *counts* (keystroke count and mouse-click count per interval;
no keystroke content, no key names, no mouse coordinates), idle periods,
machine id, user id, and a heuristic strain (workload tension) score.

Deployments must include employee notice and consent per applicable workplace
law before this agent is installed on any workstation. See "Privacy and
consent" below.

## How it works

1. **Collect** (every `WAMSS_LOG_INTERVAL` seconds, default 60): the agent
   snapshots the active window, drains the input counters, and decides whether
   the segment is idle (no input for `WAMSS_IDLE_AFTER_SEC` seconds).
2. **Spool locally**: every record is appended to a JSONL file beneath
   `WAMSS_SPOOL_DIR` before anything is sent. The agent places each
   employee/OS-user pair in a separate hashed subdirectory, so one user's
   consent cannot release another user's queued records. If the backend is
   unreachable, records accumulate in that identity's local queue.
3. **Sync in batches** (every `WAMSS_SYNC_INTERVAL` seconds, default 300):
   the agent POSTs up to `WAMSS_MAX_BATCH` records to
   `{WAMSS_AGENT_SERVER_URL}/activity`. On failure it retries with exponential
   backoff (30 s, doubling, capped at `WAMSS_MAX_BACKOFF_SEC`).
   Each upload includes the employee's personal `X-Employee-Token`; the backend
   accepts only records whose `employee_id` matches that token. The Windows
   installer collects it through a hidden prompt and stores only a machine-scope
   DPAPI-encrypted blob, with the file ACL limited to the assigned Windows user
   and (when enabled) the exact sync-service SID. Do not add either secret to
   `config.env`, command-line arguments, or logs. The deployment-wide API key is
   not provisioned to workstation agents: only employee-token-authenticated
   `POST /activity` is exempt from that key, and the route still rejects tokens
   that are invalid or mapped to another employee. Other protected API routes
   remain key-gated.
4. **Strain scoring and break reminders**: each interval the agent updates a
   heuristic strain score (see below). When the score crosses
   `WAMSS_BREAK_THRESHOLD`, the employee sees a break reminder (desktop toast
   via a pluggable notifier; log fallback on headless machines) and a
   `break_suggested` record is spooled so it flows through the normal ingest
   path. Reminders are snoozed for `WAMSS_BREAK_SNOOZE_MIN` minutes after
   firing and never repeat more often than
   `WAMSS_BREAK_REMINDER_COOLDOWN_MIN` minutes.

All behavior is configurable through environment variables; copy
`agent/.env.example` to `.env` (never commit real ids).

## Strain (workload tension) signal

**This is a heuristic workload signal, not a medical measure.** It estimates
how intense the current work pattern is; it does not measure stress, fatigue,
burnout, or any health condition, and must never be presented as such.

Signals and defaults (all tunable via env vars):

| Signal | Env var | Default | Weight |
|---|---|---|---|
| Continuous active minutes without a break | `WAMSS_STRAIN_ACTIVE_MIN_THRESHOLD` | 90 | 0.30 |
| Input intensity: keystrokes + clicks per minute | `WAMSS_STRAIN_INTENSITY_PER_MIN` | 120 | 0.25 |
| Window switches per 5 minutes | `WAMSS_STRAIN_SWITCHES_PER_5MIN` | 10 | 0.25 |
| Activity outside work hours | `WAMSS_WORK_HOURS` | `09:00-18:00` local | 0.20 |

Each signal is normalized to 0-1 against its threshold, multiplied by its
weight, and scaled to a 0-100 score. Levels: **green** < 40, **amber** 40-69,
**red** >= 70. The continuous-active clock resets after
`WAMSS_BREAK_RESET_IDLE_MIN` (default 5) idle minutes.

Backend rule: when a machine/employee sends `WAMSS_STRAIN_ALERT_BATCHES`
(default 3) consecutive batches with peak strain >=
`WAMSS_STRAIN_ALERT_THRESHOLD` (default 70), the server writes an
`HIGH_STRAIN_DETECTED` alert record. The dashboard shows the latest level per
employee/machine as a green/amber/red indicator.

### Tuning

- Lower `WAMSS_BREAK_THRESHOLD` (e.g. 50) for earlier nudges; raise it (e.g.
  85) to only catch extreme stretches.
- `WAMSS_WORK_HOURS` accepts overnight windows like `22:00-06:00`.
- Weights live in `agent/strain.py` (`W_ACTIVE`, `W_INTENSITY`,
  `W_SWITCHING`, `W_HOURS`) with unit tests in `agent/tests/`.

# signal" / "### Tuning", before "## Privacy and consent".

## ML stress dynamics (agent/ml_stress.py)

**Heuristic workload signal, not a medical measure.** A literature-based
keystroke/mouse dynamics engine (Pepa 2021, Nagelin 2023, Hibbeln 2017,
Banholzer 2021, Koldijk 2018) that complements the count-based strain
heuristic in `agent/strain.py`. Privacy rule is absolute: it consumes
**timing aggregates only** (anonymized press/release timestamps and
move/click/scroll events). Key identities, key content, and screen content
are never accepted, stored, or derivable. Backspace/delete activity enters
only as aggregate counts supplied by the logger.

### Analysis windows

Events are sliced into **60-second non-overlapping windows** before feature
extraction (`slice_pairs`, `slice_events`). Windows with no events are
zero-filled so downstream code never sees missing columns.

### Features (27 per window, stable CSV column order)

Keyboard, from `(press_ts, release_ts)` pairs (15):
`kb_n_keys`, `kb_dwell_min/mean/std` (key-hold time),
`kb_flight_min/std` (down-to-down time), `kb_digraph_mean/std`
(press-to-press rhythm inside typing bursts, pauses excluded),
`kb_release_press_latency_mean`, `kb_velocity` (keys/sec),
`kb_backspace_count`, `kb_delete_count` (logger-supplied aggregates),
`kb_pause_count`, `kb_pause_mean` (gaps > 1 s; many short pauses are a
stress signal), `kb_duty_cycle` (total dwell / 60 s).

Mouse, from `{ts, x, y, kind}` events, kind in
`move|click_down|click_up|scroll` (12): `ms_move_count`, `ms_click_count`,
`ms_scroll_count`, `ms_velocity_mean/std`, `ms_acceleration_mean`,
`ms_deviation_mean` (mean perpendicular distance from the straight-line
path: path jaggedness), `ms_direction_changes`, `ms_click_dwell_mean`,
`ms_interclick_distance_mean/time_mean`,
`ms_speed_accuracy_interaction` (velocity x deviation).

### Per-user baselines

`RollingBaseline` keeps a Welford running mean/variance per feature.
Keep **one instance per user** (the integrator owns the mapping), call
`update(features)` each window, then `zscore(features)` before scoring.
Features with fewer than `min_windows` (default 10) observations yield
z = 0.0, so cold-start users get neutral scores.

### Scoring paths

* Unsupervised: `unsupervised_strain_score(z)` returns 0-100.
  `100 * weighted_mean(|z| clipped at 4)`, weights follow literature
  emphasis: dwell-time variability 0.22, flight-time variability 0.20,
  typing-velocity deviation 0.18, mouse path jaggedness 0.12 + 0.08,
  pause-structure shift 0.10 (only elevated pause counts), digraph rhythm
  instability 0.05, speed-accuracy interaction 0.05.
* Supervised: `SupervisedStressModel().load(path).predict(features)`
  returns 0-100 (stress-class probability for classifiers).

### Training a model

```bash
python -m agent.train --csv labeled.csv --out models/stress_rf.joblib
```

`labeled.csv`: one row per 60 s window, feature columns as above plus a
binary `stress` column (1 = stressed, 0 = calm). Prints stratified k-fold
CV accuracy, refits on all data, dumps a joblib dict
`{model, feature_names, meta}`. Requires scikit-learn and joblib
(see requirements).

### Training on SWELL-KW: column mapping notes

The SWELL-KW knowledge-worker dataset (Koldijk et al.) logs keystroke and
mouse events with timestamps plus per-condition perceived-stress ratings,
which makes it the closest public fit for this pipeline. Mapping notes:

* Keystroke logs: map each key event to `(press_ts, release_ts)` in
  seconds and **drop the key identity column entirely** before feature
  extraction. SWELL key logs may only carry press timestamps; in that
  case dwell features degrade to zero and the remaining timing features
  still apply (document the substitution in your training notes).
* Mouse logs: map to `{ts, x, y, kind}` with kind derived from the
  SWELL event type (movement -> `move`, button press/release ->
  `click_down`/`click_up`, wheel -> `scroll`). Coordinates are used only
  for path geometry.
* Labels: binarize the per-condition perceived-stress rating into the
  `stress` column (e.g. stressor conditions = 1, neutral baseline = 0);
  record the exact threshold in the training notes for reproducibility.
* Always verify column names against the SWELL-KW release you download;
  the layout varies between the 2014 and 2016 releases.

### Logger extension required

The current `InputCounter` in `activity_agent.py` emits counts only. To
feed this module, extend the pynput listeners to emit anonymized
`(press_ts, release_ts)` pairs (plus backspace/delete aggregate counts)
and mouse `{ts, x, y, kind}` events. Never log key names, key content, or
screenshots; the privacy exclusion list still applies.

## Session fatigue detection and HR fusion (round 4, opt-in)

The strain pipeline can be extended beyond the heuristic with two
optional stages, both off by default. Enable with `WAMSS_STRAIN_PIPELINE=1` (wired in `agent/activity_agent.py`;
default 0 keeps the heuristic-only behavior bit-identical).

### 1. ML behavioral features (agent/ml_stress.py, Agent A)

`strain.extract_ml_features()` feeds anonymized timing data to the
keystroke/mouse dynamics extractors and scores the result with the
per-employee `RollingBaseline` plus `unsupervised_strain_score()`
(0-100). Two safety rules keep the heuristic authoritative:

- Cold-start guard: until the rolling baseline has seen `min_windows`
  windows, the ML score is withheld (returns None) and the heuristic
  score is used. A neutral all-zero z-vector must never override a
  meaningful heuristic.
- Total fallback: if `agent.ml_stress` is missing or any ML call raises,
  the pipeline returns the heuristic score with no crash.

The agent's `InputCounter` keeps counts only, so the wiring uses
`AnonymizedTimingBuffer` ring buffers (in-memory, never persisted) of
inter-event gaps. `extract_ml_features()` adapts gaps to the shapes
`ml_stress` expects: press times are rebuilt cumulatively and release is
assumed at press + 0.10 s (dwell features wash out to z = 0, flight /
digraph / pause / velocity features stay informative); each click gap
becomes a click_down/click_up pair with x = y = 0.0 because the agent
never captures coordinates (path-geometry features neutralize, click
count and inter-click timing stay informative). If the logger is ever
extended to emit real anonymized (press, release) pairs or mouse event
dicts, pass them as `key_pairs=` / `mouse_events=` instead.

### 2. Session-to-session fatigue (agent/fatigue.py, Acien et al. 2022)

`FatigueTracker` keeps the last 10 session feature vectors per employee
(in memory). At each session end the current session's k-NN distance
(k = 3, euclidean on z-normalized features) is compared against the
history of past distances; a robust z-score above 2.5 with at least 4
prior sessions raises a `fatigue_suspected` event
`{type, employee_id, severity, detail}`. Severity is `moderate`
(z in 2.5-4) or `high` (z >= 4). Heuristic only, not a medical measure.

### 3. Wearable boundary

The repository retains an experimental fusion helper for compatibility, but the running activity agent deliberately passes no wearable sample into that helper and never blends BPM or HRV into `strain_score`. Manager-visible activity alerts therefore remain based on work-pattern signals only. Wearable readings are available only through the employee's separate, token-protected wellness view after both local and dashboard consent. A generic manager check-in is separate, requires an additional employee opt-in and a server-authorized roster, and never exposes health values or trends. See `docs/WELLNESS_PRIVACY.md`.

An optional employee-local break toast can be enabled with
`WAMSS_WELLNESS_BREAK_PROMPTS=1` (default `0`). It requires the employee token,
the separate local wellness consent, and dashboard wellness consent. The agent
uses the existing employee-only combined-prompt result and threshold; it does
not persist or upload HRV values. When the prompt fires it automatically logs
only a private `suggested` event, even if the employee does not click anything.
It never records a break as taken; that status requires the employee's actual
confirmation. This local prompt does not notify a manager or alter work-pattern-
based activity alerts; optional manager check-ins are a separate dashboard
feature with their own employee opt-in and roster authorization.
The Windows installer does not distribute the deployment-wide API key. The
optional combined wellness break-prompt mode calls additional key-gated routes
and is therefore not provisioned by this installer; keep it disabled unless a
separately authorized client design is provided.

### What stays intact

The heuristic `compute_strain_score()`, toast break reminders,
`break_suggested` spool records, and backend `HIGH_STRAIN_DETECTED`
rule (which keys on `strain_score` streaks in `activity` records) retain
their work-pattern-only meaning. `HeartRateStream.latest_sample()` exposes
a fresh private sample with `{ "bpm": int, "rmssd_ms": float | null,
"ts": epoch }` or None; the uploader sends it directly and never spools it.

## USB anomaly detection (agent/usb_monitor.py)

`UsbAnomalyDetector` classifies enriched USB device descriptions and emits
structured anomaly events with `event_type` / `severity` / `reason`, matching
the backend alert vocabulary in `backend/app/security.py`:

| Event | Severity | Trigger |
|---|---|---|
| `usb_first_seen` | medium | serial never seen before for this user |
| `usb_unauthorized` | high | serial not on the allowlist |
| `usb_hid_spoof_suspected` | high | first-seen device presents as HID keyboard/mouse |
| `usb_offhours_mount` | medium | mass-storage mount outside baseline work hours |

Details:

* **First-seen state** persists per user in a small JSON file, default
  `~/.wamss/usb_seen.json` (override with `WAMSS_USB_STATE`). Written through
  on every new serial so state survives process restarts.
* **Allowlist** is a plain text file, one serial per line (`#` comments and
  blank lines ignored), default `~/.wamss/usb_allowlist.txt`, path
  configurable via `WAMSS_USB_ALLOWLIST`. Loaded once at startup.
* **HID-spoof heuristic**: a first-seen HID keyboard/mouse fires
  `usb_hid_spoof_suspected`. This is a triage signal for BadUSB-style
  spoofing, NOT proof of an attack: a legitimate new keyboard also fires.
  Human verification of the physical device is required.
* **Off-hours mounts** compare the local mount time against
  `WAMSS_WORK_HOURS` ("HH:MM-HH:MM", default "09:00-18:00") in
  `WAMSS_SECURITY_TZ` (default "UTC"). Upgrade path: replace the env value
  with the per-user normal window from `GET /hours/baseline` once the
  backend hours-baselining API is wired in.

Serial sources (best effort; no file/keystroke/screen content is ever read):

* Linux: pyudev `ID_SERIAL_SHORT` / `ID_SERIAL` (`lookup_device_serial()`).
* Windows: WMI `Win32_DiskDrive.SerialNumber`, or the Security log EventID
  6416 "new external device" record. Both Windows paths need a real machine
  and are NOT verified; they stay documented until tested on hardware.

Unit tests: `agent/tests/test_usb_anomaly.py` (synthetic devices only, temp
state/allowlist dirs). Run: `python -m pytest agent/tests/test_usb_anomaly.py`.

To ingest these events over `POST /security/events`, the backend
`SecurityEventType` enum needs the four new type values added (see
`/tmp/round4/security_event_types_note.md`); until then the agent prints /
forwards them as structured dicts.

## Packaged Windows tray monitor

Build the single-file executable with `agent/build_exe.ps1`. Place a
`config.env` beside `WAMSSWorkMonitor.exe` containing `WAMSS_AGENT_SERVER_URL`,
`WAMSS_IDLE_AFTER_SEC`, and `WAMSS_LOG_INTERVAL`; the executable has no
hard-coded server or idle policy. The build copies your `config.env` beside
the executable when one exists, otherwise it copies `config.env.example`
as a starter template to fill in. Without a `config.env` the executable
exits before collection, so fill in the template first. It shows a tray status of **WORKING** when
last input is newer than the configured idle threshold and **IDLE** once the
threshold is reached. The tray updates from local observation even if the
backend is unavailable; backend failure is displayed alongside that state.
Successful `/activity` records carry `is_idle` using this same threshold.
Configured app/title exclusions display a privacy-exclusion status and are
never posted.

To start the packaged monitor at sign-in for the current user, run
`agent/startup.ps1 -Executable C:\path\to\WAMSSWorkMonitor.exe`. This
creates a shortcut in that user's Startup folder; use
`-Action Remove -Executable <path>` to remove it. The shortcut runs
interactively so the tray and first-run employee consent prompt remain
available. Startup registration does not grant consent: collection still
requires the monitor's existing consent gate. Provide applicable employee
notice before enabling it.

## Privacy and consent

- The agent counts keystrokes and mouse clicks per interval. It **never**
  captures key content, key names, clipboard contents, or screenshots
  (`WAMSS_SCREENSHOTS` defaults to 0 and screenshot capture is not
  implemented).
- The strain score is a workload heuristic, not a medical measure.
- **Deployments must include employee notice and consent per applicable
  workplace law.** Inform employees what is collected (app names, window
  titles, input counts, idle time, strain heuristic), why, how long it is
  retained, and who can see it, before installing the agent.

### First-run consent gate

Before any collection starts, the agent calls `ensure_consent()`
(`agent/consent.py`). On Windows it shows a graphical Yes/No dialog; on the
console it asks for a typed `YES`. In headless/non-interactive mode it refuses
and the agent exits, so nothing is logged without explicit consent. Each local
marker binds consent to both the configured `WAMSS_EMPLOYEE_ID` and the active
OS user. A missing marker or either identity changing fails closed and requires
a fresh explicit prompt; consent is never silently inferred or transferred.

By default, the marker is durable and per OS user: Windows uses
`%LOCALAPPDATA%\WAMSS\consent.json`; Linux uses `$XDG_STATE_HOME/wamss/consent.json`
or, when unset, `~/.local/state/wamss/consent.json`. `WAMSS_CONSENT_FILE`, when
set, is an exact path override used consistently by the tray monitor, full
agent, sync service, and withdrawal command. Keep an override scoped to the
intended user; identity binding still applies. To withdraw, run
`python consent.py --withdraw` in the signed-in user's session and confirm the
graphical prompt (or type `WITHDRAW` in an interactive console). Withdrawal
addresses that same user's current employee identity and marker path, and will
not remove a marker belonging to another identity. The collector checks consent
before each collection interval and stops when valid consent is absent. The
boot-start Windows sync service resolves the active interactive OS user and
pauses uploads if that user cannot be resolved or the configured employee/user
identity does not match the marker. Queued records remain local until fresh
consent for that same identity. `WAMSS_SPOOL_DIR` is a base directory, and the
sync service selects only the current employee/OS-user subdirectory. Unscoped
pre-upgrade spool files are left untouched and are not automatically uploaded,
because their consenting identity cannot be proven. Headless withdrawal cannot
silently proceed: use an interactive session or ask an administrator to stop
the agent and remove the appropriate marker. Workstation withdrawal does not
change separate camera-specific or optional wellness consent.

### Separate optional wellness consent

Wearable health collection has its own `ensure_wellness_consent()` prompt and identity-bound `wellness-consent.json` marker. By default it is stored in the employee's user-state directory (`%LOCALAPPDATA%\WAMSS` on Windows; `$XDG_STATE_HOME/wamss` or `~/.local/state/wamss` on Linux), not in the protected installation directory. A Yes is not implied by activity monitoring consent; a No, missing prompt (including headless mode), missing employee token, or disabled BLE keeps the wearable off. The marker binds the configured employee ID to the active OS user and contains consent metadata only, not health readings. Dashboard consent is separate again and can be withdrawn there, which deletes persisted wellness history.

### Privacy exclusion list

Personal apps are never logged, not even to the local spool:

- `WAMSS_EXCLUDED_APPS`: comma-separated process/app names, matched
  case-insensitively (e.g. `KeePass.exe,1Password.exe,bitwarden.exe,signal.exe`).
- `WAMSS_EXCLUDED_TITLES`: optional regex; any active window whose title
  matches is skipped (e.g. `(?i)\b(password|bank|otp)\b`).

Excluded windows are dropped before segment creation, so they never reach the
spool or the sync path. The dashboard privacy panel (`PolicyView`) shows the
live configuration.

### Daily app categories and attendance

The backend reads `backend/app/app_categories.json` (override with
`WAMSS_APP_CATEGORY_FILE`) as a JSON object mapping process names to
`work`, `break`, or `neutral`. Names match case-insensitively by executable
basename. Unlisted or invalid entries always count as `neutral`. Daily totals
are available at `GET /activity/app-usage?day=YYYY-MM-DD`; `day` uses UTC and
employee, machine, and office filters are supported.

Daily attendance is a separate, **disabled-by-default** backend feature. Set
`WAMSS_ATTENDANCE_ENABLED=1` only after providing applicable employee notice
and obtaining required permissions. Camera recognition needs its own
applicable notice/consent; accepting workstation activity consent does not
enroll anyone for face recognition. `GET /attendance/daily?day=YYYY-MM-DD`
returns UTC-day totals and source labels. Camera rows are included only for recognized, enrolled identities above
`WAMSS_ATTENDANCE_MIN_CONFIDENCE` (default `0.5`) that also have a separate
affirmative camera-consent record; unknown or unconsented identities are excluded.
Record employee choices in `backend/app/attendance_camera_consents.json` (or
`WAMSS_ATTENDANCE_CAMERA_CONSENT_FILE`) only after obtaining and documenting
separate camera-specific consent, for example:

```json
{"Alex Example": {"consented": true, "recorded_at": "2026-11-14T09:00:00Z"}}
```

An absent/malformed record denies camera attendance. Remove the employee entry
to withdraw camera-derived attendance; this does not affect computer activity
attendance. The file contains consent metadata, not face images or biometric data. Camera minutes sum reported
tracked `dwell_seconds` (capped by `WAMSS_ATTENDANCE_DWELL_CAP_SEC`, default
300) without guessing across gaps. Computer minutes use non-idle activity
segments. `present_minutes` merges the two interval sets so overlapping
camera/computer time is counted once. The dashboard and weekly PDF show
camera-only, computer-only, and combined sources separately.

If the camera's recognized display name differs from `WAMSS_EMPLOYEE_ID`, add
an explicit entry to `backend/app/attendance_identity_map.json` (or point
`WAMSS_ATTENDANCE_IDENTITY_MAP` at another JSON file), for example:

```json
{"Alex Example": "employee-042"}
```

Without a mapping, a camera identity is reported under its recognized name;
it is not silently matched to a workstation account. Docker mounts both JSON
maps read-only from `backend/app/`; edit them on the host.

### Optional: private wearable wellness via BLE

Wearable collection is **off by default** and requires all three: `WAMSS_BLE_ENABLED=1`, a valid per-employee `WAMSS_EMPLOYEE_TOKEN`, and a separate local wellness consent prompt. The employee must also separately opt in on the personal wellness dashboard before the backend accepts live health samples. The first workstation activity consent does not grant wellness consent.

The stream reads the standard BLE Heart Rate service (0x180D). Heart rate and a short-window RMSSD estimate (derived from RR intervals in a bounded in-memory buffer) are sent directly to the backend; they are never written to the activity spool, activity records, strain scores, or work-pattern activity alerts. The server keeps only the latest sample in memory for at most 90 seconds. Daily HRV, resting-HR and sleep summaries are separate date-only inputs saved only after dashboard opt-in. Raw health values, trend details, and readiness remain employee-private. Only after a distinct manager-check-in opt-in may an authorized manager see the employee ID and a generic supportive prompt when both documented thresholds agree; the prompt is never an employment decision.

Pairing steps per OS:

- Windows: Settings > Bluetooth and devices > Add device. Pair your watch or chest strap, then set `WAMSS_BLE_DEVICE` to part of its device name.
- Linux: run `bluetoothctl`, then `scan on`, `pair <MAC>`, `trust <MAC>`, `connect <MAC>`. Set `WAMSS_BLE_DEVICE` to the MAC or a name substring.

If bleak is missing, the adapter/device is unavailable, consent is declined, or a token is not configured, BLE stays off and work-pattern-only activity logging is unchanged. To stop local collection, set `WAMSS_BLE_ENABLED=0` and remove `wellness-consent.json`; to withdraw server-side consent and erase wellness history, use the personal dashboard. These are separate steps by design. See `docs/WELLNESS_PRIVACY.md` for token provisioning, calculations, retention, and evidence limits.

### Security / DLP signals

`agent/usb_monitor.py` reports USB insert/remove events and file-write bursts
(byte counts only, never file content). Thresholds:

- `WAMSS_DLP_WRITE_THRESHOLD_MB` (default 500): bytes written per
  `WAMSS_DLP_WRITE_INTERVAL_SEC` (default 300) that flag a burst.
- `WAMSS_WORK_HOURS` (default "09:00-18:00") and `WAMSS_SECURITY_TZ`
  (default "UTC") drive the after-hours checks.

The backend stores only aggregate signals (device labels, byte counts,
timestamps). No file content, file paths, or keystroke content is captured
or stored, ever. Alerts appear in the dashboard Security tab.

## Install and run

### 1. Install dependencies

```bash
python -m venv /home/hatch/workspace/.venvs/wamss-agent
source /home/hatch/workspace/.venvs/wamss-agent/bin/activate
pip install -r agent/requirements.txt
```

### 2. Configure

```bash
cp agent/.env.example agent/.env   # then edit machine/employee ids
# or export the vars in your shell / service definition
```

### 3. Dry run (foreground, exits after N seconds)

```bash
WAMSS_DRY_RUN_SECONDS=45 \
WAMSS_LOG_INTERVAL=5 \
WAMSS_SYNC_INTERVAL=10 \
WAMSS_AGENT_SERVER_URL=http://127.0.0.1:1 \
python agent/activity_agent.py
```

The dummy URL forces sync failures so you can watch the spool fill and the
backoff kick in. Inspect the spool:

```bash
head -3 agent/spool/pending.jsonl | python -m json.tool
```

### 4. Run the unit tests

```bash
pytest agent/tests/ -v
```

### 5. Break-reminder verification (mocked notifier)

Force a trigger without waiting for a real 90-minute stretch:

```bash
python - <<'EOF'
import json, os, sys
sys.path.insert(0, "agent")
os.environ["WAMSS_SPOOL_DIR"] = "/tmp/wamss-verify-spool"
from activity_agent import StrainTracker, Spool
from strain import StrainConfig

calls = []
spool = Spool("/tmp/wamss-verify-spool")
cfg = StrainConfig(break_threshold=1, break_snooze_min=60,
                   break_reminder_cooldown_min=60)
tracker = StrainTracker(cfg=cfg, notifier=lambda t, m: calls.append((t, m)))
# synthetic heavy workload: 120 active minutes, 200 events/min, 30 switches/5min
tracker.active_start_ts = __import__("time").time() - 120 * 60
now = __import__("time").time()
for _ in range(30):
    tracker.switch_events.append(now)
for _ in range(200):
    tracker.input_events.append((now, 1))
rec = tracker.maybe_trigger_break(spool)
print("notifier calls:", len(calls), "| channel would be: custom")
print("break_suggested in spool:", rec["event_type"], "score=", rec["strain_score"])
print(open("/tmp/wamss-verify-spool/pending.jsonl").read().strip()[:200])
EOF
```

Expected: one notifier call, one `break_suggested` record with the computed
score in `/tmp/wamss-verify-spool/pending.jsonl`.

## Windows monitor executable (single-file tray app)

On Windows, build the standalone tray monitor from the repository root in
PowerShell:

```powershell
.\agent\build_exe.ps1
```

The executable is written to `agent\dist\WAMSSWorkMonitor.exe`; the build
script uses an isolated build environment and packages the consent prompt.
Place a `config.env` next to the executable (or set
`WAMSS_MONITOR_CONFIG`) containing only nonsecret settings: backend URL,
`WAMSS_IDLE_AFTER_SEC`, `WAMSS_LOG_INTERVAL`, and machine/employee IDs. Never
put either credential in that file. On first run the tray monitor asks for the
employee's personal token in a masked dialog, stores it under
`%LOCALAPPDATA%\WAMSS\WorkMonitor\secrets` using machine-scope DPAPI, and
restricts the store ACL to that Windows account. It does not request or store
the deployment-wide API key; employee-token-authenticated `POST /activity`
does not require that key. Add the executable to the user's Startup folder or
create an **AtLogOn** Scheduled Task; it runs without a console, presents a tray
status, prompts for consent before collection, and posts records directly to
`/activity`. This small monitor does not keep an offline spool.

For durable offline buffering and ordered batch retries, use the full agent
below. Its optional `-UseWindowsService` installer mode keeps foreground
collection and consent in the signed-in desktop task, while a boot-start
Windows service uploads the shared local spool. Windows services cannot see
the interactive desktop, so the service is deliberately limited to network
sync. The installer precreates the one identity-specific queue: the desktop
task can append new activity and use its lock file, but cannot create, replace,
or delete spool entries; the service SID owns queue rotation and upload. The
service pauses when the configured account or consent marker cannot be
verified. The deployment-wide API key is not installed on the workstation.

## Always-on background service

### Windows: Scheduled Task (AtLogOn, hidden, no console window)

Run in an elevated PowerShell (replace paths):

```powershell
$exe = "C:\path\to\venv\Scripts\pythonw.exe"
$script = "C:\path\to\narayana_murthy\agent\activity_agent.py"
$action = New-ScheduledTaskAction -Execute $exe -Argument $script -WorkingDirectory "C:\path\to\narayana_murthy\agent"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "WAMSS Activity Agent" -Action $action -Trigger $trigger -Settings $settings `
  -Description "WAMSS workstation activity logger (employee notice and consent required)"
```

Notes:

- Use `pythonw.exe` so no console window appears; the task is hidden.
- Set nonsecret `WAMSS_*` variables as user environment variables or in a
  config file. Do not use this manual task snippet to provision credentials;
  use `deploy/install-agent.ps1` for the full agent's DPAPI and ACL setup, or
  the packaged monitor's masked first-run prompt. The employee token must be
  supplied by the employee and must not be shared with a manager.
- Verify in Task Scheduler that the task ran at next logon, and check
  `agent\spool\pending.jsonl` for fresh records.

### Windows: startup-folder entry (alternative)

Create a shortcut in the user's Startup folder
(`shell:startup`, i.e. `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`)
pointing at `pythonw.exe` with the script path as argument, Start in set to
the `agent/` directory. It launches at logon with no console window.

### Linux: systemd user unit (bonus)

`~/.config/systemd/user/wamss-agent.service`:

```ini
[Unit]
Description=WAMSS workstation activity agent
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/narayana_murthy/agent
EnvironmentFile=%h/narayana_murthy/agent/.env
ExecStart=%h/.venvs/wamss-agent/bin/python activity_agent.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now wamss-agent
systemctl --user status wamss-agent
```

Note: on Linux, active-window detection needs an X11 session (`python-xlib`);
on Wayland or headless machines the agent degrades gracefully (empty
app/window fields, input counting and idle detection still work via pynput
where a display is available).
