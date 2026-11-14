"""Privacy-scoped models for employee-owned wellness and self-coaching data.

Live heart-rate telemetry is intentionally not represented by a database model;
it is held in process memory and expires quickly. Durable health values are
one daily summary per employee/day and can be deleted on consent withdrawal.
"""

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class WellnessConsent(Base):
    __tablename__ = "wellness_consents"

    employee_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ManagerAlertConsent(Base):
    """Separate, revocable employee permission for generic manager check-ins."""

    __tablename__ = "wellness_manager_alert_consents"

    employee_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class WellnessDaily(Base):
    __tablename__ = "wellness_daily"
    __table_args__ = (UniqueConstraint("employee_id", "day", name="uq_wellness_daily_employee_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    day: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    rmssd_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rmssd_source: Mapped[Optional[str]] = mapped_column(String(24), nullable=True, default="manual")
    resting_hr_bpm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sleep_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DailyOutcome(Base):
    """Optional employee-entered task count; task names and contents are never stored."""

    __tablename__ = "daily_outcomes"
    __table_args__ = (UniqueConstraint("employee_id", "day", name="uq_daily_outcome_employee_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    day: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    tasks_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class BreathingSession(Base):
    """Durable session state and optional aggregates; no detailed trace is stored."""

    __tablename__ = "wellness_breathing_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    day: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed", index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    session_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    rmssd_before_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rmssd_after_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), index=True,
    )


class WellnessBreak(Base):
    """Private prompt/completion counts for the employee's daily summary."""

    __tablename__ = "wellness_break_events"
    __table_args__ = (UniqueConstraint("employee_id", "day", "event_type", name="uq_wellness_break_day_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    day: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), index=True,
    )


class PredictionFeedback(Base):
    """Employee-only record of whether a self-coaching prediction was false."""

    __tablename__ = "prediction_feedback"
    __table_args__ = (UniqueConstraint("employee_id", "prediction_key", name="uq_prediction_feedback_employee_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    prediction_key: Mapped[str] = mapped_column(String(128), nullable=False)
    false_positive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SleepSession(Base):
    """Employee-owned interval metadata; no raw sensor or sleep-stage traces."""
    __tablename__ = "wellness_sleep_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
