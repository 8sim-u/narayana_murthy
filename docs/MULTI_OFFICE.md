# Multi-office support

WAMSS tracks which office (or site) each workstation belongs to with a
free-form `office_id` string, so one deployment can serve several offices
and the dashboard can be filtered to a single site.

## The office_id model

* `ActivityLog.office_id` and `SecurityEvent.office_id`: string columns,
  indexed, default `"hq"`. The column carries a `server_default="hq"`, so
  rows written by older agents or older backends keep working as `"hq"`.
* The workstation agent sends `office_id` on every activity record it
  posts. It reads the value from the `WAMSS_OFFICE_ID` environment variable
  and falls back to `"hq"` when unset (see `agent/.env.example`).
* Ingest schemas (`ActivityRecordIn`, `SecurityEventIn`) accept
  `office_id` and default it to `"hq"` when the client omits it, so older
  agent versions keep working unchanged.
* Read schemas (`ActivityRecordOut`, `AlertOut`) expose `office_id`.

No migration file is needed for new databases: the app creates tables with
`Base.metadata.create_all()` in `init_db()`, which now includes the new
column. Existing databases need a one-time manual step:

```sql
ALTER TABLE activity_logs ADD COLUMN office_id VARCHAR(64) NOT NULL DEFAULT 'hq';
ALTER TABLE security_events ADD COLUMN office_id VARCHAR(64) NOT NULL DEFAULT 'hq';
```

## Filtering

These endpoints accept an optional `office_id` query parameter. When given,
only that office's rows are used; when omitted, all offices are included.

| Endpoint | Scope of the filter |
|---|---|
| `GET /activity` | Filters activity records by `office_id`. |
| `GET /presence` | Filters the workstation activity logs used in the correlation. Camera/zone correlation itself is zone-map based and office-agnostic. |
| `GET /focus/daily` | Computes the focus score from the employee's rows in that office only. |
| `GET /security/alerts` | Filters security events by `office_id`; returned alerts carry the event's `office_id`. |

Not filtered by design (server-side): `GET /activity/strain-summary` and
`GET /focus/streaks` have no `office_id` parameter yet, so their dashboard
panels stay global. The team view's strain and streak panels are the ones
affected; the activity-based fallback in the strain panel does respect the
office filter.

## Dashboard usage

* The header carries an **Office** picker (`OfficeSelector`). Leaving it on
  "All offices" sends no `office_id` parameter (global view). Typing or
  picking an office id (for example `hq` or `branch-north`) filters every
  supported tab: Workstation Activity, Presence, Focus Score, Security, and
  Team.
* Office ids are free-form text; the picker suggests `hq` and accepts any
  typed value.

## Mobile / responsive behavior

The dashboard is usable on narrow screens:

* The tab bar scrolls horizontally instead of wrapping off-screen.
* Panels stack to a single column; toolbars and filter rows wrap or stack.
* Wide tables (events, activity timeline, focus breakdown) scroll
  horizontally inside their panels instead of overflowing the page.
* Presence cards and strain chips stack full-width; security alert items
  stack their badge above the body under 480 px.
* Buttons, selects, and inputs have 44 px minimum tap targets, and form
  fields use 16 px text so mobile browsers do not auto-zoom.
