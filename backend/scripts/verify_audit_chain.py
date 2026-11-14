#!/usr/bin/env python3
"""Verify the retained workstation activity audit chain.

Run from backend/ with DATABASE_URL configured, for example:
    python scripts/verify_audit_chain.py
Exit status is 0 for a valid chain, 1 for an integrity failure, and 2 when
verification could not be completed.
"""

from __future__ import annotations

import os
import sys

# Make app importable when the script is launched from any working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.audit import verify_activity_chain
from app.database import SessionLocal, init_db


def main() -> int:
    try:
        init_db()
        with SessionLocal() as db:
            result = verify_activity_chain(db)
    except Exception as exc:
        print(
            "Audit chain verification could not complete "
            f"({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 2

    if result["valid"]:
        print(f"Audit chain verified: {result['checked_records']} activity records.")
        return 0

    event_id = result["first_broken_event_id"]
    location = f" at activity event {event_id}" if event_id is not None else ""
    print(
        f"Audit chain verification failed{location}: {result['error']}.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
