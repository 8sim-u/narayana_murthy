from fastapi import FastAPI

from app.config import Settings

settings = Settings()

app = FastAPI(title=settings.app_name)


@app.get("/health")
def get_health():
    return {
        "status": "ok",
        "environment": settings.environment,
    }
