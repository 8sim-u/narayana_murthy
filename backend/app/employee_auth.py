"""Employee identity binding for scoped reads and employee-attributed writes.

The deployment-wide API key authenticates a trusted client, not an employee.
Personal records require a second, employee-owned bearer token mapped on the
server to exactly one employee ID via ``WAMSS_EMPLOYEE_TOKEN_HASHES``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from typing import Dict, Optional

from fastapi import Header, HTTPException, Request, Response, status

logger = logging.getLogger("wamss.auth.employee")
EMPLOYEE_TOKEN_HEADER = "X-Employee-Token"
EMPLOYEE_TOKEN_HASHES_ENV_VAR = "WAMSS_EMPLOYEE_TOKEN_HASHES"
_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


def _unique_json_object(pairs):
    """Reject duplicate JSON keys instead of silently accepting the last one."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _employee_token_hashes() -> Dict[str, str]:
    """Load the complete employee-ID -> SHA-256 token-hash map.

    Any malformed entry invalidates the map rather than silently dropping it;
    partial identity configuration must never authorize an ambiguous user.
    """
    raw = os.environ.get(EMPLOYEE_TOKEN_HASHES_ENV_VAR, "")
    if not raw:
        return {}
    try:
        data = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (TypeError, ValueError):
        logger.error("%s must be valid JSON", EMPLOYEE_TOKEN_HASHES_ENV_VAR)
        return {}
    if not isinstance(data, dict) or not data:
        logger.error("%s must be a non-empty JSON object", EMPLOYEE_TOKEN_HASHES_ENV_VAR)
        return {}

    result: Dict[str, str] = {}
    for employee_id, digest in data.items():
        if (
            not isinstance(employee_id, str)
            or not employee_id.strip()
            or employee_id.strip() in result
            or not isinstance(digest, str)
            or not _SHA256_HEX.fullmatch(digest.strip())
        ):
            logger.error("%s contains an invalid employee token mapping", EMPLOYEE_TOKEN_HASHES_ENV_VAR)
            return {}
        result[employee_id.strip()] = digest.strip().lower()
    return result


def configured_employee_ids() -> set[str]:
    """Return employee IDs only when the complete server token map is valid."""
    return set(_employee_token_hashes())


def _resolve_employee_identity(x_employee_token: Optional[str]) -> str:
    """Resolve exactly one employee from a personal token, failing closed."""
    hashes = _employee_token_hashes()
    if not hashes:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Employee-scoped access is not configured",
        )
    if not x_employee_token or len(x_employee_token) > 512:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Employee token required",
        )

    digest = hashlib.sha256(x_employee_token.encode("utf-8")).hexdigest()
    matches = [
        employee_id
        for employee_id, expected in hashes.items()
        if hmac.compare_digest(digest, expected)
    ]
    if len(matches) != 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or ambiguous employee token",
        )
    return matches[0]


def require_employee(
    response: Response,
    x_employee_token: Optional[str] = Header(None, alias=EMPLOYEE_TOKEN_HEADER),
) -> str:
    """Resolve an employee identity; deployment keys are not employee identity."""
    if response is not None:
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Vary"] = EMPLOYEE_TOKEN_HEADER
    return _resolve_employee_identity(x_employee_token)


def require_employee_or_service(
    request: Request,
    x_employee_token: Optional[str] = Header(None, alias=EMPLOYEE_TOKEN_HEADER),
) -> Optional[str]:
    """Authenticate employee writes or explicitly trusted service ingestion.

    Employee credentials always resolve to one employee and may only write
    records attributed to that identity. A deployment-key-only service path is
    disabled by default and requires both
    ``WAMSS_EMPLOYEE_INGESTION_SERVICE_ACCESS=1`` and proof that API-key
    middleware validated this request. Supplying an invalid employee token
    never falls back to service identity.
    """
    if x_employee_token is not None:
        return _resolve_employee_identity(x_employee_token)

    service_access = os.environ.get(
        "WAMSS_EMPLOYEE_INGESTION_SERVICE_ACCESS", ""
    ).strip().lower() in {"1", "true", "yes"}
    if service_access and getattr(request.state, "api_key_authenticated", False):
        return None
    if service_access:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A validated deployment API key is required for service ingestion",
        )
    return _resolve_employee_identity(None)


def ensure_employee_match(
    requested_employee_id: Optional[str], authenticated_employee_id: str,
) -> None:
    """Reject an employee query filter that attempts to select another person."""
    if (
        requested_employee_id is not None
        and not hmac.compare_digest(requested_employee_id, authenticated_employee_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Employee token cannot access another employee's records",
        )
