# WAMSS API key authentication

The backend uses a shared API key, sent by clients in the `X-API-Key` HTTP
header, to authenticate deployment clients. This key is not an employee
identity: employee activity and personal-history reads also require a separate
`X-Employee-Token` that the server maps to exactly one employee.

## How to turn it on

Set the `WAMSS_API_KEY` environment variable wherever the backend runs:

```bash
export WAMSS_API_KEY="<your-long-random-key>"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

When `WAMSS_API_KEY` is set, any request without a correct `X-API-Key` header
gets HTTP 401 with a JSON body:

```json
{"detail": "Missing or invalid API key. Send the deployment key in the 'X-API-Key' header."}
```

## Local development stays open

When `WAMSS_API_KEY` is unset (or empty), authentication is disabled and the
app logs a startup warning:

```
WARNING wamss.auth: WAMSS_API_KEY is not set; API key auth is DISABLED. ...
```

This keeps local development and the test suite frictionless. Production and
shared demo deployments should always set the variable.

## What is exempt

Health checks and API docs never require a key, so load balancers, uptime
probes and the interactive docs keep working:

- `/health`
- `/health/db`
- `/docs`
- `/openapi.json`
- `/redoc`
- `/wellness/live` (requires a valid `X-Employee-Token` and separate wellness consent)

When `WAMSS_API_KEY` is enabled, other routes require the deployment key except
for **`POST /activity` requests that include `X-Employee-Token`**. This is a
method-specific, employee-token-specific exemption: `GET /activity`, other
methods, and `POST /activity` without an employee token are not exempt.
Employee-scoped reads additionally require an employee token, including
`/activity`, `/activity/app-usage`, `/activity/strain-summary`, focus and hours
history, security alerts, policy violations, activity/focus exports, presence,
attendance, analytics trends, and `/wellness/me`. Those routes always bind the
query and returned records to the employee resolved from the token; an omitted
employee filter means "my records," and requesting another employee is denied.
`POST /activity` with an employee token resolves that token and verifies every
submitted `employee_id` against it; an invalid or mismatched token is rejected
and never falls back to service identity. This lets the workstation client
avoid receiving the deployment-wide key. `POST /security/events` continues to
require the global key when enabled. Multi-employee `/activity` ingestion
remains available to trusted services only when
`WAMSS_EMPLOYEE_INGESTION_SERVICE_ACCESS=1` is explicitly enabled on the
backend; such requests must omit the employee token and carry an API key already
validated by middleware. The service exception is disabled by default, and a
supplied employee token is always checked even when service access is enabled.

## Key distribution per deployment

- **One key per deployment.** Do not share the same key between production,
  staging and personal dev machines. A leaked key for one deployment should
  never open another.
- **Env vars, not config files.** Pass the key as an environment variable in
  your process manager, systemd unit, Docker/Kubernetes secret, or CI secret
  store. Do not commit it to git, paste it into chat, or check it into
  config files.
- **`.env` files.** If you use a `.env` file for local or edge deployments,
  keep it out of version control (add it to `.gitignore`) and restrict file
  permissions (for example `chmod 600 .env`).
- **Rotation.** Generate a fresh random key (for example 32 bytes from a
  cryptographic random source, base64 or hex encoded), update it in the same
  place the deployment reads it, and update trusted clients that actually need
  it (such as the vision pipeline, simulator, or digest script), then restart
  the backend. Windows workstation agents use employee-scoped tokens instead
  and never receive this global key. The middleware reads the variable once at
  startup, so a restart is what applies the new key.

## Client setup

Each client sends the header only when a key is configured:

| Client | How it picks up the key |
|---|---|
| `vision/multicamera.py` | `WAMSS_API_KEY` environment variable |
| Windows workstation agents | Employee token in a DPAPI-protected store; the Windows installer does not provision the global key |
| `shared/simulate.py` | `--api-key` flag or `WAMSS_API_KEY` environment variable |
| `backend/scripts/weekly_digest.py` | `WAMSS_API_KEY` environment variable |
| `backend/scripts/weekly_report_pdf.py` | `WAMSS_EMPLOYEE_TOKEN` plus `WAMSS_API_KEY` when enabled; personal attendance only |

`agent/usb_monitor.py` makes no HTTP calls to the backend, so it needs no key.

## Dashboard access

The React dashboard in `frontend/` talks to the backend from a browser, so it
cannot hold the shared secret itself. The composed deployment solves this with
a same-origin reverse proxy: the frontend nginx container serves the dashboard
and proxies `/api/*` to the backend, attaching `X-API-Key` from its own
`WAMSS_API_KEY` environment variable (see `frontend/nginx.conf.template`;
the key is rendered into the nginx config at container startup by the nginx
image's envsubst templating, and the browser never sees it). The dashboard
calls the relative path `/api` by default (`VITE_API_URL` unset), so rotating
the key needs no rebuild, only `docker compose up -d` to re-render the proxy
config and restart the backend. Set `VITE_API_URL` at build time only if the
dashboard must talk to a remote backend directly; in that case the browser
calls the backend without the header and authenticated endpoints return 401.

## Implementation notes

- The middleware lives in `backend/app/auth.py` and is registered in
  `backend/app/main.py` after the access-log/error middleware.
- Key comparison uses `hmac.compare_digest`, which compares in constant time
  so the key cannot be guessed from response timing.
- The middleware reads `WAMSS_API_KEY` once at startup. Changing the variable
  on a running backend has no effect until restart.
- The dashboard's API access goes through the frontend nginx `/api/` reverse
  proxy, which attaches the header server-side (see "Dashboard access"
  above).

## Employee-scoped read, write, and wellness access

Employee-scoped reads, employee-attributed writes, and wellness routes require a separate `X-Employee-Token` check. Configure `WAMSS_EMPLOYEE_TOKEN_HASHES` as a JSON object from employee ID to lowercase SHA-256 token hash. The raw personal token is not stored by the backend, and sending only an employee ID or the shared deployment key never grants access to another employee's records. Missing, malformed, or ambiguous identity configuration fails closed. See [`WELLNESS_PRIVACY.md`](WELLNESS_PRIVACY.md) for secure token generation and the separate wellness-consent flow.

Keep each employee token in that employee's protected client environment; do not place it in a shared dashboard build or source control. The Windows agent stores its employee token with machine-scope DPAPI and a filesystem ACL limited to the assigned Windows user and, when enabled, the exact sync-service SID. The installer does not provision the deployment-wide API key. Employee tokens map to one employee and do not authorize manager access. The shared deployment key remains server-side in the same-origin dashboard proxy and is never bundled into browser code. Do not enable service ingestion for browser callers; the narrow service path remains disabled by default and requires a validated API key. `/wellness/live` is exempt from shared-key middleware only because it independently requires an individual token and server-side wellness consent; other optional wellness routes remain key-gated and the Windows workstation client is not provisioned with the global key.

The optional `/wellness/manager-checkins` endpoint additionally requires an individual `X-Manager-Token` mapped server-side through `WAMSS_MANAGER_ROSTER_TOKEN_HASHES` to an administrator-authorized roster. This is a separate credential from both employee tokens and the deployment key; the browser cannot submit a roster. Every roster ID must exist in the configured employee-token map. Each employee must also have separately opted in to manager check-ins after enabling wellness consent. The endpoint returns only a fixed supportive prompt and the employee ID, never raw health values or unscoped records. When API-key middleware is enabled, the same-origin `/api` proxy must attach the deployment key as well. Missing or invalid manager configuration fails closed. See [`WELLNESS_PRIVACY.md`](WELLNESS_PRIVACY.md) for token setup, thresholds, notice, and prohibited uses.


Camera events accepted with active, camera-specific consent are stamped at ingestion with the mapped employee ID. `/events`, event detail, CSV event exports, and camera-derived attendance filter by that stored owner rather than a mutable display name. Historical event rows without an owner are intentionally unavailable through personal history. The unscoped person-level map, location, geofence-history, identity, unknown-sighting, and snapshot reads remain disabled. The Issue 19 manager token authorizes only the roster-scoped wellness check-in endpoint; it does not grant access to these broader records. A deployment key alone is not manager identity. The face gallery and face enrollment/deletion operations require an enabled, middleware-validated deployment key, and gallery results additionally require active consent for the exact identity-mode camera.
