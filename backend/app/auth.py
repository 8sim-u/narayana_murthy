"""API key authentication middleware for the WAMSS backend.

How it works
------------
When the ``WAMSS_API_KEY`` environment variable is set, requests generally
must carry that key in the ``X-API-Key`` header. The narrow exceptions are
the documented health/docs routes, ``/wellness/live`` with its own employee
token and consent checks, and ``POST /activity`` when an employee token is
present; that activity handler independently validates and scopes the token.
Requests outside those exceptions with a missing or incorrect key are rejected
with HTTP 401 and a JSON error body.

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
  ``/docs``, ``/openapi.json``, ``/redoc``. ``POST /activity`` may omit the
  deployment key only when it supplies an employee token; the handler still
  resolves that token and binds every record to its employee.
* ``request.state.api_key_authenticated`` is set whenever a request passes
  the deployment-key gate: a valid key was presented, or no key is
  configured at all (the gate is open in keyless dev and test setups, so
  endpoints that require a validated key must not fail closed there).
  Exempt paths bypass the gate and are not marked.
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
_PLACEHOLDER_API_KEYS = frozenset({"changeme", "example", "placeholder", "your-key", "your-long-random-key", "replace-me", "todo"})

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
        # Direct employee upload is separately protected by X-Employee-Token
        # and server-side wellness consent; never distribute the deployment key
        # to an employee wearable client for this endpoint.
        "/wellness/live",
    }
)
EMPLOYEE_ACTIVITY_UPLOAD_PATH = "/activity"


def _is_exempt(
    path: str,
    method: str = "GET",
    has_employee_token: bool = False,
) -> bool:
    """Return True if the request path skips API key checks.

    Covers exact exempt paths, trailing-slash/sub-path variants of the docs
    endpoints, and only employee-token-bearing POSTs to ``/activity``.
    """
    if path in EXEMPT_PATHS:
        return True
    if (
        method.upper() == "POST"
        and path == EMPLOYEE_ACTIVITY_UPLOAD_PATH
        and has_employee_token
    ):
        return True
    # Do not exempt GET /activity or any other method/path combination.
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
        normalized_key = (self.expected_key or "").strip().lower()
        if normalized_key and (
            normalized_key in _PLACEHOLDER_API_KEYS
            or normalized_key.startswith("<")
            or normalized_key.endswith(">")
        ):
            raise ValueError(f"{API_KEY_ENV_VAR} must not be a placeholder value")
        self.enabled = bool(self.expected_key)
        if self.enabled:
            logger.info(
                "API key auth ENABLED for all paths except: %s; POST /activity "
                "also skips the deployment key only when X-Employee-Token is present",
                ", ".join(sorted(EXEMPT_PATHS)),
            )
        else:
            logger.warning(
                "WAMSS_API_KEY is not set; API key auth is DISABLED. "
                "Set %s in the deployment environment to protect the API.",
                API_KEY_ENV_VAR,
            )

    async def dispatch(self, request, call_next):
        has_employee_token = bool(request.headers.get("X-Employee-Token"))
        if not self.enabled:
            # No deployment key is configured, so the deployment-key gate is
            # open. Mark the request as gate-passing so endpoints that
            # require a validated key (face enroll, gallery, geofences) work
            # in keyless dev and test setups instead of failing closed.
            request.state.api_key_authenticated = True
            return await call_next(request)
        if _is_exempt(request.url.path, request.method, has_employee_token):
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
        request.state.api_key_authenticated = True
        return await call_next(request)


def build_api_key_headers(api_key: str | None = None) -> dict:
    """Return ``{"X-API-Key": key}`` for outgoing requests, or ``{}``.

    Clients use this so they send the header only when a key is configured.
    ``api_key`` defaults to the ``WAMSS_API_KEY`` environment variable.
    """
    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR)
    return {API_KEY_HEADER: key} if key else {}
