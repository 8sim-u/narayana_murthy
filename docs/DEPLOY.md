# Deploying WAMSS with Docker

This runs the full stack (FastAPI backend + React dashboard) in containers.
No Python or Node needed on the host, only Docker with the Compose plugin.

## One-command deploy

```bash
cp .env.example .env   # edit values as needed
docker compose up --build
```

Then open:

- Dashboard: http://localhost:8080
- Backend API: http://localhost:8000 (health check at `/health`)
- API docs: http://localhost:8000/docs

Stop with `Ctrl+C`. Tear down containers and the network (data volumes kept)
with `docker compose down`. To also delete stored data: `docker compose down -v`.

## Environment variable reference

Values come from the `.env` file in the repo root (Docker Compose reads it
automatically for `${...}` interpolation). Copy `.env.example` to `.env` first.
Every default below is the value used when the variable is unset.

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | backend | `sqlite:////app/data/wamss.db` | SQLAlchemy database URL. SQLite file inside the `backend-data` volume, or a Postgres URL (see below). Required: the backend refuses to boot without it. |
| `WAMSS_API_KEY` | backend | (empty) | When set, every backend request must send the `X-API-Key` header. When empty, authentication stays disabled (development default). |
| `VITE_API_URL` | frontend | (empty) | Backend base URL baked into the dashboard at build time. Empty (default) means same-origin: the dashboard calls the relative path `/api/*`, which the frontend nginx reverse-proxies to the backend with the API key attached server-side. Set it only to point the dashboard at a remote backend directly; that URL is what the *browser* calls, so it must be browser-reachable, and authenticated endpoints will 401 without a proxy attaching the key. |
| `POSTGRES_USER` | postgres | `wamss` | Postgres superuser created on first boot (postgres profile only). |
| `POSTGRES_PASSWORD` | postgres | `changeme` | Password for `POSTGRES_USER`. Change this before any shared deployment. |
| `POSTGRES_DB` | postgres | `wamss` | Database created on first boot (postgres profile only). |

Notes:

- `VITE_API_URL` is a build arg, not a runtime setting. Changing it requires
  `docker compose build frontend` (or `up --build`). Leave it empty (default):
  the dashboard then uses the same-origin `/api/*` proxy, which also solves
  API key auth for the browser (the key is attached server-side by nginx).
  If the dashboard shows connection errors, check that the frontend container
  was rebuilt after any `VITE_API_URL` change.
- When `WAMSS_API_KEY` is set, the simulator and any other direct client must
  send the key (see "Simulator" below). The dashboard needs no manual header:
  the nginx `/api/` proxy attaches it (see docs/AUTH.md).
- Never commit `.env` (it is gitignored). `.env.example` holds placeholders only.

## SQLite vs Postgres

**SQLite (default).** Zero setup: the backend writes to `/app/data/wamss.db`
inside the named `backend-data` volume, so data survives container restarts and
rebuilds. Right for local dev, demos, and single-machine pilots. Tables are
created with `create_all` on boot.

**Postgres (production-ish).** Gated behind the `postgres` profile so the
default deploy stays dependency-free:

```bash
docker compose --profile postgres up --build
```

and in `.env` set:

```env
DATABASE_URL=postgresql+psycopg://wamss:changeme@postgres:5432/wamss
POSTGRES_USER=wamss
POSTGRES_PASSWORD=changeme
POSTGRES_DB=wamss
```

Change the password before any shared deployment. Data persists in the `pgdata`
volume. The backend waits on nothing: on first boot Postgres may still be
initializing, so `init_db()` logs a warning and retries on the next request;
in practice the health-gated frontend waits until the backend is up, and event
ingestion succeeds once Postgres accepts connections (a few seconds).

To go back to SQLite, stop the stack, set `DATABASE_URL` back to the sqlite
value, and run without `--profile postgres`.

## Running the simulator against the composed stack

`shared/simulate.py` uses only the Python standard library, so it runs on the
host with plain `python3` while the stack runs in Docker:

```bash
# Seed 5 employees x 7 days against the composed backend
python3 shared/simulate.py --api-url http://localhost:8000

# If WAMSS_API_KEY is set in .env, pass the same key:
WAMSS_API_KEY=<your-key> python3 shared/simulate.py --api-url http://localhost:8000
# or: python3 shared/simulate.py --api-url http://localhost:8000 --api-key <your-key>
```

Then refresh the dashboard at http://localhost:8080 to see the seeded events,
activity, and security data. For a dry run that posts nothing:

```bash
python3 shared/simulate.py --dry-run
```

## Rebuilding after code changes

```bash
docker compose up --build        # rebuild changed services and restart
docker compose build --no-cache  # full clean rebuild
```

Only the frontend needs a rebuild when `VITE_API_URL` changes; only the backend
when Python code changes.

## Troubleshooting

- `docker compose up` fails with "DATABASE_URL is required": your `.env` file
  is missing and interpolation fell through. `cp .env.example .env` and retry.
- Dashboard loads but shows no data / connection errors: the browser calls
  the relative path `/api/*`, which the frontend nginx proxies to the
  backend. Check `docker compose logs frontend` for proxy errors and make
  sure the frontend container was recreated after the last compose change
  (`docker compose up -d`). Only if you overrode `VITE_API_URL` at build
  time does the browser call the backend directly; then the URL must be
  browser-reachable and key auth will 401 without a proxy.
- Port already in use (8000 or 8080): stop the conflicting process, or change
  the host side of the port mapping in `docker-compose.yml`
  (e.g. `"8081:80"`).
- Backend container exits immediately: check `docker compose logs backend`.
  The most common cause is an unreachable `DATABASE_URL`.
