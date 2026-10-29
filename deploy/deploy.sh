#!/usr/bin/env bash
# WAMSS one-command deployment (Linux).
#
#   bash deploy/deploy.sh [--help] [--no-build] [--profile PROFILE...]
#
# What it does:
#   1. Checks prerequisites (docker, docker compose plugin, ports 8000/8080 free).
#   2. Creates .env from .env.example on FIRST run only, generating a random
#      WAMSS_API_KEY (and POSTGRES_PASSWORD when the postgres block is active).
#      An existing .env is NEVER overwritten and secrets are NEVER committed.
#   3. Builds and starts the compose stack in the background.
#   4. Waits for the backend health endpoint (GET /health) with a timeout.
#   5. Prints the dashboard URL and next steps.
#
# Idempotent: safe to re-run for updates. Re-runs rebuild and restart the
# stack but preserve .env and the named data volumes.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

NO_BUILD=0
PROFILES=()
HEALTH_TIMEOUT=180

usage() {
    cat <<'EOF'
Usage: bash deploy/deploy.sh [--help] [--no-build] [--profile PROFILE...]

One-command WAMSS deployment (FastAPI backend + React dashboard via Docker).

First run:  creates .env from .env.example and generates a random
            WAMSS_API_KEY (printed once; keep it safe). Existing .env and
            data volumes are preserved on every run.

Options:
  --help              Show this help and exit.
  --no-build          Skip the build step; restart existing images only.
  --profile NAME      Pass an extra compose profile (repeatable).
                      Example: --profile postgres  (with DATABASE_URL set in .env)

After a successful deploy:
  Dashboard:  http://localhost:8080
  Backend:    http://localhost:8000  (health: /health, API docs: /docs)

Idempotent: re-running rebuilds and restarts the stack without touching
.env or the backend-data / pgdata volumes.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --no-build) NO_BUILD=1; shift ;;
        --profile) PROFILES+=("$2"); shift 2 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

step() { echo "==> $1"; }

# 1. Prerequisites -----------------------------------------------------------
step "Checking prerequisites"
command -v docker >/dev/null 2>&1 || { echo "[FAIL] docker not found in PATH" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "[FAIL] docker daemon not reachable" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "[FAIL] docker compose plugin missing" >&2; exit 1; }

for port in 8000 8080; do
    in_use=$(python3 - "$port" 2>/dev/null <<'EOF'
import socket, sys
port = int(sys.argv[1])
s = socket.socket()
try:
    s.bind(("127.0.0.1", port))
    print("no")
except OSError:
    print("yes")
finally:
    s.close()
EOF
)
    if [ "$in_use" = "yes" ]; then
        echo "[FAIL] port $port is already in use; stop the conflicting service or remap it in docker-compose.yml" >&2
        exit 1
    fi
done
echo "[OK] docker, compose plugin, and ports 8000/8080 available"

# 2. .env (first run only) ---------------------------------------------------
step "Preparing .env"
if [ -f .env ]; then
    echo "[OK] .env already exists; keeping it untouched (secrets preserved)"
else
    KEY_LINE=$(python3 - <<'EOF'
import re, secrets, shutil

shutil.copy(".env.example", ".env")
api_key = secrets.token_urlsafe(32)

lines = open(".env").read().splitlines(keepends=True)
out = []
for line in lines:
    if re.match(r"^WAMSS_API_KEY=\s*$", line):
        out.append("WAMSS_API_KEY=" + api_key + "\n")
    elif re.match(r"^POSTGRES_PASSWORD=changeme\s*$", line):
        out.append("POSTGRES_PASSWORD=" + secrets.token_urlsafe(24) + "\n")
    else:
        out.append(line)
open(".env", "w").write("".join(out))
print("GENERATED_API_KEY=" + api_key)
EOF
)
    GENERATED_API_KEY="${KEY_LINE#GENERATED_API_KEY=}"
    echo "[OK] created .env from .env.example with a freshly generated WAMSS_API_KEY"
    echo "      API key (shown once, saved in .env as WAMSS_API_KEY):"
    echo "        $GENERATED_API_KEY"
    echo "      Show it any time with: grep '^WAMSS_API_KEY=' .env"
fi

# 3. Build and start ----------------------------------------------------------
step "Starting the compose stack"
COMPOSE_ARGS=()
# shellcheck disable=SC2086
for p in ${PROFILES[@]+"${PROFILES[@]}"}; do COMPOSE_ARGS+=(--profile "$p"); done
# ${#COMPOSE_ARGS[@]} is safe under `set -u` even on bash < 4.4, where
# expanding an empty array with "${COMPOSE_ARGS[@]}" would be an error.
if [ "$NO_BUILD" -eq 1 ]; then
    if [ "${#COMPOSE_ARGS[@]}" -eq 0 ]; then docker compose up -d;
    else docker compose "${COMPOSE_ARGS[@]}" up -d; fi
else
    if [ "${#COMPOSE_ARGS[@]}" -eq 0 ]; then docker compose up --build -d;
    else docker compose "${COMPOSE_ARGS[@]}" up --build -d; fi
fi

# 4. Wait for backend health --------------------------------------------------
step "Waiting for backend health (GET /health)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
until python3 - <<'EOF' >/dev/null 2>&1
import urllib.request
urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5).read()
EOF
do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "[FAIL] backend did not become healthy within ${HEALTH_TIMEOUT}s" >&2
        echo "       Inspect with: docker compose logs backend" >&2
        exit 1
    fi
    echo "      ... still waiting (backend starting up)"
    sleep 5
done
echo "[OK] backend is healthy"

# 5. Report -------------------------------------------------------------------
echo
echo "================ WAMSS is up ================"
echo "  Dashboard:  http://localhost:8080"
echo "  Backend:    http://localhost:8000"
echo "  API docs:   http://localhost:8000/docs"
echo "============================================="
echo
echo "Next steps:"
echo "  1. Seed demo data:  python3 shared/simulate.py --api-url http://localhost:8000"
echo "     (with the API key:  python3 shared/simulate.py --api-url http://localhost:8000 --api-key \$(grep '^WAMSS_API_KEY=' .env | cut -d= -f2-))"
echo "  2. Install the workstation agent:  deploy/install-agent.sh   (Linux)"
echo "     or  deploy\\install-agent.ps1   (Windows, run as admin)"
echo "  3. Open the dashboard at http://localhost:8080 and verify the seeded data."
echo "  4. To update later, just re-run:  bash deploy/deploy.sh"
