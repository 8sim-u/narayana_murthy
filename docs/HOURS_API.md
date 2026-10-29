
## Work-hours baselining API (backend/app/hours.py)

CERT-style per-user hours baselining built from `ActivityLog` rows
(`event_type="activity"`, `is_idle=False`). The router is mounted in
`backend/app/main.py`.

### GET /hours/baseline?employee_id=X[&days=N]

30-day rolling histogram (N configurable 7-90) of active 15-minute bins.
Each activity row contributes its active minutes to 15-minute bins (96 per
day) in `WAMSS_HOURS_TZ` (default "UTC"); rows spanning bin boundaries
split minutes across the touched bins. Idle rows are excluded.

Response:

```json
{
  "employee_id": "e001",
  "normal_start": "09:00",
  "normal_end": "18:00",
  "bins": [0, 0, ..., 30, ...],
  "days_used": 30,
  "mean_daily_active_minutes": 450.0,
  "std_daily_active_minutes": 45.0
}
```

* `normal_start` / `normal_end`: 5th percentile of daily first-active bins
  and 95th percentile of daily last-active bins, formatted "HH:MM". Days
  with no activity are excluded from the percentile math.
* `bins`: 96 counts, one per 15-minute bin: number of days in the window
  with activity in that bin.
* With no data the endpoint returns 200 with `normal_start`/`normal_end`
  null, zero bins, and `days_used` 0.

### GET /hours/anomaly?employee_id=X

Anomaly check: z-score of the last 24h of activity against the personal
30-day norm (the 30 days *before* the last 24h), an after-hours flag, and a
burstiness metric. Always returns 200.

```json
{
  "employee_id": "e001",
  "time_anomaly_score": -5.0,
  "after_hours_activity": true,
  "burstiness": 3.2,
  "recent_active_minutes": 180.0,
  "normal_start": "09:00",
  "normal_end": "18:00"
}
```

* `time_anomaly_score`: `(recent_minutes - baseline_mean) / baseline_std`;
  0.0 when the baseline std is 0 or undefined.
* `after_hours_activity`: true when any recent active minute falls outside
  the baseline normal window. With no baseline days, falls back to
  `WAMSS_WORK_HOURS` (default "09:00-18:00").
* `burstiness`: coefficient of variation (std/mean) of per-bin active
  minutes across the 96 bins of the last 24h. A uniform day scores near 0;
  activity concentrated in a few bins scores high.

All scoring math lives in pure, importable helpers
(`daily_active_spans`, `compute_baseline`, `normal_window`,
`time_anomaly_score`, `after_hours_flag`, `burstiness`), unit-tested in
`backend/tests/test_hours.py` alongside endpoint tests with synthetic rows.
