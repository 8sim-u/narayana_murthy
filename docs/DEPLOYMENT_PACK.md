# WAMSS Deployment Pack Runbook

End-to-end guide for bringing WAMSS up on a fresh machine, from bare host to
workstations reporting in. Every script lives in `deploy/`; this document is
the order of operations.

Assumed layout: the repository is checked out on the server machine, and you
work from the repo root (`~/workspace/narayana_murthy` or wherever you cloned
it).

Prerequisites at a glance: Docker with the Compose plugin, Python 3.10+
(useful for the simulator and preflight), ports 8000 and 8080 free.

---

## Step 1: Preflight

Run the checklist first. It fails fast on anything that would break the
deploy, and only warns on nice-to-haves (like a BLE adapter).

```bash
bash deploy/preflight.sh
```

Windows (PowerShell):

```powershell
.\deploy\preflight.ps1
```

What it checks:

| Check | Critical | Notes |
|---|---|---|
| python3 >= 3.10 | yes | needed for the simulator and preflight itself |
| docker present and daemon reachable | yes | |
| docker compose plugin | yes | the plugin (`docker compose`), not standalone `docker-compose` |
| >= 5 GB free disk space | yes | images, volumes, spool growth |
| ports 8000 and 8080 free | yes | backend API and dashboard |
| BLE adapter present | no (warning) | needed only for the optional smartwatch heart-rate module |

Fix any `[FAIL]` lines, then re-run until you see "Preflight OK".

## Step 2: Deploy the stack

```bash
bash deploy/deploy.sh
```

Windows (PowerShell):

```powershell
.\deploy\deploy.ps1
```

This:

1. Re-checks docker, the compose plugin, and that ports 8000/8080 are free.
2. On the **first run only**, creates `.env` from `.env.example` and generates
   a random `WAMSS_API_KEY` (printed once; it is saved in `.env`). If `.env`
   already exists it is never touched, and secrets are never committed.
3. Builds and starts the compose stack in the background.
4. Polls `GET http://127.0.0.1:8000/health` (up to 3 minutes) and only then
   reports success.
5. Prints the dashboard URL and next steps.

Idempotent: re-running rebuilds and restarts the stack (for example after a
`git pull`) while preserving `.env` and the `backend-data` / `pgdata` volumes.

Useful variants:

```bash
bash deploy/deploy.sh --no-build                 # restart without rebuilding
bash deploy/deploy.sh --profile postgres         # also start the Postgres service
                                                 # (requires DATABASE_URL set in .env)
```

When finished you have:

- Dashboard: http://localhost:8080
- Backend API: http://localhost:8000 (`/health` for liveness, `/docs` for API docs)

## Step 3: Verify (health endpoints + seeded demo)

1. Health:

```bash
curl http://localhost:8000/health
```

Expect HTTP 200 with a status payload.

2. Seed demo data with the simulator (standard library only, no extra install):

```bash
# Without API key auth (default when WAMSS_API_KEY is empty):
python3 shared/simulate.py --api-url http://localhost:8000

# With the generated key from step 2:
python3 shared/simulate.py --api-url http://localhost:8000 \
    --api-key $(grep '^WAMSS_API_KEY=' .env | cut -d= -f2-)
```

This seeds 5 employees x 7 days of activity, security, and vision events.
Then open http://localhost:8080 and confirm the dashboard shows the seeded
employees, activity, and security data. A dry run that posts nothing:

```bash
python3 shared/simulate.py --dry-run
```

3. Quick API smoke test (replace `<key>` with your key if auth is enabled):

```bash
curl -H "X-API-Key: <key>" http://localhost:8000/activity?limit=1
```

## Step 4: Install agents on workstations

Employee notice and consent are required before installing the agent on any
workstation. See `agent/README.md` ("Privacy and consent") for what the agent
collects (app names, window titles, input *counts*, idle time, strain
heuristic; never key content, clipboard, or screenshots).

Linux workstation:

```bash
bash deploy/install-agent.sh
```

You will be prompted for the backend server URL (use the server's LAN address,
e.g. `http://192.0.2.10:8000`, not localhost), machine id, employee id, and
office id. This installs a systemd user service (`wamss-agent`) enabled at
login. Non-interactive form:

```bash
bash deploy/install-agent.sh --server-url http://192.0.2.10:8000 \
    --machine-id workstation-042 --employee-id employee-042 --office-id hq
```

Windows workstation (run PowerShell **as administrator**):

```powershell
.\deploy\install-agent.ps1
```

This copies the agent sources, builds a venv, installs
`agent/requirements.txt`, writes `config.env`, and registers the hidden
at-logon scheduled task "WAMSS Activity Agent" (runs `pythonw.exe`, so no
console window appears).

After installing on each workstation:

- Linux: `systemctl --user status wamss-agent`
- Windows: on first run the agent shows a graphical consent dialog; nothing
  is logged until the employee answers Yes. Then check
  `C:\ProgramData\WAMSS\agent\spool\pending.jsonl` for fresh records.
- Confirm the machine id appears in the dashboard.

Uninstall later with:

```bash
bash deploy/install-agent.sh --uninstall
```

```powershell
.\deploy\install-agent.ps1 -Uninstall
```

## Step 5: Pair the smartwatch (optional)

The heart-rate module is optional; without it the agent keeps working with
heuristic-only strain scoring.

- Windows: Settings > Bluetooth and devices > Add device. Pair the watch or
  chest strap, then set `WAMSS_BLE_DEVICE` to part of its device name in the
  agent's `config.env` (Windows) or `~/.config/wamss-agent.env` (Linux), and
  set `WAMSS_BLE_ENABLED=1`.
- Linux: `bluetoothctl` -> `scan on`, `pair <MAC>`, `trust <MAC>`,
  `connect <MAC>`. Set `WAMSS_BLE_DEVICE` to the MAC or a name substring.

If `WAMSS_BLE_DEVICE` is empty, the agent connects to the first device
advertising the standard Heart Rate service (0x180D). Heart rate only nudges
the strain heuristic (BPM above `WAMSS_STRAIN_HR_RESTING`, default 90, adds up
to 10 strain points); it is not a medical measure. See `agent/README.md` for
the full BLE section.

## Step 6: Configure cameras and zones

Vision events carry `camera_id` and `zone`. Two things to configure:

1. **Zone to machine mapping** (used for presence): the backend reads
   `WAMSS_ZONE_MAP`, a JSON object mapping camera zone to machine id, e.g.:

```json
{"RESTRICTED": "workstation-042", "LOBBY": "workstation-043"}
```

2. **Restricted-zone rule**: the built-in rule engine flags
   `RESTRICTED_ZONE_DETECTED` whenever a detection event arrives with
   `zone` set to `RESTRICTED` (case-insensitive). Name the camera zones in
   your vision pipeline accordingly.

How to pass these to the container: the compose file only forwards
`DATABASE_URL` and `WAMSS_API_KEY` by default. Add any extra backend variables
(like `WAMSS_ZONE_MAP`) to your `.env`, then add one line to the `backend`
service in `docker-compose.yml`:

```yaml
    env_file:
      - .env
```

Then re-run `bash deploy/deploy.sh` (your `.env` is preserved; the stack is
rebuilt with the new variables).

## Step 7: SMTP for digests and real-time alerts

Two separate mail paths:

**Real-time alerts** (strain and security anomalies, `backend/app/alerts.py`).
Set in `.env` (and expose to the container as in step 6):

```env
WAMSS_ALERT_EMAIL_ENABLED=1
WAMSS_ALERT_SMTP_HOST=smtp.example.com
WAMSS_ALERT_SMTP_PORT=587
WAMSS_ALERT_SMTP_USER=alerts@example.com
WAMSS_ALERT_SMTP_PASS=<app password>
WAMSS_ALERT_SMTP_FROM=alerts@example.com
WAMSS_ALERT_EMAIL_TO=security@example.com,hr@example.com
```

Telegram and webhook channels are also supported (`WAMSS_TELEGRAM_BOT_TOKEN` /
`WAMSS_TELEGRAM_CHAT_ID`, `WAMSS_WEBHOOK_URL`); see `docs/ALERTS.md`.

**Weekly digest** (`backend/scripts/weekly_digest.py --send`). It pulls live
data from the API and sends via SMTP from env or CLI flags:

```bash
WAMSS_API_URL=http://localhost:8000 \
WAMSS_API_KEY=$(grep '^WAMSS_API_KEY=' .env | cut -d= -f2-) \
WAMSS_SMTP_HOST=smtp.example.com WAMSS_SMTP_PORT=587 \
WAMSS_SMTP_USER=alerts@example.com WAMSS_SMTP_PASS=<app password> \
WAMSS_SMTP_FROM=alerts@example.com WAMSS_SMTP_TO=team@example.com \
python3 backend/scripts/weekly_digest.py --send
```

Dry-run first (renders the digest without sending):

```bash
python3 backend/scripts/weekly_digest.py --dry-run
```

Tip: use an app-specific password, never the mailbox login password, and keep
these values in `.env` (gitignored), never in the repo.

## Step 8: Backup schedule

`deploy/backup.sh` writes a timestamped archive to `./backups/` (SQLite
volume `.tgz`, or Postgres `.sql` when the postgres profile is in use).
Restore with `deploy/restore.sh <archive>` (replaces the current database;
it asks for confirmation unless `--yes` is passed).

Recommended schedule: **nightly backup, keep 14 days**. Examples:

cron (runs daily at 02:30 as the deploy user):

```cron
30 2 * * * cd /path/to/narayana_murthy && bash deploy/backup.sh --out-dir /var/backups/wamss >> /var/log/wamss-backup.log 2>&1
14 2 * * * find /var/backups/wamss -name 'wamss-*-backup-*.tgz' -mtime +14 -delete
```

systemd timer (user-level alternative). `~/.config/systemd/user/wamss-backup.service`:

```ini
[Unit]
Description=WAMSS nightly database backup

[Service]
Type=oneshot
WorkingDirectory=%h/narayana_murthy
ExecStart=%h/narayana_murthy/deploy/backup.sh --out-dir %h/wamss-backups
```

`~/.config/systemd/user/wamss-backup.timer`:

```ini
[Unit]
Description=Run WAMSS backup daily at 02:30

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now wamss-backup.timer
```

Also copy archives off the machine (object storage, NAS, or a second disk);
a backup on the same disk protects against accidents, not disk failure.

---

## Troubleshooting

**Port already in use (8000 or 8080).** Preflight and `deploy.sh` both fail
with `[FAIL] port ... is already in use`. Find the culprit
(`ss -tlnp | grep 8000` on Linux; `netstat -ano | findstr 8000` on Windows),
stop it, or change the host side of the port mapping in
`docker-compose.yml` (e.g. `"8081:80"` for the dashboard). If you remap the
backend port, the dashboard's `VITE_API_URL` must match the new port and the
frontend needs a rebuild (`docker compose build frontend`).

**Missing `.env` / "DATABASE_URL is required".** `deploy.sh` creates `.env`
automatically on first run. If you see this error anyway, your `.env` was
deleted or you are in the wrong directory; re-run `bash deploy/deploy.sh`
from the repo root. Never hand-write secrets into tracked files.

**Docker permission denied (Linux).** The docker daemon socket usually
requires root or membership in the `docker` group:
`sudo usermod -aG docker $USER`, then log out and back in (or run the deploy
with `sudo -E`). Preflight reports "docker daemon not reachable" in this case.

**BLE adapter not found.** Preflight prints a `[WARN]`, not a failure. The
agent installs and runs fine; strain scoring just stays heuristic-only.
If you do want the smartwatch module later, add a BLE adapter (or enable
Bluetooth on the host), pair the device per step 5, and set
`WAMSS_BLE_ENABLED=1` plus `WAMSS_BLE_DEVICE` in the agent config.

**Backend container exits immediately.** `docker compose logs backend`. The
most common cause is an unreachable `DATABASE_URL` (typo in the Postgres URL,
or the postgres service not started with `--profile postgres`).

**Dashboard loads but shows no data.** The frontend was built with the wrong
`VITE_API_URL` (it is baked in at build time; it must be a URL the *browser*
can reach). Fix `.env`, then `docker compose build frontend` and re-run
`deploy.sh`.

**Agent installs but nothing syncs.** Check the spool file for fresh records
(`spool/pending.jsonl` in the install dir). If records spool but never
upload, the server URL is wrong or unreachable from the workstation, or the
API key is missing/mismatched when auth is enabled. If the spool stays empty,
check the consent marker: the agent does nothing until consent is recorded
(graphical dialog on Windows, typed `YES` on Linux console).

**`docker compose` says "unknown shorthand flag" or similar.** You have the
old standalone `docker-compose` (v1) rather than the Compose plugin (v2).
Update Docker Desktop / the docker-ce package.

---

## File map

| Path | Purpose |
|---|---|
| `deploy/preflight.sh` / `deploy/preflight.ps1` | readiness checklist (step 1) |
| `deploy/deploy.sh` / `deploy/deploy.ps1` | one-command stack deploy (step 2) |
| `deploy/install-agent.sh` | workstation agent installer, Linux (step 4) |
| `deploy/install-agent.ps1` | workstation agent installer, Windows (step 4) |
| `deploy/backup.sh` | timestamped database backup (step 8) |
| `deploy/restore.sh` | restore a backup archive |
| `docs/DEPLOY.md` | Docker reference (env vars, SQLite vs Postgres, simulator) |
| `docs/AUTH.md` | API key auth details |
| `docs/ALERTS.md` | real-time alert channels |
| `docs/ANALYTICS.md` | CSV exports, trends, PDF weekly report |
| `agent/README.md` | agent install/run reference, BLE pairing, consent |
