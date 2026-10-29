# WAMSS API key authentication

The backend protects every API route with a shared API key, sent by clients in
the `X-API-Key` HTTP header. The design is deliberately simple: one key per
deployment, read from the environment, constant-time comparison, no user
accounts and no database lookups on the request path.

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

Everything else (event ingest, activity ingest, security events, focus,
presence, policy, digest endpoints) requires the key when auth is enabled.

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
  place the deployment reads it, update the same value on every client
  (vision pipeline, activity agent, simulator, digest script), then restart
  the backend. The middleware reads the variable once at startup, so a
  restart is what applies the new key.

## Client setup

Each client sends the header only when a key is configured:

| Client | How it picks up the key |
|---|---|
| `vision/multicamera.py` | `WAMSS_API_KEY` environment variable |
| `agent/activity_agent.py` | `WAMSS_API_KEY` environment variable |
| `shared/simulate.py` | `--api-key` flag or `WAMSS_API_KEY` environment variable |
| `backend/scripts/weekly_digest.py` | `WAMSS_API_KEY` environment variable |

`agent/usb_monitor.py` makes no HTTP calls to the backend, so it needs no key.

## Implementation notes

- The middleware lives in `backend/app/auth.py` and is registered in
  `backend/app/main.py` after the access-log/error middleware.
- Key comparison uses `hmac.compare_digest`, which compares in constant time
  so the key cannot be guessed from response timing.
- The middleware reads `WAMSS_API_KEY` once at startup. Changing the variable
  on a running backend has no effect until restart.
- The React dashboard in `frontend/` talks to the backend from a browser; if
  auth is enabled for a deployment the dashboard operator serves, the
  dashboard build for that deployment needs a way to attach the header (for
  example a small API proxy), because a browser client cannot hold a shared
  secret. Out of scope for this change; see the roadmap doc for follow-ups.
