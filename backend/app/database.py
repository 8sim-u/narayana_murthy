import logging
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Base

logger = logging.getLogger(__name__)

# SQLite (local development / test fallback) requires check_same_thread=False
# because FastAPI may hand the session to a different thread than the one that
# created it. PostgreSQL does not accept this argument, so only pass it for SQLite.
_connect_args = {}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=_connect_args,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Create all database tables if they do not already exist."""
    try:
        # Register feature models before create_all so direct CLI use gets the
        # same schema as API startup, including wellness schema migrations.
        from app import activity_models, security_models, wellness_models  # noqa: F401

        Base.metadata.create_all(bind=engine)
        # Existing installations need an additive column; create_all does not
        # alter already-created tables. Legacy values remain explicitly unknown.
        columns = {column["name"] for column in inspect(engine).get_columns("wellness_daily")}
        if "rmssd_source" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE wellness_daily ADD COLUMN rmssd_source VARCHAR(24)"))
            logger.info("Added wellness_daily.rmssd_source; legacy readings display as manual/unknown.")
        breathing_columns = {column["name"] for column in inspect(engine).get_columns("wellness_breathing_sessions")}
        with engine.begin() as conn:
            if "status" not in breathing_columns:
                conn.execute(text("ALTER TABLE wellness_breathing_sessions ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'completed'"))
            if "started_at" not in breathing_columns:
                conn.execute(text("ALTER TABLE wellness_breathing_sessions ADD COLUMN started_at TIMESTAMP WITH TIME ZONE"))
            if "session_key" not in breathing_columns:
                conn.execute(text("ALTER TABLE wellness_breathing_sessions ADD COLUMN session_key VARCHAR(64)"))
        from app.audit import ensure_audit_schema

        ensure_audit_schema()
        logger.info("Database tables initialized successfully.")
    except Exception as exc:
        logger.warning(f"Database table initialization skipped (database may be offline): {exc}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
