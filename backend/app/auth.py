"""API key authentication middleware for the WAMSS backend.

How it works
------------
When the ``WAMSS_API_KEY`` environment variable is set, every incoming
request must carry that key in the ``X-API-Key`` header. Requests with a
missing or incorrect key are rejected with HTTP 401 and a JSON error body.

When ``WAMSS_API_KEY`` is unset (or empty), authentication stays disabled
and the app logs a startup warning. This keeps local development and the
test suite frictionless.

Design notes
------------
* Implemented as a Starlette ``BaseHTTPMiddleware`` so it covers every
  route, including routers mounted by other agents, without touching
  ``main.py`` beyond a two-line registration.
* Key comparison uses ``hmac.compare_digest`` (constant time) so an
  attacker cannot learn the key byte-by-byte from timing differences.
* Health checks and API docs are exempt: ``/health``, ``/health/db``,
  ``/docs``, ``/openapi.json``, ``/redoc``.
* Mounting order matters: register this middleware AFTER any error-handling
  middleware so auth rejections go through the same logging/error pipeline.
"""

import hmac
import logging
import os

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("wamss.auth")

API_KEY_HEADER = "X-API-Key"
API_KEY_ENV_VAR = "WAMSS_API_KEY"

# Paths that never require an API key. Health checks stay open so load
# balancers and uptime probes keep working; docs stay open so the API
# remains discoverable in staging/dev environments.
EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/health/db",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
)


def _is_exempt(path: str) -> bool:
    """Return True if the request path skips API key checks.

    Covers the exact exempt paths plus trailing-slash/sub-path variants of
    the docs endpoints (e.g. ``/docs/``).
    """
    if path in EXEMPT_PATHS:
        return True
    return path.startswith("/docs/") or path.startswith("/redoc/")


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Require the shared WAMSS API key on the ``X-API-Key`` header.

    The expected key is read once from the ``WAMSS_API_KEY`` environment
    variable when the middleware is constructed. An explicit ``api_key``
    argument overrides the environment (used by the test suite).
    """

    def __init__(self, app, api_key: str | None = None) -> None:
        super().__init__(app)
        self.expected_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR)
        self.enabled = bool(self.expected_key)
        if self.enabled:
            logger.info(
                "API key auth ENABLED for all paths except: %s",
                ", ".join(sorted(EXEMPT_PATHS)),
            )
        else:
            logger.warning(
                "WAMSS_API_KEY is not set; API key auth is DISABLED. "
                "Set %s in the deployment environment to protect the API.",
                API_KEY_ENV_VAR,
            )

    async def dispatch(self, request, call_next):
        if not self.enabled or _is_exempt(request.url.path):
            return await call_next(request)

        provided = request.headers.get(API_KEY_HEADER)
        if not provided or not hmac.compare_digest(provided, self.expected_key):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "detail": (
                        "Missing or invalid API key. "
                        f"Send the deployment key in the '{API_KEY_HEADER}' header."
                    )
                },
            )
        return await call_next(request)


def build_api_key_headers(api_key: str | None = None) -> dict:
    """Return ``{"X-API-Key": key}`` for outgoing requests, or ``{}``.

    Clients use this so they send the header only when a key is configured.
    ``api_key`` defaults to the ``WAMSS_API_KEY`` environment variable.
    """
    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR)
    return {API_KEY_HEADER: key} if key else {}
