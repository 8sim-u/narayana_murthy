#!/usr/bin/env python3
"""
WAMSS data-retention purger.

Deletes ActivityLog rows (and SecurityEvent rows when that model exists)
older than N days, where N comes from env WAMSS_RETENTION_DAYS (default 90).

Usage:
    # dry run: only print what WOULD be deleted
    python scripts/purge_old_logs.py --dry-run

    # real run
    python scripts/purge_old_logs.py

    # custom retention
    WAMSS_RETENTION_DAYS=30 python scripts/purge_old_logs.py --dry-run

Must run with the backend's DATABASE_URL available (backend/.env or env).
Run from the backend/ directory so `app` is importable.

Scheduling:
  - Linux cron (daily at 02:00):
      0 2 * * * cd /opt/wamss/backend && WAMSS_RETENTION_DAYS=90 \
          /opt/wamss/venv/bin/python scripts/purge_old_logs.py \
          >> /var/log/wamss-purge.log 2>&1
  - Windows Task Scheduler: action runs
      pythonw.exe C:\\wamss\\backend\\scripts\\purge_old_logs.py
    with "Start in" set to C:\\wamss\\backend and a WAMSS_RETENTION_DAYS
    environment variable on the task.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("wamss.retention")

# Make `app` importable when run from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _getenv_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _models_to_purge():
    """Return (label, model, timestamp_column_name) for each table to purge."""
    from app.activity_models import ActivityLog

    targets = [("activity_logs", ActivityLog, "created_at")]

    # SecurityEvent lives in app.security_models
    try:
        from app.security_models import SecurityEvent
    except ImportError:
        try:
            from app.models import SecurityEvent  # type: ignore[attr-defined]
        except (ImportError, AttributeError):
            logger.info("SecurityEvent model not present; skipping that table")
        else:
            targets.append(("security_events", SecurityEvent, "created_at"))
    else:
        targets.append(("security_events", SecurityEvent, "created_at"))

    return targets


def purge_older_than(days: int, dry_run: bool = False) -> dict:
    """
    Delete rows older than `days`. Returns {table_label: deleted_count}.
    With dry_run=True only counts and deletes nothing.
    """
    from app.database import SessionLocal, init_db
    import app.activity_models  # noqa: F401
    import app.security_models  # noqa: F401

    init_db()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results = {}
    db = SessionLocal()
    try:
        for label, model, ts_column in _models_to_purge():
            column = getattr(model, ts_column)
            query = db.query(model).filter(column < cutoff)
            if dry_run:
                count = query.count()
                logger.info("[dry-run] %s: %d rows older than %d days would be deleted",
                            label, count, days)
                results[label] = count
            else:
                count = query.delete(synchronize_session=False)
                logger.info("%s: deleted %d rows older than %d days", label, count, days)
                results[label] = count
        if not dry_run:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Purge WAMSS logs older than the retention window.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only print how many rows would be deleted; delete nothing.")
    parser.add_argument("--days", type=int, default=None,
                        help="Override WAMSS_RETENTION_DAYS for this run.")
    args = parser.parse_args(argv)

    days = args.days if args.days is not None else _getenv_int("WAMSS_RETENTION_DAYS", 90)
    if days <= 0:
        print("error: retention days must be positive", file=sys.stderr)
        return 2

    logger.info("starting purge: retention_days=%d dry_run=%s", days, args.dry_run)
    results = purge_older_than(days, dry_run=args.dry_run)
    total = sum(results.values())
    print(f"retention_days={days} dry_run={args.dry_run} tables={results} total={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
