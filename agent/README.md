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
2. **Spool locally**: every record is appended to a JSONL file in
   `WAMSS_SPOOL_DIR` before anything is sent. If the backend is unreachable,
   records simply accumulate on disk; nothing is lost.
3. **Sync in batches** (every `WAMSS_SYNC_INTERVAL` seconds, default 300):
   the agent POSTs up to `WAMSS_MAX_BATCH` records to
   `{WAMSS_AGENT_SERVER_URL}/activity`. On failure it retries with exponential
   backoff (30 s, doubling, capped at `WAMSS_MAX_BACKOFF_SEC`).
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

### 3. Behavioral + heart-rate fusion (agent/fusion.py)

`fuse(behavioral_score, behavioral_features, hr_sample,
weights=(0.7, 0.3))` supports two literature patterns:

- Early (feature-level) fusion, the default when HR is present and
  fresh: combined vector
  `v = [behavioral/100, bpm/200, min(1, max(0, bpm-(resting+20))/40)]`,
  scored as `100*(w_b*v[0] + w_hr*v[2]) + 10*v[0]*v[2]`. The
  interaction term is the feature-level part: joint elevation scores
  higher than the sum of its parts. Literature pattern: early fusion
  wins when modalities are complete.
- Late (decision-level) fusion (`mode="late"`): weighted average of the
  behavioral score and the HR-derived score, robust to dropout.

The HR-derived score rises as bpm exceeds personal resting + 20
(0 at resting + 20, 100 at resting + 40). Missing-modality masking: if
the HR sample is None or stale (older than 120 s), the behavioral score
is returned unchanged with `meta = {"hr_missing": True,
"mode": "behavioral_only"}`.

### What stays intact

The heuristic `compute_strain_score()`, toast break reminders,
`break_suggested` spool records, and the backend `HIGH_STRAIN_DETECTED`
rule (which keys on `strain_score` streaks in `activity` records) are
unchanged. `EnrichedStrain` always carries the original heuristic
result, so thresholds keep their meaning. `HeartRateStream` gained one
additive accessor, `latest_sample()` returning
`{"bpm": int, "ts": epoch}` or None.

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
and the agent exits, so nothing is ever logged without an explicit answer.
Consent is recorded in a local `consent.json` marker (override with
`WAMSS_CONSENT_FILE`); deleting the marker forces a re-prompt.

### Privacy exclusion list

Personal apps are never logged, not even to the local spool:

- `WAMSS_EXCLUDED_APPS`: comma-separated process/app names, matched
  case-insensitively (e.g. `KeePass.exe,1Password.exe,bitwarden.exe,signal.exe`).
- `WAMSS_EXCLUDED_TITLES`: optional regex; any active window whose title
  matches is skipped (e.g. `(?i)\b(password|bank|otp)\b`).

Excluded windows are dropped before segment creation, so they never reach the
spool or the sync path. The dashboard privacy panel (`PolicyView`) shows the
live configuration.

### Optional: smartwatch heart-rate via BLE

Set `WAMSS_BLE_ENABLED=1` and install bleak (`pip install -r
agent/requirements.txt`). The agent connects to a BLE heart-rate device using
the standard Heart Rate service (0x180D) and blends the live BPM into the
strain score: BPM above `WAMSS_STRAIN_HR_RESTING` (default 90) adds up to 10
strain points (+1 per 2 BPM over resting).

Pairing steps per OS:

- Windows: Settings > Bluetooth and devices > Add device. Pair your watch or
  chest strap, then set `WAMSS_BLE_DEVICE` to part of its device name.
- Linux: run `bluetoothctl`, then `scan on`, `pair <MAC>`, `trust <MAC>`,
  `connect <MAC>`. Set `WAMSS_BLE_DEVICE` to the MAC or a name substring.

If `WAMSS_BLE_DEVICE` is empty, the agent connects to the first device
advertising the Heart Rate service. If bleak is missing, the adapter is
absent, or the device is not found, the agent logs a warning and continues
with heuristic-only strain scoring; no functionality is lost. Heart rate is
used only as a heuristic workload signal blended into the strain score. Like
the rest of the strain score, it is NOT a medical measure of stress, fatigue,
or heart health.

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
- Set the required `WAMSS_*` variables as machine/user environment variables
  or in a `.env` file next to the script (the agent reads `os.environ`).
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
