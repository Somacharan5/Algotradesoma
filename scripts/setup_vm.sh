#!/usr/bin/env bash
# ============================================================
# Oracle Cloud VM — one-time bootstrap script
# Run as: bash scripts/setup_vm.sh
# Tested on: Ubuntu 22.04 (ARM or AMD free tier)
# ============================================================
set -euo pipefail

REPO_URL="https://github.com/Somacharan5/Algotradesoma.git"
APP_DIR="/home/ubuntu/trading-agent"
VENV="$APP_DIR/.venv"
SERVICE_NAME="trading-agent"

echo "=== [1/7] System update ==="
sudo apt-get update -qq
sudo apt-get install -y -qq python3.12 python3.12-venv python3.12-dev \
    git curl build-essential libpq-dev

echo "=== [2/7] Clone repo ==="
if [ -d "$APP_DIR/.git" ]; then
    echo "Repo already cloned — pulling latest."
    cd "$APP_DIR" && git pull
else
    git clone "$REPO_URL" "$APP_DIR"
fi

echo "=== [3/7] Python virtual environment ==="
python3.12 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "=== [4/7] .env file ==="
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ""
    echo "  *** ACTION REQUIRED ***"
    echo "  Fill in your credentials in: $APP_DIR/.env"
    echo "  Then re-run this script OR manually run:"
    echo "    sudo systemctl start $SERVICE_NAME"
    echo ""
fi

echo "=== [5/7] Apply Supabase schema ==="
"$VENV/bin/python" -m scripts.apply_schema || echo "Schema already applied (or SUPABASE_DB_URL not set — apply manually)."

echo "=== [6/7] Systemd service ==="
sudo cp "$APP_DIR/deploy/trading-agent.service" "/etc/systemd/system/$SERVICE_NAME.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo "=== [7/7] Log rotation ==="
sudo cp "$APP_DIR/deploy/logrotate.conf" "/etc/logrotate.d/$SERVICE_NAME"
mkdir -p "$APP_DIR/logs"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Commands:"
echo "  sudo systemctl start   $SERVICE_NAME  # start agent"
echo "  sudo systemctl stop    $SERVICE_NAME  # stop agent"
echo "  sudo systemctl status  $SERVICE_NAME  # check status"
echo "  journalctl -u $SERVICE_NAME -f        # live logs"
echo "  sudo systemctl restart $SERVICE_NAME  # restart"
