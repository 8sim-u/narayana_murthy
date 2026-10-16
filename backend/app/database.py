import logging
from sqlalchemy import create_engine
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
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables initialized successfully.")
    except Exception as exc:
        logger.warning(f"Database table initialization skipped (database may be offline): {exc}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
