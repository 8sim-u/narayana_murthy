"""Unit tests for backend/app/alerts.py (Agent 3).

All channels are exercised with mocked transports: smtplib for email and
urllib.request for Telegram/webhook. No real network traffic is performed.
"""

from __future__ import annotations

import json
import smtplib
import urllib.request

import pytest

from app import alerts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALERT_FLAGS = (
    "WAMSS_ALERT_EMAIL_ENABLED",
    "WAMSS_TELEGRAM_ENABLED",
    "WAMSS_WEBHOOK_ENABLED",
)


@pytest.fixture
def env_disabled(monkeypatch):
    """All alert channels disabled."""
    for flag in ALERT_FLAGS:
        monkeypatch.setenv(flag, "0")
    return monkeypatch


@pytest.fixture
def env_email(monkeypatch, env_disabled):
    monkeypatch.setenv("WAMSS_ALERT_EMAIL_ENABLED", "1")
    monkeypatch.setenv("WAMSS_ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("WAMSS_ALERT_SMTP_PORT", "587")
    monkeypatch.setenv("WAMSS_ALERT_SMTP_USER", "alerts@example.com")
    monkeypatch.setenv("WAMSS_ALERT_SMTP_PASS", "secret")
    monkeypatch.setenv("WAMSS_ALERT_SMTP_FROM", "alerts@example.com")
    monkeypatch.setenv("WAMSS_ALERT_EMAIL_TO", "soc@example.com, ciso@example.com")
    return monkeypatch


@pytest.fixture
def env_telegram(monkeypatch, env_disabled):
    monkeypatch.setenv("WAMSS_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("WAMSS_TELEGRAM_BOT_TOKEN", "TOKEN123")
    monkeypatch.setenv("WAMSS_TELEGRAM_CHAT_ID", "98765")
    return monkeypatch


@pytest.fixture
def env_webhook(monkeypatch, env_disabled):
    monkeypatch.setenv("WAMSS_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("WAMSS_WEBHOOK_URL", "https://hooks.example.com/wamss")
    return monkeypatch


class _FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


class _FakeHTTPResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen_factory(calls, status_code=200):
    def _fake(req, timeout=None):
        calls.append({"url": req.full_url,
                      "data": json.loads(req.data.decode("utf-8")) if req.data else None,
                      "status_code": status_code})
        return _FakeHTTPResponse()

    return _fake


# ---------------------------------------------------------------------------
# Email channel
# ---------------------------------------------------------------------------


def test_email_channel_sends_when_enabled(env_email, monkeypatch):
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    alerts.dispatch_alert("usb_insert", {"machine_id": "WS-1", "employee_id": "EMP-42"})

    assert len(_FakeSMTP.instances) == 1
    smtp = _FakeSMTP.instances[0]
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert len(smtp.sent) == 1
    msg = smtp.sent[0]
    assert msg["Subject"] == "[WAMSS] usb_insert"
    assert msg["From"] == "alerts@example.com"
    assert msg["To"] == "soc@example.com, ciso@example.com"
    body = msg.get_content()
    assert "WS-1" in body and "EMP-42" in body


def test_email_channel_disabled_sends_nothing(env_disabled, monkeypatch):
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    alerts.dispatch_alert("usb_insert", {"machine_id": "WS-1"})
    assert _FakeSMTP.instances == []


def test_email_failure_does_not_raise(env_email, monkeypatch):
    def _boom(*args, **kwargs):
        raise ConnectionError("smtp down")

    monkeypatch.setattr(smtplib, "SMTP", _boom)
    alerts.dispatch_alert("usb_insert", {"machine_id": "WS-1"})  # must not raise


# ---------------------------------------------------------------------------
# Telegram channel
# ---------------------------------------------------------------------------


def test_telegram_channel_sends_when_enabled(env_telegram, monkeypatch):
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(calls))
    alerts.dispatch_alert("HIGH_STRAIN_DETECTED",
                          {"machine_id": "WS-2", "employee_id": "EMP-7", "strain_score": 92})

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://api.telegram.org/botTOKEN123/sendMessage"
    assert call["data"]["chat_id"] == "98765"
    assert "WS-2" in call["data"]["text"]
    assert "EMP-7" in call["data"]["text"]


def test_telegram_channel_disabled_sends_nothing(env_disabled, monkeypatch):
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(calls))
    alerts.dispatch_alert("usb_remove", {"machine_id": "WS-1"})
    assert calls == []


def test_telegram_failure_does_not_raise(env_telegram, monkeypatch):
    def _boom(req, timeout=None):
        raise TimeoutError("telegram unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    alerts.dispatch_alert("usb_remove", {"machine_id": "WS-1"})  # must not raise


# ---------------------------------------------------------------------------
# Webhook channel
# ---------------------------------------------------------------------------


def test_webhook_channel_sends_when_enabled(env_webhook, monkeypatch):
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(calls))
    payload = {"machine_id": "WS-3", "employee_id": "EMP-9",
               "severity": "medium", "reason": "after hours"}
    alerts.dispatch_alert("after_hours_activity", payload)

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://hooks.example.com/wamss"
    body = call["data"]
    assert body["kind"] == "after_hours_activity"
    assert body["payload"]["machine_id"] == "WS-3"
    assert body["payload"]["reason"] == "after hours"
    assert body["source"] == "wamss"
    assert "timestamp" in body


def test_webhook_channel_disabled_sends_nothing(env_disabled, monkeypatch):
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(calls))
    alerts.dispatch_alert("after_hours_activity", {"machine_id": "WS-1"})
    assert calls == []


def test_webhook_failure_does_not_raise(env_webhook, monkeypatch):
    def _boom(req, timeout=None):
        raise ConnectionError("webhook host down")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    alerts.dispatch_alert("file_write_burst", {"machine_id": "WS-1"})  # must not raise


# ---------------------------------------------------------------------------
# Cross-cutting behaviour
# ---------------------------------------------------------------------------


def test_all_channels_fire_when_all_enabled(env_email, monkeypatch):
    monkeypatch.setenv("WAMSS_TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("WAMSS_TELEGRAM_BOT_TOKEN", "TOKEN123")
    monkeypatch.setenv("WAMSS_TELEGRAM_CHAT_ID", "98765")
    monkeypatch.setenv("WAMSS_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("WAMSS_WEBHOOK_URL", "https://hooks.example.com/wamss")

    _FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(calls))

    alerts.dispatch_alert("usb_insert", {"machine_id": "WS-1", "employee_id": "EMP-1"})

    assert len(_FakeSMTP.instances) == 1
    assert len(calls) == 2  # telegram + webhook


def test_dispatch_with_empty_payload_gets_timestamp(env_webhook, monkeypatch):
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(calls))
    alerts.dispatch_alert("usb_insert")
    body = calls[0]["data"]
    assert "timestamp" in body["payload"]
    assert body["payload"]["timestamp"]  # non-empty ISO timestamp


def test_format_alert_text_is_human_readable():
    text = alerts.format_alert_text(
        "HIGH_STRAIN_DETECTED",
        {"machine_id": "WS-1", "employee_id": "EMP-1", "strain_score": 88},
    )
    assert text.startswith("[WAMSS] High workstation strain detected")
    assert "WS-1" in text and "EMP-1" in text and "88" in text
