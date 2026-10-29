#!/usr/bin/env bash
# WAMSS database backup (Linux).
#
# Creates a timestamped archive of the backend database, auto-detecting the
# backend in use from DATABASE_URL in .env:
#   - SQLite (default): tars the backend-data Docker volume to a .tgz file.
#   - Postgres (profile): pg_dump from the postgres container to a .sql file.
#
# Usage:
#   bash deploy/backup.sh [--out-dir DIR] [--help]
#
# Safe to run while the stack is up (SQLite backups are a file-level copy;
# run during low write activity for best consistency).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OUT_DIR="./backups"

usage() {
    cat <<'EOF'
Usage: bash deploy/backup.sh [--out-dir DIR]

Creates a timestamped archive of the WAMSS backend database.

Detection (from DATABASE_URL in .env):
  SQLite (default):  archives the backend-data Docker volume as
                     wamss-backup-YYYYmmdd-HHMMSS.tgz
  Postgres:          pg_dump from the postgres service container as
                     wamss-pg-backup-YYYYmmdd-HHMMSS.sql

Options:
  --out-dir DIR   Where to write the archive (default: ./backups)
  --help          Show this help and exit

Restore with: bash deploy/restore.sh <archive>
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --out-dir) OUT_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

command -v docker >/dev/null 2>&1 || { echo "[FAIL] docker not found in PATH" >&2; exit 1; }

# Read DATABASE_URL from .env (fall back to the compose default).
DATABASE_URL="sqlite:////app/data/wamss.db"
if [ -f .env ]; then
    line=$(grep -E "^DATABASE_URL=" .env 2>/dev/null | tail -1 || true)
    if [ -n "$line" ]; then DATABASE_URL="${line#DATABASE_URL=}"; fi
fi

mkdir -p "$OUT_DIR"
TS=$(date +%Y%m%d-%H%M%S)

if [[ "$DATABASE_URL" == postgresql* ]]; then
    # ---- Postgres ---------------------------------------------------------
    echo "==> Postgres backend detected; dumping via pg_dump"
    PGUSER=$(grep -E "^POSTGRES_USER=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true)
    PGDB=$(grep -E "^POSTGRES_DB=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true)
    PGUSER="${PGUSER:-wamss}"
    PGDB="${PGDB:-wamss}"
    OUT_FILE="${OUT_DIR}/wamss-pg-backup-${TS}.sql"
    docker compose exec -T postgres pg_dump -U "$PGUSER" "$PGDB" > "$OUT_FILE"
    echo "[OK] backup written to $OUT_FILE"
else
    # ---- SQLite (named volume) ---------------------------------------------
    echo "==> SQLite backend detected; archiving the data volume"
    CID=$(docker compose ps -q backend 2>/dev/null || true)
    if [ -z "$CID" ]; then
        echo "[FAIL] backend container is not running; start the stack first (bash deploy/deploy.sh)" >&2
        exit 1
    fi
    VOL=$(docker inspect -f '{{ range .Mounts }}{{ if eq .Destination "/app/data" }}{{ .Name }}{{ end }}{{ end }}' "$CID")
    if [ -z "$VOL" ]; then
        echo "[FAIL] could not find the /app/data volume on the backend container" >&2
        exit 1
    fi
    OUT_FILE="${OUT_DIR}/wamss-backup-${TS}.tgz"
    docker run --rm -v "${VOL}:/data:ro" -v "${REPO_ROOT}/${OUT_DIR}:/backup" \
        busybox tar czf "/backup/$(basename "$OUT_FILE")" -C /data .
    echo "[OK] backup of volume '$VOL' written to $OUT_FILE"
fi

echo
echo "Keep this archive somewhere safe (off this machine). Restore with:"
echo "  bash deploy/restore.sh $OUT_FILE"
