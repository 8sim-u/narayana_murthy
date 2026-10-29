"""
WAMSS weekly PDF report generator (reads the live API, writes a PDF file).

Usage:
    python3 backend/scripts/weekly_report_pdf.py [--days 7] [--out weekly-report.pdf]
    WAMSS_API_URL=http://localhost:8000 python3 backend/scripts/weekly_report_pdf.py

Contents (one-page-ish, kept simple):
  1. Title + reporting window
  2. Team stats for the window (from /analytics/trends, focus + strain)
  3. Top strain cases (from /activity/strain-summary, worst first)
  4. Policy flags (from /policy/violations per employee per day)

Strain is a heuristic workload signal, not a medical measure. The report
only reflects data the live API returns; sections degrade gracefully to
"unavailable" when an endpoint is missing (e.g. analytics router not mounted).
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

API_URL = os.environ.get("WAMSS_API_URL", "http://localhost:8000")
DEFAULT_DAYS = 7


# ---------------------------------------------------------------- HTTP helpers

def _get(path: str, params: dict | None = None):
    """GET JSON from the live API; return None on any failure."""
    url = API_URL + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            import json

            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"warning: {path} unavailable ({exc})", file=sys.stderr)
        return None


# ---------------------------------------------------------------- data collection

def _collect(days: int) -> dict:
    today = date.today()
    start = today - timedelta(days=days - 1)

    focus = _get("/analytics/trends", {"metric": "focus", "weeks": max(1, (days + 6) // 7)})
    strain = _get("/analytics/trends", {"metric": "strain", "weeks": max(1, (days + 6) // 7)})
    strain_summary = _get("/activity/strain-summary")

    employees = []
    if isinstance(strain_summary, list):
        employees = sorted({s.get("employee_id") for s in strain_summary if s.get("employee_id")})

    # Top strain cases: latest strain level per employee, worst first.
    top_strain = []
    if isinstance(strain_summary, list):
        rank = {"red": 0, "amber": 1, "green": 2, "unknown": 3}
        top_strain = sorted(
            strain_summary,
            key=lambda s: (rank.get(s.get("strain_level"), 3), -(s.get("strain_score") or 0)),
        )[:8]

    # Policy flags: violations per employee per day in the window (cap the
    # workload: at most 25 employees x `days` days).
    policy_flags = []
    for emp in employees[:25]:
        for i in range(days):
            day = start + timedelta(days=i)
            data = _get("/policy/violations", {"employee_id": emp, "date": day.isoformat()})
            if not data:
                continue
            for v in data.get("violations", []):
                policy_flags.append(
                    {
                        "employee_id": emp,
                        "date": day.isoformat(),
                        "policy": v.get("policy", ""),
                        "severity": v.get("severity", ""),
                        "detail": v.get("detail", "")[:120],
                    }
                )

    return {
        "start": start,
        "end": today,
        "focus": focus,
        "strain": strain,
        "top_strain": top_strain,
        "policy_flags": policy_flags,
        "employee_count": len(employees),
    }


# ---------------------------------------------------------------- PDF rendering (fpdf2)

def _team_line(section: dict, key: str) -> str:
    if not isinstance(section, dict):
        return "unavailable"
    rows = section.get("team") or []
    if not rows:
        return "no data"
    latest = rows[-1]
    avg = latest.get(f"average_{key}")
    count = latest.get("employee_count", 0)
    if avg is None:
        return f"no data ({count} employees)"
    if key == "focus":
        return f"avg focus score {avg:.1f} (min {latest.get('min_focus')}, max {latest.get('max_focus')}, {count} employees)"
    extra = (
        f"green {latest.get('green', 0)}, amber {latest.get('amber', 0)}, "
        f"red {latest.get('red', 0)}"
    )
    return f"avg strain {avg:.1f} ({extra}, {count} employees)"


def render_pdf(report: dict, out_path: str) -> None:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "WAMSS Weekly Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(
        0, 8,
        f"Reporting window: {report['start'].isoformat()} to {report['end'].isoformat()} "
        f"({report['employee_count']} employees seen)",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    def section(title: str):
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_fill_color(240, 240, 240)
        pdf.cell(0, 9, title, fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    def body(text: str):
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5.5, text)
        pdf.ln(1)

    # 1. Team stats
    section("Team stats")
    body("Focus: " + _team_line(report["focus"], "focus"))
    body("Strain: " + _team_line(report["strain"], "strain"))
    body("Note: strain level is a heuristic workload signal (green < 40, amber 40-69, red >= 70). It is not a medical measure.")

    # 2. Top strain cases
    section("Top strain cases (latest per employee)")
    top = report["top_strain"]
    if not top:
        body("No strain data available for the window.")
    else:
        pdf.set_font("Helvetica", "B", 10)
        widths = (55, 40, 30, 65)
        for w, h in zip(widths, ("Employee", "Machine", "Score", "Level")):
            pdf.cell(w, 7, h, border=1)
        pdf.ln()
        pdf.set_font("Helvetica", "", 10)
        for s in top:
            level = s.get("strain_level") or "unknown"
            score = s.get("strain_score")
            pdf.cell(widths[0], 7, str(s.get("employee_id", ""))[:26], border=1)
            pdf.cell(widths[1], 7, str(s.get("machine_id", ""))[:20], border=1)
            pdf.cell(widths[2], 7, "" if score is None else str(score), border=1)
            if level == "red":
                pdf.set_text_color(200, 30, 30)
            elif level == "amber":
                pdf.set_text_color(180, 120, 0)
            pdf.cell(widths[3], 7, level, border=1)
            pdf.set_text_color(0, 0, 0)
            pdf.ln()
        pdf.ln(2)

    # 3. Policy flags
    section("Policy flags")
    flags = report["policy_flags"]
    if not flags:
        body("No policy violations recorded in the window.")
    else:
        body(f"{len(flags)} violation(s) recorded in the window. Showing the first 20:")
        pdf.set_font("Helvetica", "", 10)
        for f in flags[:20]:
            pdf.multi_cell(
                0, 5.5,
                f"[{f['severity']}] {f['date']} {f['employee_id']}: {f['policy']} - {f['detail']}",
            )
        pdf.ln(1)

    pdf.output(out_path)
    print(f"wrote {out_path}")


# ---------------------------------------------------------------- main

def main() -> int:
    global API_URL
    parser = argparse.ArgumentParser(description="Generate a WAMSS weekly PDF report from the live API.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Reporting window in days (default 7)")
    parser.add_argument("--out", default="wamss-weekly-report.pdf", help="Output PDF path")
    parser.add_argument("--api", default=API_URL, help="Base URL of the WAMSS API")
    args = parser.parse_args()

    API_URL = args.api.rstrip("/")

    report = _collect(max(1, args.days))
    render_pdf(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
