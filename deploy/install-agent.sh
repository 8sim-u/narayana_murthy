#!/usr/bin/env bash
# WAMSS workstation activity agent installer (Linux).
#
# Installs the agent as a systemd USER service:
#   bash deploy/install-agent.sh [--install-dir DIR] [--server-url URL]
#       [--machine-id ID] [--employee-id ID] [--office-id ID]
#   bash deploy/install-agent.sh --uninstall [--install-dir DIR]
#
# Missing options are asked interactively. The service reads
# ~/.config/wamss-agent.env (KEY=VALUE) and runs the agent in a venv.
#
# Employee notice and consent are required before installing this agent on
# any workstation (see agent/README.md "Privacy and consent"). On first run
# the agent asks for a typed YES on the console; nothing is logged until
# consent is recorded.
#
# Safe to re-run: it refreshes sources, config, and the unit, then restarts
# the service. Data in the spool directory is preserved.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

INSTALL_DIR="${HOME}/.wamss-agent"
SERVER_URL=""
MACHINE_ID=""
EMPLOYEE_ID=""
OFFICE_ID="hq"
UNINSTALL=0

usage() {
    cat <<'EOF'
Usage:
  bash deploy/install-agent.sh [options]          Install / refresh the agent
  bash deploy/install-agent.sh --uninstall        Remove the agent

Options:
  --install-dir DIR   Install location (default: ~/.wamss-agent)
  --server-url URL    Backend base URL, e.g. http://192.0.2.10:8000
  --machine-id ID     Stable id for this workstation, e.g. workstation-042
  --employee-id ID    Employee id, e.g. employee-042
  --office-id ID      Office/site id (default: hq)
  --uninstall         Remove the systemd user service and install dir
  --help              Show this help and exit

Any option you omit is asked interactively. The agent runs as a systemd
user service named wamss-agent, enabled at login.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        --server-url)  SERVER_URL="$2";  shift 2 ;;
        --machine-id)  MACHINE_ID="$2";  shift 2 ;;
        --employee-id) EMPLOYEE_ID="$2"; shift 2 ;;
        --office-id)   OFFICE_ID="$2";   shift 2 ;;
        --uninstall)   UNINSTALL=1; shift ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

UNIT_NAME="wamss-agent.service"
UNIT_FILE="${HOME}/.config/systemd/user/${UNIT_NAME}"
ENV_FILE="${HOME}/.config/wamss-agent.env"

prompt() { # prompt <var> <message> [default]
    local var="$1" msg="$2" def="${3:-}" val
    eval "val=\${$var}"
    if [ -n "$val" ]; then return 0; fi
    if [ -n "$def" ]; then
        read -rp "$msg [$def]: " val
        val="${val:-$def}"
    else
        read -rp "$msg: " val
    fi
    eval "$var=\$val"
}

# ---- Uninstall ---------------------------------------------------------------
if [ "$UNINSTALL" -eq 1 ]; then
    echo "==> Removing WAMSS agent"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user stop "$UNIT_NAME" 2>/dev/null || true
        systemctl --user disable "$UNIT_NAME" 2>/dev/null || true
    fi
    rm -f "$UNIT_FILE" "$ENV_FILE"
    rm -rf "$INSTALL_DIR"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload 2>/dev/null || true
    fi
    echo "[OK] agent removed (service, unit, env file, and $INSTALL_DIR)"
    exit 0
fi

# ---- Install -----------------------------------------------------------------
echo "==> WAMSS workstation agent installer (Linux)"

if ! command -v systemctl >/dev/null 2>&1; then
    echo "[FAIL] systemctl not found; this installer needs systemd" >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "[FAIL] python3 not found in PATH" >&2
    exit 1
fi
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    echo "[FAIL] python3 >= 3.10 is required (found: $(python3 --version 2>&1))" >&2
    exit 1
fi
if ! python3 -c "import venv" 2>/dev/null; then
    echo "[FAIL] python3 venv module missing (install python3-venv)" >&2
    exit 1
fi

prompt SERVER_URL "Backend server URL (e.g. http://192.0.2.10:8000)"
prompt MACHINE_ID "Machine id (e.g. workstation-042)"
prompt EMPLOYEE_ID "Employee id (e.g. employee-042)"
prompt OFFICE_ID "Office id" "hq"

SRC_DIR="$REPO_ROOT/agent"
if [ ! -f "$SRC_DIR/activity_agent.py" ]; then
    echo "[FAIL] agent sources not found at $SRC_DIR" >&2
    exit 1
fi

echo "==> Copying agent sources to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
for f in activity_agent.py consent.py notifier.py strain.py usb_monitor.py wearable_ble.py requirements.txt; do
    if [ -f "$SRC_DIR/$f" ]; then cp -f "$SRC_DIR/$f" "$INSTALL_DIR/$f"; fi
done
echo "[OK] sources copied"

echo "==> Creating virtualenv (this can take a few minutes)"
if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
echo "[OK] dependencies installed"

echo "==> Writing $ENV_FILE"
mkdir -p "$(dirname "$ENV_FILE")"
cat > "$ENV_FILE" <<EOF
WAMSS_AGENT_SERVER_URL=$SERVER_URL
WAMSS_MACHINE_ID=$MACHINE_ID
WAMSS_EMPLOYEE_ID=$EMPLOYEE_ID
WAMSS_OFFICE_ID=$OFFICE_ID
WAMSS_LOG_INTERVAL=60
WAMSS_SYNC_INTERVAL=300
WAMSS_SPOOL_DIR=$INSTALL_DIR/spool
EOF
echo "[OK] env file written"

echo "==> Installing systemd user unit"
mkdir -p "$(dirname "$UNIT_FILE")"
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=WAMSS workstation activity agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$INSTALL_DIR/venv/bin/python activity_agent.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"
echo "[OK] service enabled and started"

echo
echo "================ Agent installed ================"
echo "  Install dir: $INSTALL_DIR"
echo "  Service:     $UNIT_NAME (systemd user, enabled at login)"
echo "  Server:      $SERVER_URL"
echo "================================================"
echo
echo "Next steps:"
echo "  1. First run asks for consent on the console (typed YES). Check status:"
echo "       systemctl --user status $UNIT_NAME"
echo "  2. Verify records are spooling: tail $INSTALL_DIR/spool/pending.jsonl"
echo "     and look for machine id '$MACHINE_ID' in the dashboard."
echo "  3. Note: on Wayland or headless machines the agent degrades gracefully"
echo "     (empty app/window fields); X11 gives full active-window detection."
echo "  4. To remove later: bash deploy/install-agent.sh --uninstall"
