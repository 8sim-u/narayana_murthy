# WAMSS Analytics: CSV Exports, Trends API, and Weekly PDF Report

This page covers the analytics slice of the WAMSS backend and dashboard:
CSV export endpoints, the weekly trends API, the PDF report generator,
and the two frontend components that consume them.

## CSV export endpoints

All export endpoints stream CSV (`text/csv`) with a
`Content-Disposition: attachment` header so the browser downloads the file
directly. Query filters mirror the corresponding JSON endpoints.

### GET /exports/events.csv

Same filters as `GET /events`: `camera_id`, `start_time`, `end_time`,
`limit` (default 1000, max 10000), `offset`. Rows are newest first.

Columns: `id, camera_id, track_id, objects, confidence, zone, decision, alerts, timestamp`.
`objects` and `alerts` are JSON-encoded arrays.

Example:

```
GET /exports/events.csv?camera_id=CAM_01&limit=500
```

### GET /exports/activity.csv

Same filters as `GET /activity`: `machine_id`, `employee_id`, `event_type`,
`start_time`, `end_time`, `limit` (default 1000, max 10000), `offset`.
Rows are newest first.

Columns: `id, machine_id, employee_id, event_type, app_name, window_title,
process_name, start_time, end_time, keystrokes, mouse_clicks, is_idle,
strain_score, strain_level, created_at`. `strain_level` is the server-side
`green / amber / red / unknown` bucket for `strain_score`.

Example:

```
GET /exports/activity.csv?employee_id=E001&event_type=activity
```

### GET /exports/focus.csv

Mirrors `GET /focus/daily`'s `employee_id` filter, extended with a date
range so a full week exports in one call: `start_date` and `end_date`
(`YYYY-MM-DD`, defaulting to the last 7 days ending today; range limited
to 366 days). Scores are computed with the same `app.focus.daily_focus`
logic as `/focus/daily`.

Columns: `employee_id, date, focus_score, focused_blocks, app_switches,
short_segments, idle_fraction, active_minutes, breaks_required,
breaks_taken, break_compliant`.

Example:

```
GET /exports/focus.csv?employee_id=E001&start_date=2026-10-23&end_date=2026-10-30
```

## Trends API

### GET /analytics/trends?metric=focus|strain&weeks=N

Returns weekly aggregates per employee plus team-level rollups.
`weeks` is the number of trailing 7-day buckets ending today (default 4,
max 52). Week 0 is the most recent 7 days; each bucket is labeled by its
first calendar date (`week_start`).

**metric=focus.** Per employee per week: the mean of that employee's daily
focus scores for days with data (days with no activity rows are excluded,
they do not drag the average down). Team rollup: mean, min, max of the
per-employee weekly means, plus `employee_count`.

**metric=strain.** Per employee per week: mean `strain_score` over scored
rows plus counts of `green / amber / red / unknown` strain levels. Team
rollup: mean of per-employee weekly averages and summed level counts.
Strain is a heuristic workload signal, not a medical measure.

Example:

```
GET /analytics/trends?metric=strain&weeks=8
```

Response shape (focus):

```json
{
  "metric": "focus",
  "weeks": 8,
  "per_employee": [
    {"week_start": "2026-10-24", "employee_id": "E001",
     "average_focus": 82.5, "days_with_data": 5}
  ],
  "team": [
    {"week_start": "2026-10-24", "average_focus": 74.2,
     "min_focus": 61.0, "max_focus": 88.0, "employee_count": 6}
  ]
}
```

## Weekly PDF report

`backend/scripts/weekly_report_pdf.py` builds a one-page-ish PDF weekly
report from the live API. It needs the `fpdf2` package (already in
`backend/requirements.txt`).

```
python3 backend/scripts/weekly_report_pdf.py [--days 7] [--out weekly-report.pdf]
```

Environment: `WAMSS_API_URL` (default `http://localhost:8000`), or the
`--api` flag.

Report contents:

1. Title and reporting window, with the employee count seen.
2. Team stats: average focus and strain for the window, from
   `/analytics/trends`.
3. Top strain cases: latest strain level per employee, worst first, from
   `/activity/strain-summary`.
4. Policy flags: violations per employee per day in the window, from
   `/policy/violations`.

Each section degrades to "unavailable" if its endpoint is missing (for
example `/analytics/trends` before the analytics router is mounted).

## Frontend components

**ExportButton** (`frontend/src/components/ExportButton.tsx`): reusable
CSV download button. Props: `endpoint` (e.g. `/exports/activity.csv`),
`label`, `filters` (forwarded as query params), `filename`. Renders a
plain anchor so the browser handles the download.

**AnalyticsTab** (`frontend/src/components/AnalyticsTab.tsx`): renders
`/analytics/trends` with a metric toggle (focus/strain) and a weeks
selector. Team rollup is drawn as a pure-SVG bar chart (no chart
dependency); the latest week renders a per-employee table with CSS
trend bars.
