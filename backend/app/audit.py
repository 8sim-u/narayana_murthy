"""Tamper-evident hash chain for workstation activity records.

The append head protects the newest event. A separate retention checkpoint
anchors the first retained event after an oldest-prefix purge. The chain is
not a signature: anyone able to rewrite the entire database, including both
head and checkpoint, can rebuild it.

Chain coverage decision: only activity_logs is chained. Detection, security,
geofence, and wellness events live in separate tables with their own
retention and access rules, and none of them is purged as an oldest prefix,
which is the case the checkpoint and head design exists to anchor. Chaining
them would need a head per table plus cross-table ordering, with no added
tamper evidence for the purge case. If a prefix purge is ever added for
another event table, give that table its own chain following this same
pattern instead of widening this one.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.activity_models import ActivityAuditHead, ActivityLog
from app.database import SessionLocal, engine

logger = logging.getLogger(__name__)
GENESIS_HASH = "0" * 64
AUDIT_HEAD_ID = 1
_HASHED_FIELDS = (
    "id",
    "machine_id",
    "employee_id",
    "office_id",
    "event_type",
    "app_name",
    "window_title",
    "process_name",
    "start_time",
    "end_time",
    "keystrokes",
    "mouse_clicks",
    "is_idle",
    "strain_score",
    "ml_stress_score",
    "created_at",
)


class AuditChainError(RuntimeError):
    """Raised when the activity audit chain cannot be extended or purged safely."""


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return value


def activity_event_hash(record: ActivityLog, previous_hash: str) -> str:
    """Return the SHA-256 hash of the previous link and the stored event data."""
    event_data = {
        field: _canonical_value(getattr(record, field))
        for field in _HASHED_FIELDS
    }
    canonical = json.dumps(
        {"previous_hash": previous_hash, "event": event_data},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _is_hash(value: Optional[str]) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(char in "0123456789abcdef" for char in value)
    )


def _legacy_chain_is_complete(db: Session, head: ActivityAuditHead) -> bool:
    """Allow a new checkpoint only when the old database proves a genesis chain."""
    records = db.query(ActivityLog).order_by(ActivityLog.id.asc()).all()
    if not records:
        return head.last_event_id is None and head.last_hash == GENESIS_HASH

    previous_hash = GENESIS_HASH
    for record in records:
        if record.previous_hash != previous_hash or not record.event_hash:
            return False
        if record.event_hash != activity_event_hash(record, previous_hash):
            return False
        previous_hash = record.event_hash
    return head.last_event_id == records[-1].id and head.last_hash == previous_hash


def ensure_audit_schema() -> None:
    """Create/migrate audit metadata without inventing missing chain history.

    Existing unchained records are hashed once, in primary-key order, on the
    first upgrade. A checkpoint is initialized for an existing chain only when
    all current events demonstrably form a complete genesis-based chain. Any
    prior unanchored purge or partial chain requires operator investigation.
    """
    ActivityAuditHead.metadata.create_all(
        bind=engine, tables=[ActivityAuditHead.__table__]
    )
    with engine.begin() as connection:
        if not inspect(connection).has_table(ActivityLog.__tablename__):
            raise RuntimeError("activity_logs table is not available")
        activity_columns = {
            column["name"]
            for column in inspect(connection).get_columns(ActivityLog.__tablename__)
        }
        for name in ("previous_hash", "event_hash"):
            if name in activity_columns:
                continue
            column_type = ActivityLog.__table__.c[name].type.compile(
                dialect=engine.dialect
            )
            connection.execute(
                text(f"ALTER TABLE activity_logs ADD COLUMN {name} {column_type}")
            )
            logger.info("added missing column activity_logs.%s", name)

        head_columns = {
            column["name"]
            for column in inspect(connection).get_columns(ActivityAuditHead.__tablename__)
        }
        for name, column_type in (
            ("pruned_through_event_id", "INTEGER NULL"),
            ("pruned_through_hash", "VARCHAR(64) NULL"),
        ):
            if name in head_columns:
                continue
            connection.execute(
                text(f"ALTER TABLE activity_audit_head ADD COLUMN {name} {column_type}")
            )
            logger.info("added missing column activity_audit_head.%s", name)

    db = SessionLocal()
    try:
        head = (
            db.query(ActivityAuditHead)
            .filter(ActivityAuditHead.id == AUDIT_HEAD_ID)
            .one_or_none()
        )
        if head is None:
            any_hash = (
                db.query(ActivityLog.id)
                .filter(
                    ActivityLog.previous_hash.is_not(None)
                    | ActivityLog.event_hash.is_not(None)
                )
                .first()
            )
            if any_hash is not None:
                raise AuditChainError(
                    "activity audit chain head is missing; refusing to reconstruct it"
                )
            last_id, last_hash = _backfill_legacy_records(db)
            db.add(
                ActivityAuditHead(
                    id=AUDIT_HEAD_ID,
                    last_event_id=last_id,
                    last_hash=last_hash,
                    pruned_through_event_id=None,
                    pruned_through_hash=GENESIS_HASH,
                )
            )
            db.commit()
            return

        # A NULL/NULL checkpoint indicates an older schema or incomplete
        # migration. Bootstrap it only if no prior prefix is missing.
        if head.pruned_through_event_id is None and head.pruned_through_hash is None:
            if not _legacy_chain_is_complete(db, head):
                raise AuditChainError(
                    "activity retention checkpoint is missing and the complete "
                    "genesis-based chain cannot be established"
                )
            head.pruned_through_hash = GENESIS_HASH
            db.commit()

        result = verify_activity_chain(db)
        if not result["valid"]:
            raise AuditChainError(
                f"activity audit chain is invalid after schema initialization: {result['error']}"
            )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _backfill_legacy_records(db: Session) -> tuple[Optional[int], str]:
    """Create the initial chain for records predating the hash columns."""
    previous_hash = GENESIS_HASH
    last_id: Optional[int] = None
    after_id: Optional[int] = None
    while True:
        query = db.query(ActivityLog).order_by(ActivityLog.id.asc())
        if after_id is not None:
            query = query.filter(ActivityLog.id > after_id)
        records = query.limit(500).all()
        if not records:
            break
        for record in records:
            record.previous_hash = previous_hash
            record.event_hash = activity_event_hash(record, previous_hash)
            previous_hash = record.event_hash
            last_id = record.id
        after_id = records[-1].id
        db.flush()
    return last_id, previous_hash


def lock_activity_chain_head(db: Session) -> ActivityAuditHead:
    """Serialize append and retention transactions on the singleton head row."""
    try:
        # The caller may already have pending ActivityLog objects. Do not let
        # this lock operation flush them before the head is serialized.
        with db.no_autoflush:
            updated = (
                db.query(ActivityAuditHead)
                .filter(ActivityAuditHead.id == AUDIT_HEAD_ID)
                .update(
                    {ActivityAuditHead.last_hash: ActivityAuditHead.last_hash},
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise AuditChainError("activity audit chain head is not initialized")
            head = (
                db.query(ActivityAuditHead)
                .filter(ActivityAuditHead.id == AUDIT_HEAD_ID)
                .one_or_none()
            )
    except SQLAlchemyError as exc:
        raise AuditChainError("unable to lock activity audit chain head") from exc
    if head is None or not head.last_hash:
        raise AuditChainError("activity audit chain head is invalid")
    return head


def append_activity_records(
    db: Session,
    records: Iterable[ActivityLog],
    head: ActivityAuditHead,
) -> None:
    """Hash and append pending activity records in their database order.

    Callers must lock the head before flushing the records. Pending records
    may already be in the session. The head lock, event rows, and updated
    hashes are committed by the caller in one transaction.
    """
    ordered_records = list(records)
    if not ordered_records:
        return
    db.flush()
    ordered_records.sort(key=lambda record: record.id)
    previous_hash = head.last_hash
    for record in ordered_records:
        record.previous_hash = previous_hash
        record.event_hash = activity_event_hash(record, previous_hash)
        previous_hash = record.event_hash
    head.last_event_id = ordered_records[-1].id
    head.last_hash = previous_hash
    db.flush()


def purge_activity_prefix(db: Session, cutoff: datetime, dry_run: bool = False) -> int:
    """Delete only an expired, contiguous oldest prefix and advance its anchor.

    The audit head, checkpoint update, and row deletion share the caller's
    transaction. Expired rows after any non-expired row cannot be deleted
    safely without creating an unrepresented gap, so that case fails closed.
    """
    head = lock_activity_chain_head(db)
    current = verify_activity_chain(db)
    if not current["valid"]:
        raise AuditChainError(
            f"refusing to purge an unverifiable activity chain: {current['error']}"
        )

    last_expired = (
        db.query(ActivityLog)
        .filter(ActivityLog.created_at < cutoff)
        .order_by(ActivityLog.id.desc())
        .first()
    )
    if last_expired is None:
        return 0

    blocker = (
        db.query(ActivityLog.id)
        .filter(
            ActivityLog.id < last_expired.id,
            ActivityLog.created_at >= cutoff,
        )
        .first()
    )
    if blocker is not None:
        raise AuditChainError(
            "expired activity events are not an oldest contiguous prefix; "
            "refusing to create a gap in the audit chain"
        )

    prefix = db.query(ActivityLog).filter(ActivityLog.id <= last_expired.id)
    count = prefix.count()
    if dry_run:
        return count

    head.pruned_through_event_id = last_expired.id
    head.pruned_through_hash = last_expired.event_hash
    deleted = prefix.delete(synchronize_session=False)
    if deleted != count:
        raise AuditChainError("activity prefix changed during retention purge")
    db.flush()

    after = verify_activity_chain(db)
    if not after["valid"]:
        raise AuditChainError(
            f"activity chain did not verify after purge: {after['error']}"
        )
    return deleted


def verify_activity_chain(db: Session) -> dict[str, Any]:
    """Verify retained event hashes against the pruning checkpoint and append head."""
    records = db.query(ActivityLog).order_by(ActivityLog.id.asc()).all()
    head = (
        db.query(ActivityAuditHead)
        .filter(ActivityAuditHead.id == AUDIT_HEAD_ID)
        .one_or_none()
    )

    def failure(index: int, event_id: Optional[int], error: str) -> dict[str, Any]:
        return {
            "valid": False,
            "checked_records": index,
            "first_broken_event_id": event_id,
            "error": error,
        }

    if head is None:
        return failure(
            0, records[0].id if records else None,
            "activity audit chain head is missing",
        )

    checkpoint_id = head.pruned_through_event_id
    checkpoint_hash = head.pruned_through_hash
    if checkpoint_hash is None:
        return failure(
            0, records[0].id if records else None,
            "activity retention checkpoint is missing",
        )
    if checkpoint_id is None and checkpoint_hash != GENESIS_HASH:
        return failure(
            0, records[0].id if records else None,
            "activity retention checkpoint is malformed",
        )
    if checkpoint_id is not None and (
        checkpoint_id <= 0 or not _is_hash(checkpoint_hash)
    ):
        return failure(
            0, records[0].id if records else None,
            "activity retention checkpoint is malformed",
        )
    if head.last_event_id is None:
        if head.last_hash != GENESIS_HASH:
            return failure(0, None, "empty activity audit head has a non-genesis hash")
    elif head.last_event_id <= 0 or not _is_hash(head.last_hash):
        return failure(0, records[-1].id if records else None, "activity audit head is malformed")
    if checkpoint_id is not None and (
        head.last_event_id is None or checkpoint_id > head.last_event_id
    ):
        return failure(
            0, records[0].id if records else None,
            "activity retention checkpoint is beyond the append head",
        )

    if not records:
        if (
            head.last_event_id != checkpoint_id
            or head.last_hash != checkpoint_hash
        ):
            return failure(
                0, None,
                "activity logs are empty but the append head is not covered by "
                "the retention checkpoint",
            )
        return {
            "valid": True,
            "checked_records": 0,
            "first_broken_event_id": None,
            "error": None,
        }

    first = records[0]
    if checkpoint_id is not None and first.id <= checkpoint_id:
        return failure(
            0, first.id,
            "first retained event does not follow the retention checkpoint",
        )
    if first.previous_hash != checkpoint_hash:
        return failure(
            0, first.id,
            "first retained event does not match the retention checkpoint",
        )

    previous_record_hash = checkpoint_hash
    for index, record in enumerate(records):
        if not record.previous_hash or not record.event_hash:
            return failure(index, record.id, "missing previous or event hash")
        if record.previous_hash != previous_record_hash:
            error = (
                "first retained event does not match the retention checkpoint"
                if index == 0
                else "previous hash does not match the preceding retained event"
            )
            return failure(index, record.id, error)
        expected_hash = activity_event_hash(record, record.previous_hash)
        if record.event_hash != expected_hash:
            return failure(index, record.id, "event data hash does not match")
        previous_record_hash = record.event_hash

    if (
        head.last_event_id != records[-1].id
        or head.last_hash != records[-1].event_hash
    ):
        return failure(
            len(records), records[-1].id,
            "activity audit chain head does not match the latest retained event",
        )
    return {
        "valid": True,
        "checked_records": len(records),
        "first_broken_event_id": None,
        "error": None,
    }
