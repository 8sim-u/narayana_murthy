# Employee-owned wellness features

## The privacy boundary

Wellness is optional and separate from the workstation activity notice. Three gates must stay distinct:

1. The existing activity-monitoring consent covers the agent’s work-pattern collection.
2. Optional BLE collection requires `WAMSS_BLE_ENABLED=1`, a per-employee token on that workstation, and a separate local wellness prompt. If the user declines or no prompt can be shown, the wearable stays off.
3. The personal dashboard has a separate server-side wellness-consent switch. Until the employee opts in there, health-data endpoints reject samples and no health summary is returned.

An employee ID in a URL or request body is never an access credential. Personal routes resolve `X-Employee-Token` to exactly one employee using only a configured SHA-256 hash. If the token map is absent, malformed, duplicated or does not match, personal access fails closed. The employee’s individual token is held in page memory only; it is not stored in local storage or sent to a manager.

The existing activity system continues to store workstation activity records under its separate notice, including app/window metadata. The personal view derives only daily active minutes from those records and does not add app names, window titles, task descriptions, messages or typed content to the response. The activity advisory below uses those same aggregates and does not enable new collection.

## What is stored

- **Live wearable sample:** latest BPM and optional RMSSD only, in server process memory, with a 90-second expiry. It is never written to the database or the agent’s offline activity spool. A server restart clears it.
- **Daily health summary:** at most one date-only row per employee/day for RMSSD and resting heart rate. Each stored RMSSD carries a source label: manual/unknown, wearable short-window, overnight, or resting. Overnight/resting labels are employee/source-reported and are not independently verified or inferred from a generic RMSSD value. The dashboard chart shows values with their source labels; raw overnight or beat-to-beat traces are not stored. Existing rows without source metadata are shown as manual/unknown.
- **Sleep session:** employee-private start/end times and source (manual entry or watch-imported times). No raw overnight, sleep-stage, or beat-to-beat traces are stored. No watch-health connector is configured; watch import means employee-entered device-provided times.
- **Breathing session:** only a completed-session duration and optional before/after RMSSD snapshots. Unfinished session state remains in server memory and expires. RR intervals are used transiently on the workstation to calculate RMSSD and are not uploaded.
- **Task outcome:** an optional employee-entered task count per day. Task titles, contents, or descriptions are not accepted.
- **Prediction feedback:** whether the employee considered a current private hint a false positive. It is visible only in that employee’s view.
- **Break record:** at most one private daily marker for a suggested or completed pause. It is not sent to managers.

Withdrawing dashboard wellness consent deletes daily health summaries, completed breathing-session measurements, break markers, and the current in-memory live/session state. It also revokes the separate manager-check-in opt-in. Workstation activity consent is unaffected. Task counts and prediction feedback are not health records and remain subject to the configured retention purge.

## What the personal view calculates

The view requires an individual token and shows only that employee’s daily activity totals, optional health history, own task counts, private coaching hints and feedback. Responses use `Cache-Control: no-store`.

- The HRV trend compares adjacent seven-day medians. It requires at least four daily RMSSD readings in each week and marks a change at 10% or more; it is a personal change signal, not a universal “good/bad” value.
- Morning advisory readiness is shown only for a morning with a recorded sleep session. It is an optional, employee-only 0–100 heuristic using that session’s duration (50%), comparison with that employee’s earlier HRV (30%), and comparison with their earlier resting heart rate (20%). Missing personal-measurement components are omitted and remaining weights are normalized; without a recorded sleep session, no readiness score is shown. It is not a clinical or employment score, must never be used to penalize employees, and is not returned to managers.
- A private pause prompt can appear only when three recent work-strain samples (within 90 minutes) are each at least 70 and the daily HRV-deviation heuristic is sufficiently elevated. The transparent combination is 60% recent work-strain heuristic and 40% HRV deviation. It is shown to the employee only and is not included in activity strain or alerts.
- The employee-only recorded-active-time advisory uses the three most recent completed Monday-Sunday weeks in `WAMSS_TIMEZONE`. Each week needs at least four days with positive recorded active minutes. It appears only when the latest weekly average is at least 10% below the preceding week and at least 20% below the average of the two preceding weekly averages. The card shows the values, dates, and thresholds. It suggests only that recorded active time may remain lower over the next seven days; it is not validated, does not predict productivity, and gives no explanation of cause. Existing workstation activity consent governs the underlying records; optional wellness consent is not required and no health data enters this rule.
- The self-reported task-count check compares reported task counts per recorded active hour across adjacent seven-day windows. It needs at least three self-reported days in each window, at least 80% of the previous active time, and a 30% or larger change. It describes the rule's inputs; it does not establish a cause or measure performance.
- The employee can mark each current hint as a false positive from the token-authenticated personal view. Feedback is stored against the employee ID resolved from that token, is visible only to that employee, and expires under the configured retention purge. The activity advisory is intentionally labeled as advisory; neither it nor the retrospective task-count check is a validated prediction of performance, health, or intent.

## Optional manager check-ins

Manager check-ins are off unless an employee first enables wellness consent and then makes a separate, explicit choice in the personal dashboard. Employees can withdraw that choice at any time. Withdrawing wellness consent also revokes it and deletes the health history used here. Missing consent, a missing or invalid manager roster, or insufficient signal data produces no prompt.

The rule compares the latest and preceding seven-day windows:

- **HRV trend:** compare median RMSSD. At least four daily readings are required in each week, and the recent median must be at least 10% lower.
- **Recorded active-time proxy:** compare average non-idle active minutes per recorded day in those same weeks. At least four days with active minutes are required in each week, and the recent average must be at least 30% lower. This is an imperfect work-pattern proxy, not a measure of engagement, motivation, intent, health, or performance. Leave, schedule changes, holidays, offline work, device or logging gaps, and other ordinary causes can change recorded minutes; the rule does not identify high performers or establish burnout.

When both thresholds match and both employee consents are active, the authorized manager view returns only the employee ID and the fixed message “A supportive check-in may be helpful.” It does not return raw HRV, trend direction, values, scores, thresholds, reasons, sleep/resting-heart-rate data, task reports, or an employment consequence. The prompt is available only from the roster-scoped check-in view; it is not broadcast or sent by email or another external notification. Use it only to offer supportive help, never for performance, attendance, compensation, discipline, hiring, termination, or other employment decisions. It is not a medical, diagnostic, or productivity assessment.

The shared deployment API key alone is not manager authorization. Every manager check-in request requires an individual `X-Manager-Token` whose SHA-256 hash is mapped server-side in `WAMSS_MANAGER_ROSTER_TOKEN_HASHES` to an administrator-authorized roster. The browser cannot provide or expand a roster. Each roster is limited to 100 unique IDs, and every ID must also appear in the configured employee-token map. Missing, malformed, or ambiguous configuration fails closed. This token authorizes only `/wellness/manager-checkins`; broader manager attendance, geofence/location, identity, snapshot, event, and personal-history access remains disabled. When API-key middleware is enabled, the same-origin dashboard proxy must also attach the deployment key; never put that key in browser code. Protect manager tokens in transit with HTTPS and revoke them by removing their hash from protected deployment configuration.

## Employee-token setup

The backend environment variable `WAMSS_EMPLOYEE_TOKEN_HASHES` is a JSON object mapping employee IDs to lowercase SHA-256 token hashes. The same employee identity map is used by self-service activity-history reads; a deployment-wide API key alone does not identify an employee or grant cross-employee access. Wellness endpoints continue to require separate wellness consent where applicable. Example shape (use real employee IDs only in the deployment secret store):

```json
{"employee-001":"<64-character-sha256-hex-digest>"}
```

Generate a fresh raw token and its hash on an administrator-controlled secure machine, then share the raw token only with that employee through an approved secure channel. Store only the hash in the backend secret/environment. On Windows, the employee enters the raw token at the installer’s masked prompt; it is stored as machine-scope DPAPI ciphertext, with filesystem access restricted to that employee’s Windows account and (if enabled) the exact sync-service SID. Do not put the raw token in a workstation `.env`/`config.env`, command line, source control, or chat.

```bash
python - <<'PY'
import hashlib, secrets
raw = secrets.token_urlsafe(32)
print("Give this token only to the employee:", raw)
print("Put this SHA-256 digest in WAMSS_EMPLOYEE_TOKEN_HASHES:", hashlib.sha256(raw.encode()).hexdigest())
PY
```
## Manager-token and roster setup

`WAMSS_MANAGER_ROSTER_TOKEN_HASHES` is optional and should be stored in the protected deployment secret store. It is a JSON object mapping each manager token’s lowercase SHA-256 hash to that manager’s exact employee-ID roster, for example:

```json
{"<64-character-sha256-hex-digest>":["employee-001","employee-002"]}
```

Generate a fresh random token and hash on an administrator-controlled secure machine. Give the raw token only to the authorized manager through an approved secure channel; store only its hash and roster mapping in the backend environment. Use a distinct token per manager and include only employee IDs the manager is authorized to support. Never commit or log raw tokens. Removing the hash or an employee from the roster revokes access after the backend reloads its configuration; restart the service when environment changes are not reloaded automatically.

```bash
python - <<'PY'
import hashlib, secrets
raw = secrets.token_urlsafe(32)
print("Give this token only to the authorized manager:", raw)
print("Put this SHA-256 digest in WAMSS_MANAGER_ROSTER_TOKEN_HASHES:", hashlib.sha256(raw.encode()).hexdigest())
PY
```

The manager dashboard keeps the entered token in page memory only and sends it in `X-Manager-Token`. If deployment API-key authentication is enabled, use the same-origin `/api` proxy; direct frontend-to-backend requests cannot safely attach the deployment key and remain unauthorized. Employee-scoped personal APIs use `X-Employee-Token`. Existing databases receive an additive `rmssd_source` column at startup; no health values are migrated or enriched, and legacy rows remain source-unknown. The dashboard’s same-origin proxy continues to attach the deployment `X-API-Key` server-side. The direct `/wellness/live` wearable endpoint is exempt from the shared-key middleware only because it independently requires the individual employee token and current wellness consent; employee devices do not receive the deployment-wide key for health uploads. Rotate a token by generating a new one and replacing its hash in the deployment environment; removing the mapping revokes it.

## Retention and deployment

`WAMSS_RETENTION_DAYS` defaults to 90. Run `backend/scripts/purge_old_logs.py` on the same schedule as other retained records (for example daily); it now purges expired daily health summaries, sleep sessions, task counts, completed sessions, break markers and prediction feedback, alongside existing records. The consent state itself is retained so an employee’s opt-in/off status survives the purge. Set `WAMSS_TIMEZONE` to the deployment’s intended day boundary; the personal API returns that timezone to the dashboard. Use an IANA timezone such as `Asia/Kolkata` or `UTC`.

The token map is loaded from the backend environment; configure it through the deployment secret store. On Windows, the full-agent installer stores the employee credential only as DPAPI ciphertext and does not distribute the deployment-wide API key. Machine-scope DPAPI still trusts local administrators and LocalSystem; the file ACL protects against other ordinary local accounts. If an employee revokes wellness consent on the dashboard, the server immediately rejects further live samples; the agent does not queue rejected or offline health samples for later retry.

## Evidence and limits

The abstract for [Vagedes et al. (2025)](https://pubmed.ncbi.nlm.nih.gov/39485585/) describes a four-week, three-arm, non-randomized controlled trial of 73 employees: two active mobile HRV-biofeedback training formats and a waitlist control. Burnout scores decreased in both HRV-biofeedback and waitlist groups; the reported Cohen's d values (0.63/0.69 versus 0.27/0.36) are within-group changes over two follow-up intervals, not a direct estimate that biofeedback reduced burnout by more than twice as much as passive wearable tracking. The control was a waitlist, not passive tracking. The authors describe heterogeneous findings and say they need verification in further studies. This study evaluated multi-week active biofeedback training, not this optional one-minute paced-breathing pause or passive wearable monitoring, so it does not establish this feature's effectiveness. This feature is optional, preserves employee control, and makes no medical or employment conclusions.

Implementation follows the relevant [Clean Architecture mini guidance](https://github.com/ciembor/agent-rules-books/blob/main/clean-architecture/clean-architecture.mini.md) (privacy/business rules separated from framework, persistence and UI details) and [Designing Data-Intensive Applications mini guidance](https://github.com/ciembor/agent-rules-books/blob/main/designing-data-intensive-applications/designing-data-intensive-applications.mini.md) (explicit source-of-truth, ephemeral versus durable data, retention, and bounded behavior).
