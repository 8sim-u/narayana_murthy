from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app

client = TestClient(app)


def test_health_db_connected():
    response = client.get("/health/db")
    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "ok", "database": "connected"}


def test_health_db_failure_overridden():
    def failing_get_db():
        raise RuntimeError(
            "Connection refused: postgresql+psycopg://secret_user:secret_pass@localhost:5432/secret_db"
        )

    app.dependency_overrides[get_db] = failing_get_db
    try:
        response = client.get("/health/db")
        assert response.status_code == 503
        data = response.json()
        assert data == {"status": "error", "database": "unavailable"}
        assert "postgresql" not in response.text
        assert "secret_pass" not in response.text
        assert "secret_user" not in response.text
        assert "RuntimeError" not in response.text
    finally:
        app.dependency_overrides.clear()
