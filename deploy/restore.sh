#!/usr/bin/env bash
# WAMSS database restore (Linux).
#
# Restores a backup archive created by deploy/backup.sh:
#   bash deploy/restore.sh backups/wamss-backup-20261030-120000.tgz
#   bash deploy/restore.sh backups/wamss-pg-backup-20261030-120000.sql
#
# The archive type decides the method:
#   - .tgz: extracted into the backend-data Docker volume (backend container
#     is stopped first and restarted after; the volume's current content is
#     replaced, so take a fresh backup first if you might need it).
#   - .sql: fed into the postgres service via psql (postgres profile must
#     be running with the same DATABASE_URL).
#
# This REPLACES the current database. Confirm at the prompt, or pass --yes.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

YES=0

usage() {
    cat <<'EOF'
Usage: bash deploy/restore.sh <archive> [--yes]

Restores a WAMSS backup created by deploy/backup.sh. The current database
is REPLACED, so take a fresh backup first if you might need the old data.

Arguments:
  <archive>   Path to a .tgz (SQLite volume backup) or .sql (Postgres dump)

Options:
  --yes       Skip the confirmation prompt
  --help      Show this help and exit

Examples:
  bash deploy/restore.sh backups/wamss-backup-20261030-120000.tgz
  bash deploy/restore.sh backups/wamss-pg-backup-20261030-120000.sql --yes
EOF
}

ARCHIVE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --yes) YES=1; shift ;;
        -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
        *) ARCHIVE="$1"; shift ;;
    esac
done

if [ -z "$ARCHIVE" ]; then echo "Missing archive path." >&2; usage >&2; exit 2; fi
if [ ! -f "$ARCHIVE" ]; then echo "[FAIL] archive not found: $ARCHIVE" >&2; exit 1; fi
command -v docker >/dev/null 2>&1 || { echo "[FAIL] docker not found in PATH" >&2; exit 1; }

if [ "$YES" -ne 1 ]; then
    echo "This will REPLACE the current WAMSS database with: $ARCHIVE"
    read -rp "Continue? (y/N): " ans
    if [ "$ans" != "y" ] && [ "$ans" != "Y" ]; then echo "Cancelled."; exit 0; fi
fi

case "$ARCHIVE" in
    *.tgz|*.tar.gz)
        echo "==> Restoring SQLite volume backup"
        echo "==> Stopping backend (data will be replaced)"
        docker compose stop backend 2>/dev/null || true
        CID=$(docker compose ps -q backend 2>/dev/null || true)
        if [ -z "$CID" ]; then
            echo "[FAIL] backend container not found; is the stack deployed?" >&2
            exit 1
        fi
        VOL=$(docker inspect -f '{{ range .Mounts }}{{ if eq .Destination "/app/data" }}{{ .Name }}{{ end }}{{ end }}' "$CID")
        if [ -z "$VOL" ]; then
            echo "[FAIL] could not find the /app/data volume on the backend container" >&2
            exit 1
        fi
        ABS_ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"
        echo "==> Clearing volume '$VOL' and extracting archive"
        docker run --rm -v "${VOL}:/data" -v "${ABS_ARCHIVE}:/restore/archive.tgz:ro" \
            busybox sh -c 'find /data -mindepth 1 -delete; tar xzf /restore/archive.tgz -C /data'
        echo "==> Restarting backend"
        docker compose start backend
        echo "[OK] restore complete; backend restarted"
        ;;
    *.sql)
        echo "==> Restoring Postgres dump"
        PGUSER=$(grep -E "^POSTGRES_USER=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true)
        PGDB=$(grep -E "^POSTGRES_DB=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true)
        PGUSER="${PGUSER:-wamss}"
        PGDB="${PGDB:-wamss}"
        if ! docker compose ps postgres 2>/dev/null | grep -q "Up\|running"; then
            echo "[FAIL] postgres service is not running; start it first (deploy.sh --profile postgres)" >&2
            exit 1
        fi
        docker compose exec -T postgres psql -U "$PGUSER" -d "$PGDB" < "$ARCHIVE"
        echo "[OK] Postgres restore complete"
        ;;
    *)
        echo "[FAIL] unknown archive type: $ARCHIVE (expected .tgz or .sql)" >&2
        exit 2
        ;;
esac

echo "Verify with: curl http://localhost:8000/health  (and check the dashboard)"
