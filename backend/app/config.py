from pathlib import Path
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    app_name: str = "narayana-murthy-surveillance-api"
    environment: str = "development"
    database_url: str

    model_config = SettingsConfigDict(
        env_file=ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
    )


try:
    settings = Settings()
except ValidationError as exc:
    missing_fields = [
        err.get("loc", [""])[0]
        for err in exc.errors()
        if err.get("type") == "missing"
    ]
    if "database_url" in missing_fields:
        raise RuntimeError(
            "DATABASE_URL is required but was not provided. Please set DATABASE_URL in backend/.env or your environment."
        ) from exc
    raise
