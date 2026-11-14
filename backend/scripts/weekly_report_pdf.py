"""Generate a personal attendance PDF using the authenticated employee scope.

Required environment variables:
    WAMSS_EMPLOYEE_TOKEN  Employee-owned token for the attendance API.
Optional:
    WAMSS_API_URL         API base URL (default http://localhost:8000)
    WAMSS_API_KEY         Deployment API key when the backend requires it.

The script never accepts an employee ID and never calls team-wide endpoints.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_URL = os.environ.get("WAMSS_API_URL", "http://localhost:8000")
DEFAULT_DAYS = 7


def _headers() -> dict[str, str]:
    token = os.environ.get("WAMSS_EMPLOYEE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("WAMSS_EMPLOYEE_TOKEN is required for a personal report")
    result = {"X-Employee-Token": token}
    api_key = os.environ.get("WAMSS_API_KEY", "").strip()
    if api_key:
        result["X-API-Key"] = api_key
    return result


def _get(path: str, params: dict, headers: dict[str, str]):
    url = API_URL.rstrip("/") + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"Personal attendance endpoint unavailable: {type(exc).__name__}") from exc


def _collect(days: int, headers: dict[str, str]) -> dict:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    daily = []
    employee_id = None
    enabled = False
    for offset in range(days):
        target = start + timedelta(days=offset)
        payload = _get("/attendance/daily", {"day": target.isoformat()}, headers)
        if not isinstance(payload, dict):
            raise RuntimeError("Attendance API returned an invalid response")
        enabled = payload.get("enabled") is True
        items = payload.get("items", [])
        if not isinstance(items, list) or len(items) > 1:
            raise RuntimeError("Refusing a response that is not limited to one employee")
        if items:
            row = items[0]
            if not isinstance(row, dict) or not row.get("employee_id"):
                raise RuntimeError("Attendance API returned an invalid personal record")
            current_id = str(row["employee_id"])
            if employee_id is not None and current_id != employee_id:
                raise RuntimeError("Employee identity changed within the personal report")
            employee_id = current_id
            daily.append({"day": target, **row})
        else:
            daily.append({"day": target, "present_minutes": 0, "camera_minutes": 0, "computer_minutes": 0, "sources": []})
    return {"start": start, "end": today, "employee_id": employee_id, "enabled": enabled, "daily": daily}


def render_pdf(report: dict, out_path: str) -> None:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Personal Attendance Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, f"Reporting window: {report['start'].isoformat()} to {report['end'].isoformat()}")
    if report["employee_id"]:
        pdf.multi_cell(0, 6, f"Employee: {report['employee_id']}")
    pdf.ln(3)
    if not report["enabled"]:
        pdf.multi_cell(0, 6, "Attendance is disabled or unavailable. No attendance details were included.")
    else:
        pdf.multi_cell(0, 6, "This report is limited to the authenticated employee token. Attendance values are estimates from eligible, consented sources; overlapping intervals are counted once.")
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 9)
        widths = (35, 40, 40, 40, 35)
        for width, heading in zip(widths, ("UTC day", "Present min", "Camera min", "Computer min", "Sources")):
            pdf.cell(width, 8, heading, border=1)
        pdf.ln()
        pdf.set_font("Helvetica", "", 9)
        for row in report["daily"]:
            values = (
                row["day"].isoformat(),
                f"{float(row.get('present_minutes') or 0):.1f}",
                f"{float(row.get('camera_minutes') or 0):.1f}",
                f"{float(row.get('computer_minutes') or 0):.1f}",
                ", ".join(row.get("sources") or []),
            )
            for width, value in zip(widths, values):
                pdf.cell(width, 8, value[:24], border=1)
            pdf.ln()
    pdf.output(out_path)


def main() -> int:
    global API_URL
    parser = argparse.ArgumentParser(description="Generate a personal attendance PDF from the employee-scoped API.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Reporting window in days (default 7)")
    parser.add_argument("--out", default="personal-attendance.pdf", help="Output PDF path")
    parser.add_argument("--api", default=API_URL, help="Base URL of the WAMSS API")
    args = parser.parse_args()
    API_URL = args.api.rstrip("/")
    try:
        headers = _headers()
        report = _collect(max(1, min(args.days, 31)), headers)
        render_pdf(report, args.out)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"wrote personal attendance report to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
