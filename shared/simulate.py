#!/usr/bin/env python3
"""WAMSS demo data simulator.

Seeds a running WAMSS backend with realistic simulated telemetry so the full
dashboard can be demoed without cameras, workstation agents, or wearables.

What it posts:

* POST /event            vision detections (varied cameras, zones, confidence)
* POST /activity          workstation activity batches (apps, windows, input
                         counts, idle periods, strain scores)
* POST /security/events   USB and file-write-burst events (metadata only)

Only the Python standard library is used. Timestamps are spread over the last
``--days`` days, 09:00-18:00 UTC each day, with a lunch idle window.

Usage:

    # dry run: show what would be posted, send nothing
    python3 shared/simulate.py --dry-run

    # seed 5 employees x 7 days against the local backend
    python3 shared/simulate.py

    # custom scale and target
    python3 shared/simulate.py --employees 10 --days 14 \\
        --api-url http://192.0.2.10:8000 --seed 42

The backend must be running first (see docs/LOCAL_SETUP.md).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

APPS = [
    ("chrome.exe", "Google Chrome",
     ["Inbox - Gmail", "GitHub - pull requests", "Stack Overflow - python",
      "WAMSS Dashboard", "Docs - project plan"]),
    ("code.exe", "Visual Studio Code",
     ["main.py - narayana_murthy", "weekly_digest.py - backend",
      "simulate.py - shared", "Terminal - bash"]),
    ("slack.exe", "Slack",
     ["#team-standup", "#general", "#random", "Direct message - Priya"]),
    ("outlook.exe", "Outlook",
     ["Inbox (3)", "Calendar - Sprint planning", "Drafts"]),
    ("teams.exe", "Microsoft Teams",
     ["Daily standup meeting", "Sprint review", "1:1 with manager"]),
    ("explorer.exe", "File Explorer",
     ["Documents", "Downloads", "Project files"]),
    ("notepad.exe", "Notepad",
     ["notes.txt", "todo.txt"]),
]

CAMERAS = ["CAM_01", "CAM_02", "CAM_03"]
ZONES = ["OPEN", "OPEN", "OPEN", "LOBBY", "RESTRICTED"]
USB_DEVICES = ["SanDisk Ultra 32GB", "Kingston DataTraveler 16GB", "Logitech mouse"]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def post_json(url: str, payload: dict, timeout: float = 15.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8") or "{}"
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"POST {url} -> HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"POST {url} failed: {exc}") from exc


def workdays(days: int, now: datetime):
    """Yield date objects for the last `days` days (skip weekends), oldest first."""
    out = []
    d = now.date()
    while len(out) < days:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def gen_vision_events(rng: random.Random, day, count: int):
    events = []
    for _ in range(count):
        hour = rng.randint(9, 17)
        minute = rng.randint(0, 59)
        ts = datetime(day.year, day.month, day.day, hour, minute,
                      tzinfo=timezone.utc)
        zone = rng.choice(ZONES)
        events.append({
            "camera_id": rng.choice(CAMERAS),
            "track_id": str(rng.randint(1, 40)),
            "objects": ["person"],
            "confidence": round(rng.uniform(0.62, 0.99), 2),
            "zone": zone,
            # timestamp is informational only; the API stamps its own
            "_sim_time": iso(ts),
        })
    return events


def gen_activity(rng: random.Random, employee_id: str, machine_id: str, day):
    """Build one workday of activity records for an employee."""
    records = []
    cursor = datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc)
    end_of_day = datetime(day.year, day.month, day.day, 18, 0, tzinfo=timezone.utc)
    continuous_min = 0  # for a rough strain model

    while cursor < end_of_day:
        # lunch window: idle
        if datetime(day.year, day.month, day.day, 12, 30,
                     tzinfo=timezone.utc) <= cursor < datetime(
                         day.year, day.month, day.day, 13, 15, tzinfo=timezone.utc):
            span = 45
            records.append({
                "machine_id": machine_id, "employee_id": employee_id,
                "event_type": "activity", "app_name": "", "window_title": "",
                "process_name": "", "start_time": iso(cursor),
                "end_time": iso(cursor + timedelta(minutes=span)),
                "keystrokes": 0, "mouse_clicks": 0, "is_idle": True,
                "strain_score": 0,
            })
            cursor += timedelta(minutes=span)
            continuous_min = 0
            continue

        span = rng.randint(10, 40)
        seg_end = min(cursor + timedelta(minutes=span), end_of_day)
        idle = rng.random() < 0.08
        app_exe, _, titles = rng.choice(APPS)
        mins = max(1, int((seg_end - cursor).total_seconds() // 60))
        if idle:
            ks, clicks, score = 0, 0, 0
            continuous_min = 0
        else:
            ks = mins * rng.randint(20, 80)
            clicks = mins * rng.randint(5, 25)
            continuous_min += mins
            score = min(100, int(continuous_min * rng.uniform(0.5, 0.9)))
        records.append({
            "machine_id": machine_id, "employee_id": employee_id,
            "event_type": "activity", "app_name": app_exe,
            "window_title": "" if idle else rng.choice(titles),
            "process_name": "" if idle else app_exe,
            "start_time": iso(cursor), "end_time": iso(seg_end),
            "keystrokes": ks, "mouse_clicks": clicks, "is_idle": idle,
            "strain_score": score,
        })
        cursor = seg_end
    return records


def gen_security(rng: random.Random, employee_id: str, machine_id: str, day):
    events = []
    if rng.random() < 0.25:
        ts = datetime(day.year, day.month, day.day, rng.randint(9, 16),
                      rng.randint(0, 59), tzinfo=timezone.utc)
        events.append({
            "machine_id": machine_id, "employee_id": employee_id,
            "type": "usb_insert",
            "detail": {"device_label": rng.choice(USB_DEVICES),
                       "direction": "insert"},
            "timestamp": iso(ts),
        })
    if rng.random() < 0.15:
        ts = datetime(day.year, day.month, day.day, rng.randint(10, 17),
                      rng.randint(0, 59), tzinfo=timezone.utc)
        events.append({
            "machine_id": machine_id, "employee_id": employee_id,
            "type": "file_write_burst",
            "detail": {"bytes_written_mb": round(rng.uniform(600, 2400), 1)},
            "timestamp": iso(ts),
        })
    if rng.random() < 0.10:
        ts = datetime(day.year, day.month, day.day, rng.randint(21, 23),
                      rng.randint(0, 59), tzinfo=timezone.utc)
        events.append({
            "machine_id": machine_id, "employee_id": employee_id,
            "type": "after_hours_activity",
            "detail": {"note": "simulated late session"},
            "timestamp": iso(ts),
        })
    return events


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Seed a running WAMSS backend with simulated demo data.")
    p.add_argument("--employees", type=int, default=5,
                   help="Number of simulated employees (default 5).")
    p.add_argument("--days", type=int, default=7,
                   help="Workdays of history to generate (default 7).")
    p.add_argument("--api-url", default="http://localhost:8000",
                   help="WAMSS API base URL (default http://localhost:8000).")
    p.add_argument("--seed", type=int, default=None,
                   help="Random seed for reproducible output.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be posted without sending anything.")
    p.add_argument("--vision-per-day", type=int, default=40,
                   help="Vision events per day (default 40).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    rng = random.Random(args.seed)
    api = args.api_url.rstrip("/")
    now = datetime.now(timezone.utc)
    days = workdays(args.days, now)

    vision_total = 0
    activity_total = 0
    security_total = 0

    try:
        # vision events are team-wide: post once per day, not per employee
        for day in days:
            for ev in gen_vision_events(rng, day, args.vision_per_day):
                payload = {k: v for k, v in ev.items() if not k.startswith("_")}
                if not args.dry_run:
                    post_json(f"{api}/event", payload)
                vision_total += 1
        print(f"seeded vision: {len(days)} days x {args.vision_per_day} events",
              flush=True)

        for i in range(args.employees):
            eid = f"E{i + 1:03d}"
            mid = f"MACHINE_{i + 1:02d}"
            for day in days:
                # activity batches of 100
                records = gen_activity(rng, eid, mid, day)
                for j in range(0, len(records), 100):
                    batch = {"records": records[j:j + 100]}
                    if not args.dry_run:
                        post_json(f"{api}/activity", batch)
                    activity_total += len(batch["records"])

                # security events
                sec = gen_security(rng, eid, mid, day)
                if sec:
                    if not args.dry_run:
                        post_json(f"{api}/security/events", {"events": sec})
                    security_total += len(sec)

            print(f"seeded {eid} ({mid}): {len(days)} workdays", flush=True)
    except RuntimeError as exc:
        print(f"Simulation failed: {exc}", file=sys.stderr)
        print("Is the API running? See docs/LOCAL_SETUP.md.", file=sys.stderr)
        return 1

    mode = "DRY RUN" if args.dry_run else "POSTED"
    print(f"{mode}: vision events={vision_total} "
          f"activity records={activity_total} security events={security_total}")
    if not args.dry_run:
        print(f"Open the dashboard and point VITE_API_URL at {api} "
              f"to explore the demo data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
