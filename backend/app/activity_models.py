"""
SQLAlchemy model for workstation activity logs.

Self-contained addition for the workstation activity module. Imports Base
from app.models so the coordinator only needs to import this module before
init_db() for the table to be created.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # Office / site the workstation belongs to. server_default keeps older
    # rows (written before this column existed) working as "hq".
    office_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, default="hq", server_default="hq"
    )
    # "activity" for normal segments, "break_suggested" for break-reminder
    # events, "alert" for server-side rule alerts (e.g. HIGH_STRAIN_DETECTED).
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="activity", index=True)
    app_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    window_title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    process_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    keystrokes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mouse_clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_idle: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Heuristic workload-tension score 0-100 computed by the workstation
    # agent. This is a workload signal, not a medical measure.
    strain_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # ML behavioral stress score 0-100 from agent/ml_stress.py (round 4).
    # Populated only when the agent runs with WAMSS_STRAIN_PIPELINE=1.
    ml_stress_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
