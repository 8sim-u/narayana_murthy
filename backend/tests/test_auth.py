"""Tests for backend/app/auth.py (API key auth middleware).

The middleware is mounted on a standalone test FastAPI app here, so these
tests do not depend on backend/app/main.py or its registration patch.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import (
    API_KEY_ENV_VAR,
    API_KEY_HEADER,
    APIKeyAuthMiddleware,
    build_api_key_headers,
)

TEST_KEY = "wamss-test-key-12345"


def make_app(**middleware_kwargs):
    """Build a minimal app with the auth middleware mounted."""
    app = FastAPI()

    @app.get("/protected")
    def protected():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "online"}

    @app.get("/health/db")
    def health_db():
        return {"status": "ok"}

    app.add_middleware(APIKeyAuthMiddleware, **middleware_kwargs)
    return app


def client_for(monkeypatch, **middleware_kwargs):
    return TestClient(make_app(**middleware_kwargs))


# ---- auth enabled (WAMSS_API_KEY set) ---------------------------------


def test_correct_key_allows_access(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, TEST_KEY)
    client = client_for(monkeypatch)
    resp = client.get("/protected", headers={API_KEY_HEADER: TEST_KEY})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_missing_key_returns_401(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, TEST_KEY)
    client = client_for(monkeypatch)
    resp = client.get("/protected")
    assert resp.status_code == 401
    assert "API key" in resp.json()["detail"]


def test_wrong_key_returns_401(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, TEST_KEY)
    client = client_for(monkeypatch)
    resp = client.get("/protected", headers={API_KEY_HEADER: "nope-wrong-key"})
    assert resp.status_code == 401
    assert "API key" in resp.json()["detail"]


def test_exempt_paths_open_without_key(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, TEST_KEY)
    client = client_for(monkeypatch)
    for path in ("/health", "/health/db", "/docs", "/openapi.json", "/redoc"):
        resp = client.get(path)
        assert resp.status_code == 200, f"exempt path {path} blocked: {resp.status_code}"


def test_unauthorized_body_is_json(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, TEST_KEY)
    client = client_for(monkeypatch)
    resp = client.get("/protected")
    assert resp.headers["content-type"].startswith("application/json")


# ---- auth disabled (WAMSS_API_KEY unset) --------------------------------


def test_everything_open_when_env_unset(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    client = client_for(monkeypatch)
    resp = client.get("/protected")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_empty_env_value_disables_auth(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "")
    client = client_for(monkeypatch)
    resp = client.get("/protected")
    assert resp.status_code == 200


# ---- middleware construction --------------------------------------------


def test_explicit_api_key_argument_overrides_env(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    client = client_for(monkeypatch, api_key=TEST_KEY)
    assert client.get("/protected").status_code == 401
    assert (
        client.get("/protected", headers={API_KEY_HEADER: TEST_KEY}).status_code
        == 200
    )


def test_build_api_key_headers_with_env(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, TEST_KEY)
    assert build_api_key_headers() == {API_KEY_HEADER: TEST_KEY}


def test_build_api_key_headers_without_env(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    assert build_api_key_headers() == {}


def test_build_api_key_headers_explicit_arg():
    assert build_api_key_headers("k") == {API_KEY_HEADER: "k"}
    assert build_api_key_headers("") == {}
