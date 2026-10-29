#!/usr/bin/env bash
# WAMSS preflight checklist (Linux).
#
# Verifies the host is ready for deployment before running deploy/deploy.sh:
#   bash deploy/preflight.sh
#
# Checks (critical unless noted):
#   1. python3 >= 3.10
#   2. docker present and reachable
#   3. docker compose plugin present
#   4. at least 5 GB free disk space
#   5. ports 8000 and 8080 free
#   6. BLE adapter presence (best-effort, warning only; needed only for the
#      optional smartwatch heart-rate module)
#
# Exits non-zero if any critical check fails. Safe to re-run.

set -u

FAIL=0
WARN=0

pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }
warn() { echo "[WARN] $1"; WARN=$((WARN + 1)); }

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    cat <<'EOF'
Usage: bash deploy/preflight.sh

Runs the WAMSS deployment preflight checklist on the local host.

Critical checks (exit non-zero when any fails):
  python3 >= 3.10, docker present and reachable, docker compose plugin,
  >= 5 GB free disk space, ports 8000 and 8080 free.

Warning-only checks (never fail the run):
  BLE adapter presence (needed only for the optional smartwatch module).
EOF
    exit 0
fi

echo "=== WAMSS preflight checklist ==="
echo

# 1. Python >= 3.10 ---------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
    if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
        pass "python3 $(python3 --version 2>&1 | cut -d' ' -f2) (>= 3.10)"
    else
        fail "python3 found but version < 3.10: $(python3 --version 2>&1)"
    fi
else
    fail "python3 not found in PATH (need >= 3.10)"
fi

# 2. Docker -----------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        pass "docker present and reachable: $(docker --version | head -1)"
    else
        fail "docker is installed but the daemon is not reachable (try: sudo systemctl start docker, or check docker group membership)"
    fi
else
    fail "docker not found in PATH"
fi

# 3. Docker compose plugin --------------------------------------------------
if docker compose version >/dev/null 2>&1; then
    pass "docker compose plugin: $(docker compose version --short 2>/dev/null || docker compose version)"
else
    fail "docker compose plugin missing (need Docker with the compose plugin, not the standalone docker-compose)"
fi

# 4. Disk space >= 5 GB free ------------------------------------------------
FREE_KB=$(df -k . --output=avail 2>/dev/null | tail -1 | tr -d ' ')
if [ -n "$FREE_KB" ] && [ "$FREE_KB" -ge 5242880 ] 2>/dev/null; then
    pass "free disk space: $((FREE_KB / 1024 / 1024)) GB (>= 5 GB)"
else
    fail "free disk space below 5 GB (found: ${FREE_KB:-unknown} KB)"
fi

# 5. Ports 8000 and 8080 free -----------------------------------------------
port_free() {
    python3 - "$1" 2>/dev/null <<'EOF'
import socket, sys
port = int(sys.argv[1])
s = socket.socket()
try:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    print("free")
except OSError:
    print("in-use")
finally:
    s.close()
EOF
}

for port in 8000 8080; do
    if [ "$(port_free "$port")" = "free" ]; then
        pass "port $port is free"
    else
        fail "port $port is already in use (stop the conflicting service or remap the port in docker-compose.yml)"
    fi
done

# 6. BLE adapter (warning only) ---------------------------------------------
BLE_FOUND=0
if [ -d /sys/class/bluetooth ] && ls /sys/class/bluetooth >/dev/null 2>&1; then
    BLE_FOUND=1
elif command -v bluetoothctl >/dev/null 2>&1 && bluetoothctl list 2>/dev/null | grep -q .; then
    BLE_FOUND=1
elif command -v hciconfig >/dev/null 2>&1 && hciconfig 2>/dev/null | grep -q "^hci"; then
    BLE_FOUND=1
fi
if [ "$BLE_FOUND" = "1" ]; then
    pass "BLE adapter detected (smartwatch module available)"
else
    warn "no BLE adapter detected; the agent will run with heuristic-only strain scoring (smartwatch heart-rate disabled)"
fi

echo
echo "=== Result: $FAIL critical failure(s), $WARN warning(s) ==="
if [ "$FAIL" -gt 0 ]; then
    echo "Preflight FAILED. Fix the items above and re-run."
    exit 1
fi
echo "Preflight OK. Next: bash deploy/deploy.sh"
