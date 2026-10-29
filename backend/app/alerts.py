"""
WAMSS real-time alert dispatch (Agent 3).

`dispatch_alert(kind, payload)` fans an alert out to zero or more channels:

  * SMTP email    (env: WAMSS_ALERT_EMAIL_ENABLED=1, WAMSS_ALERT_SMTP_*, WAMSS_ALERT_EMAIL_TO)
  * Telegram bot  (env: WAMSS_TELEGRAM_ENABLED=1, WAMSS_TELEGRAM_BOT_TOKEN, WAMSS_TELEGRAM_CHAT_ID)
  * Generic webhook POST (env: WAMSS_WEBHOOK_ENABLED=1, WAMSS_WEBHOOK_URL)
    (covers WhatsApp/Slack/PagerDuty style integrations via a provider webhook)

All channels are optional and OFF unless explicitly enabled. Environment is
read fresh on every dispatch so tests and hot-reconfig work without restarts.

Channel failures are logged and swallowed: alerting must never break the
request that fired the alert. No alert payload ever includes raw keystrokes,
file content, or other PII beyond employee/machine identifiers already stored.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

logger = logging.getLogger("wamss.alerts")

# ---------------------------------------------------------------------------
# Config helpers (env read fresh on every call)
# ---------------------------------------------------------------------------


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _enabled(name: str) -> bool:
    return _env(name).lower() in ("1", "true", "yes", "on")


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

ALERT_TITLES = {
    "HIGH_STRAIN_DETECTED": "High workstation strain detected",
    "usb_insert": "USB device inserted",
    "usb_remove": "USB device removed",
    "file_write_burst": "Large file write burst",
    "after_hours_activity": "After-hours activity",
}


def format_alert_text(kind: str, payload: dict) -> str:
    """Human-readable one-screen summary of the alert."""
    title = ALERT_TITLES.get(kind, kind.replace("_", " ").title())
    lines = [f"[WAMSS] {title}"]
    for key in ("employee_id", "machine_id", "severity", "reason", "detail",
                "strain_score", "peak_score", "streak", "timestamp"):
        value = payload.get(key)
        if value in (None, "", {}):
            continue
        if isinstance(value, dict):
            value = json.dumps(value, default=str)
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


def _send_email(kind: str, payload: dict, text: str) -> None:
    """SMTP email channel. Raises on failure; caller catches."""
    host = _env("WAMSS_ALERT_SMTP_HOST")
    if not host:
        raise RuntimeError("WAMSS_ALERT_SMTP_HOST is not set")
    port = int(_env("WAMSS_ALERT_SMTP_PORT", "587") or "587")
    user = _env("WAMSS_ALERT_SMTP_USER")
    password = _env("WAMSS_ALERT_SMTP_PASS")
    sender = _env("WAMSS_ALERT_SMTP_FROM") or user
    recipients = [r.strip() for r in _env("WAMSS_ALERT_EMAIL_TO").split(",") if r.strip()]
    if not sender or not recipients:
        raise RuntimeError("WAMSS_ALERT_SMTP_FROM and WAMSS_ALERT_EMAIL_TO are required")

    msg = EmailMessage()
    msg["Subject"] = f"[WAMSS] {kind}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(text)

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)


def _post_json(url: str, data: dict, timeout: int = 15) -> None:
    body = json.dumps(data, default=str).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "wamss-alerts/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status} from {url}")


def _send_telegram(kind: str, payload: dict, text: str) -> None:
    """Telegram bot channel. Raises on failure; caller catches."""
    token = _env("WAMSS_TELEGRAM_BOT_TOKEN")
    chat_id = _env("WAMSS_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("WAMSS_TELEGRAM_BOT_TOKEN and WAMSS_TELEGRAM_CHAT_ID are required")
    _post_json(f"https://api.telegram.org/bot{token}/sendMessage",
               {"chat_id": chat_id, "text": text})


def _send_webhook(kind: str, payload: dict, text: str) -> None:
    """Generic JSON webhook channel. Raises on failure; caller catches."""
    url = _env("WAMSS_WEBHOOK_URL")
    if not url:
        raise RuntimeError("WAMSS_WEBHOOK_URL is not set")
    _post_json(url, {
        "kind": kind,
        "payload": payload,
        "text": text,
        "timestamp": _timestamp_iso(),
        "source": "wamss",
    })


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_CHANNELS = (
    ("WAMSS_ALERT_EMAIL_ENABLED", "email", _send_email),
    ("WAMSS_TELEGRAM_ENABLED", "telegram", _send_telegram),
    ("WAMSS_WEBHOOK_ENABLED", "webhook", _send_webhook),
)


def dispatch_alert(kind: str, payload: dict | None = None) -> None:
    """
    Send `kind` alert with `payload` to every enabled channel.

    Never raises: a misconfigured or failing channel is logged and skipped,
    so the request that triggered the alert always completes.
    """
    payload = dict(payload or {})
    if "timestamp" not in payload:
        payload["timestamp"] = _timestamp_iso()
    text = format_alert_text(kind, payload)

    for flag, name, sender in _CHANNELS:
        if not _enabled(flag):
            continue
        try:
            sender(kind, payload, text)
            logger.info("alert %s sent via %s", kind, name)
        except Exception as exc:  # noqa: BLE001 - alerting must never be fatal
            logger.error("alert %s via %s failed: %s %s", kind, name,
                         type(exc).__name__, exc)
