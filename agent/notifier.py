"""
Pluggable break-reminder notifier for the WAMSS workstation agent.

The default path shows a native desktop toast (Windows: system toast via
plyer; other platforms: plyer where supported) and always falls back to a
plain log line so the reminder is never silently lost on headless machines.

The notifier is deliberately swappable: tests and dry runs can replace
``notify_break`` (or pass ``notifier=``) with a stub that records calls.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger("wamss.agent.notifier")


def _native_toast(title: str, message: str) -> bool:
    """Best-effort native toast. Returns True if a toast was shown."""
    try:
        from plyer import notification  # type: ignore
    except Exception:
        return False
    try:
        notification.notify(
            title=title,
            message=message,
            app_name="WAMSS Workstation Agent",
            timeout=30,
        )
        return True
    except Exception as exc:  # never let a toast crash the agent
        logger.debug("native toast failed: %s", exc)
        return False


def notify_break(title: str, message: str, notifier: Callable[[str, str], None] | None = None) -> str:
    """
    Show a break reminder. Returns the channel used: "toast", "custom", or "log".

    ``notifier`` is a pluggable hook: any callable taking (title, message).
    """
    if notifier is not None:
        notifier(title, message)
        return "custom"
    if _native_toast(title, message):
        logger.info("break reminder shown as desktop toast")
        return "toast"
    logger.info("break reminder (log fallback): %s - %s", title, message)
    print(f"[WAMSS] {title}: {message}")
    return "log"
