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
