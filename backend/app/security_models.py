"""
SQLAlchemy model for workstation security / DLP signal events.

Self-contained addition for the security module. Imports Base from
app.models so the coordinator only needs to import this module before
init_db() for the table to be created.

Privacy note: only aggregate signals are stored (device label, byte
counts, timestamps). No file content or keystroke content is ever stored.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # Office / site the workstation belongs to. server_default keeps older
    # rows (written before this column existed) working as "hq".
    office_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, default="hq", server_default="hq"
    )
    # One of: usb_insert, usb_remove, file_write_burst, after_hours_activity
    event_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    # Free-form detail payload, e.g. {"device": "USB-8GB"} or
    # {"bytes_written_mb": 640.0, "threshold_mb": 500.0}.
    detail: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
