# shared/ - Simulation Harness

Demo tooling for WAMSS. Lets anyone explore the full dashboard and API
without cameras, workstation agents, or wearables.

## simulate.py

Seeds a running backend with realistic synthetic telemetry:

* `POST /event` - vision detections (varied cameras, zones, confidence)
* `POST /activity` - workstation activity batches (apps, windows, input
  counts, idle periods, strain scores)
* `POST /security/events` - USB insert/remove and file-write-burst events
  (metadata only, never content)

Standard library only. Timestamps are spread over the last N workdays
(weekends skipped), 09:00-18:00 UTC, with a lunch idle window.

```bash
# 1. Start the backend first (from the repo root)
cd backend
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. Dry run: see what would be posted, send nothing
python3 shared/simulate.py --dry-run

# 3. Seed 5 employees x 7 workdays against the local backend
python3 shared/simulate.py

# 4. Custom scale, target, and reproducible output
python3 shared/simulate.py --employees 10 --days 14 \
    --api-url http://192.0.2.10:8000 --seed 42 --vision-per-day 60
```

Flags: `--employees` (default 5), `--days` (default 7), `--api-url`
(default http://localhost:8000), `--seed`, `--vision-per-day` (default 40),
`--dry-run`.

After seeding, point the dashboard at the same API URL (`VITE_API_URL` in
`frontend/.env`) and explore the Events, Employee activity, Presence,
Focus, Security, Policy, and Team views.

To build a Monday summary from the seeded data:

```bash
cd backend
python3 scripts/weekly_digest.py --api-url http://localhost:8000
```
