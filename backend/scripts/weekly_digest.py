#!/usr/bin/env python3
"""WAMSS weekly digest generator.

Builds a Monday team summary from workstation telemetry rows and policy /
presence data. The core logic is the pure function ``build_digest``: it takes
lists of row dicts and returns a plain-text and a simple HTML summary.

Input row shapes (dicts; keys beyond those used below are ignored):

* activity_rows:  {"employee_id": str, "is_idle": bool,
                   "keystrokes": int, "mouse_clicks": int, "strain_score": int|None}
* focus_rows:     {"employee_id": str, "focus_score": float|None,
                   "break_compliant": bool, "streak_days": int}
* strain_rows:    {"employee_id": str, "strain_level": str,
                   "strain_score": int|None}
* policy_flags:   {"employee_id": str, "flag": str, "detail": str}
* presence_rows:  {"employee_id": str, "status": str, "anomaly": str|None}

Delivery is via the CLI: ``--dry-run`` (default) prints to stdout;
``--send`` actually sends via SMTP. Only the Python standard library is used
(smtplib, email).

SMTP settings come from CLI flags or environment variables
WAMSS_SMTP_HOST, WAMSS_SMTP_PORT, WAMSS_SMTP_USER, WAMSS_SMTP_PASS,
WAMSS_SMTP_FROM, WAMSS_SMTP_TO. Values shown in help are placeholders only.
"""

from __future__ import annotations

import argparse
import os
import smtplib
from email.message import EmailMessage
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------- pure digest builder


def _strain_rank(level: str) -> int:
    return {"red": 3, "amber": 2, "green": 1}.get(str(level).lower(), 0)


def _strain_color(level: str) -> str:
    return {"red": "#c0392b", "amber": "#d68910", "green": "#1e8449"}.get(
        str(level).lower(), "#6c757d"
    )


def build_digest(
    activity_rows: List[Dict],
    focus_rows: List[Dict],
    strain_rows: List[Dict],
    policy_flags: List[Dict],
    presence_rows: List[Dict],
) -> Tuple[str, str]:
    """Build the weekly digest.

    Returns (plain_text, html) summaries. All stats are defensive: empty input
    lists yield "no data" sections rather than errors.
    """
    employees: set = set()
    for row in activity_rows + focus_rows + strain_rows + presence_rows:
        eid = row.get("employee_id")
        if eid:
            employees.add(str(eid))

    # ---- focus stats
    focus_scores: List[float] = [
        float(r["focus_score"])
        for r in focus_rows
        if r.get("focus_score") is not None
    ]
    avg_focus: Optional[float] = (
        sum(focus_scores) / len(focus_scores) if focus_scores else None
    )

    # ---- break compliance
    focus_ids = {str(r.get("employee_id")) for r in focus_rows if r.get("employee_id")}
    compliant = sum(1 for r in focus_rows if r.get("break_compliant"))
    break_pct: Optional[float] = (
        100.0 * compliant / len(focus_ids) if focus_ids else None
    )

    # ---- strain distribution
    strain_counts = {"green": 0, "amber": 0, "red": 0, "unknown": 0}
    strain_by_employee: Dict[str, Tuple[str, Optional[int]]] = {}
    for r in strain_rows:
        eid = str(r.get("employee_id", "?"))
        level = str(r.get("strain_level", "unknown")).lower()
        score = r.get("strain_score")
        if level not in ("green", "amber", "red"):
            level = "unknown"
        strain_counts[level] += 1
        key_rank = _strain_rank(strain_by_employee.get(eid, ("unknown", None))[0])
        if _strain_rank(level) > key_rank:
            strain_by_employee[eid] = (level, score)

    # top strain cases: red first, then amber, highest score first
    top_strain = sorted(
        (
            (eid, level, score)
            for eid, (level, score) in strain_by_employee.items()
            if level in ("red", "amber")
        ),
        key=lambda t: (_strain_rank(t[1]), t[2] if t[2] is not None else -1),
        reverse=True,
    )[:5]

    # ---- idle activity ratio
    idle_count = sum(1 for r in activity_rows if r.get("is_idle"))
    total_activity = len(activity_rows)
    idle_pct: Optional[float] = (
        100.0 * idle_count / total_activity if total_activity else None
    )

    # ---- presence anomalies
    anomalies = [r for r in presence_rows if r.get("anomaly")]
    present_count = sum(
        1 for r in presence_rows if str(r.get("status", "")).lower() == "present"
    )

    # ---- policy flags grouped per employee
    flags_by_employee: Dict[str, List[Dict]] = {}
    for f in policy_flags:
        flags_by_employee.setdefault(str(f.get("employee_id", "?")), []).append(f)

    def fmt(x: Optional[float]) -> str:
        return f"{x:.1f}" if x is not None else "no data"

    # =============== plain text ===============
    lines = [
        "WAMSS Weekly Team Digest",
        "========================",
        "",
        f"Employees covered: {len(employees)}",
        f"Average focus score: {fmt(avg_focus)}",
        f"Break compliance: {fmt(break_pct)}%",
        f"Idle share of activity rows: {fmt(idle_pct)}%",
        f"Presence: {present_count} present of {len(presence_rows)} reported",
        "Strain distribution: "
        + ", ".join(f"{k}: {strain_counts[k]}" for k in ("green", "amber", "red", "unknown")),
        "",
        "Top strain cases:",
    ]
    if top_strain:
        for eid, level, score in top_strain:
            score_txt = f" (score {score})" if score is not None else ""
            lines.append(f"  - {eid}: {level.upper()}{score_txt}")
    else:
        lines.append("  none reported")

    lines += ["", "Policy flags:"]
    if flags_by_employee:
        for eid, flags in sorted(flags_by_employee.items()):
            for f in flags:
                lines.append(f"  - {eid}: {f.get('flag', '?')} - {f.get('detail', '')}")
    else:
        lines.append("  none raised")

    lines += ["", "Presence anomalies:"]
    if anomalies:
        for r in anomalies:
            lines.append(f"  - {r.get('employee_id', '?')}: {r.get('anomaly')}")
    else:
        lines.append("  none detected")

    plain = "\n".join(lines) + "\n"

    # =============== HTML ===============
    def badge(level: str, text: str) -> str:
        color = _strain_color(level)
        return (
            f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
            f'background-color:{color};color:#fff;font-size:0.85em;">{text}</span>'
        )

    rows_html = "".join(
        f"<tr><td>{eid}</td><td>{badge(level, level.upper())}</td>"
        f"<td>{score if score is not None else 'n/a'}</td></tr>"
        for eid, level, score in top_strain
    )
    if not rows_html:
        rows_html = '<tr><td colspan="3">none reported</td></tr>'

    flags_html = "".join(
        f"<li><strong>{eid}</strong>: {f.get('flag', '?')} - {f.get('detail', '')}</li>"
        for eid, flags in sorted(flags_by_employee.items())
        for f in flags
    ) or "<li>none raised</li>"

    anomalies_html = "".join(
        f"<li><strong>{r.get('employee_id', '?')}</strong>: {r.get('anomaly')}</li>"
        for r in anomalies
    ) or "<li>none detected</li>"

    html = (
        "<html><body style=\"font-family:sans-serif;max-width:700px;margin:auto;\">\n"
        "<h1>WAMSS Weekly Team Digest</h1>\n"
        "<h2>Team stats</h2>\n<ul>\n"
        f"<li>Employees covered: {len(employees)}</li>\n"
        f"<li>Average focus score: {fmt(avg_focus)}</li>\n"
        f"<li>Break compliance: {fmt(break_pct)}%</li>\n"
        f"<li>Idle share of activity rows: {fmt(idle_pct)}%</li>\n"
        f"<li>Presence: {present_count} present of {len(presence_rows)} reported</li>\n"
        "<li>Strain distribution: "
        + ", ".join(f"{k}: {strain_counts[k]}" for k in ("green", "amber", "red", "unknown"))
        + "</li>\n</ul>\n"
        "<h2>Top strain cases</h2>\n"
        '<table border="1" cellpadding="6" cellspacing="0" '
        'style="border-collapse:collapse;">\n'
        "<tr><th>Employee</th><th>Level</th><th>Score</th></tr>\n"
        f"{rows_html}\n</table>\n"
        "<h2>Policy flags</h2>\n<ul>\n" + flags_html + "\n</ul>\n"
        "<h2>Presence anomalies</h2>\n<ul>\n" + anomalies_html + "\n</ul>\n"
        "</body></html>"
    )
    return plain, html


# ---------------------------------------------------------------- demo data


def demo_data() -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict], List[Dict]]:
    """Synthetic rows for the ``--demo`` dry-run (no backend needed)."""
    activity_rows = [
        {"employee_id": "E001", "is_idle": False, "keystrokes": 420, "mouse_clicks": 130, "strain_score": 55},
        {"employee_id": "E001", "is_idle": True, "keystrokes": 0, "mouse_clicks": 0, "strain_score": None},
        {"employee_id": "E002", "is_idle": False, "keystrokes": 610, "mouse_clicks": 90, "strain_score": 82},
        {"employee_id": "E003", "is_idle": False, "keystrokes": 300, "mouse_clicks": 200, "strain_score": 25},
        {"employee_id": "E004", "is_idle": True, "keystrokes": 5, "mouse_clicks": 2, "strain_score": 45},
    ]
    focus_rows = [
        {"employee_id": "E001", "focus_score": 72.5, "break_compliant": True, "streak_days": 4},
        {"employee_id": "E002", "focus_score": 58.0, "break_compliant": False, "streak_days": 0},
        {"employee_id": "E003", "focus_score": 91.0, "break_compliant": True, "streak_days": 12},
        {"employee_id": "E004", "focus_score": None, "break_compliant": False, "streak_days": 1},
    ]
    strain_rows = [
        {"employee_id": "E001", "strain_level": "amber", "strain_score": 55},
        {"employee_id": "E002", "strain_level": "red", "strain_score": 82},
        {"employee_id": "E003", "strain_level": "green", "strain_score": 25},
        {"employee_id": "E004", "strain_level": "amber", "strain_score": 45},
    ]
    policy_flags = [
        {"employee_id": "E002", "flag": "EXCESSIVE_OVERTIME", "detail": "logged 62h this week"},
        {"employee_id": "E004", "flag": "UNAUTHORIZED_APP", "detail": "game client detected 3 times"},
    ]
    presence_rows = [
        {"employee_id": "E001", "status": "present", "anomaly": None},
        {"employee_id": "E002", "status": "present", "anomaly": "no badge-out after 22:00"},
        {"employee_id": "E003", "status": "absent", "anomaly": None},
        {"employee_id": "E004", "status": "present", "anomaly": None},
    ]
    return activity_rows, focus_rows, strain_rows, policy_flags, presence_rows


# ---------------------------------------------------------------- SMTP send


def send_digest(
    plain: str,
    html: str,
    host: str,
    port: int,
    user: str,
    password: str,
    mail_from: str,
    mail_to: List[str],
    use_tls: bool = True,
) -> None:
    """Send the digest as a multipart (plain + HTML) email via SMTP."""
    msg = EmailMessage()
    msg["Subject"] = "WAMSS Weekly Team Digest"
    msg["From"] = mail_from
    msg["To"] = ", ".join(mail_to)
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(host, port, timeout=30) as server:
        if use_tls:
            server.starttls()
        if user:
            server.login(user, password)
        server.send_message(msg)


# ---------------------------------------------------------------- CLI


def _resolve_env(flag_value: Optional[str], env_name: str, default: str = "") -> str:
    return flag_value if flag_value is not None else os.environ.get(env_name, default)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="WAMSS weekly digest generator (dry-run prints, --send emails)."
    )
    p.add_argument("--dry-run", action="store_true", default=True,
                   help="Print the digest to stdout (default).")
    p.add_argument("--send", action="store_true",
                   help="Actually send the digest via SMTP.")
    p.add_argument("--demo", action="store_true",
                   help="Build the digest from synthetic demo data.")
    p.add_argument("--smtp-host", default=None,
                   help="SMTP host (or WAMSS_SMTP_HOST). Example: smtp.example.com")
    p.add_argument("--smtp-port", type=int, default=None,
                   help="SMTP port (or WAMSS_SMTP_PORT). Example: 587")
    p.add_argument("--smtp-user", default=None,
                   help="SMTP username (or WAMSS_SMTP_USER). Example: wamss@example.com")
    p.add_argument("--smtp-pass", default=None,
                   help="SMTP password (or WAMSS_SMTP_PASS). Example: YOUR-APP-PASSWORD")
    p.add_argument("--smtp-from", default=None,
                   help="From address (or WAMSS_SMTP_FROM). Example: wamss@example.com")
    p.add_argument("--smtp-to", default=None,
                   help="Comma-separated recipients (or WAMSS_SMTP_TO). Example: team@example.com")
    p.add_argument("--no-tls", action="store_true",
                   help="Do not use STARTTLS.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if args.demo:
        data = demo_data()
    else:
        # No live backend fetch is wired here; callers can extend this branch.
        data = ([], [], [], [], [])

    plain, html = build_digest(*data)

    if args.send:
        host = _resolve_env(args.smtp_host, "WAMSS_SMTP_HOST", "smtp.example.com")
        port = int(_resolve_env(args.smtp_port, "WAMSS_SMTP_PORT", "587"))
        user = _resolve_env(args.smtp_user, "WAMSS_SMTP_USER", "")
        password = _resolve_env(args.smtp_pass, "WAMSS_SMTP_PASS", "")
        mail_from = _resolve_env(args.smtp_from, "WAMSS_SMTP_FROM", "wamss@example.com")
        mail_to = [
            a.strip()
            for a in _resolve_env(args.smtp_to, "WAMSS_SMTP_TO", "team@example.com").split(",")
            if a.strip()
        ]
        try:
            send_digest(plain, html, host, port, user, password, mail_from, mail_to,
                        use_tls=not args.no_tls)
        except (smtplib.SMTPException, OSError) as exc:
            print(f"SMTP send failed: {exc}")
            return 1
        print(f"Digest sent to {', '.join(mail_to)} via {host}:{port}")
        return 0

    # default: dry-run to stdout
    print(plain)
    return 0


if __name__ == "__main__":
    # Demo: build a digest from synthetic data in dry-run mode.
    raise SystemExit(main(["--demo"]))
