#!/usr/bin/env bash
# One-time server setup on Ubuntu 24.04 (run with sudo). Re-running it updates the code
# and the systemd units. See deploy/README.md for the steps around it.
set -euo pipefail

REPO="${REPO:-https://github.com/deOliveira-R/financial_intelligence.git}"
APP_USER=finintel
APP_DIR="/home/$APP_USER/financial_intelligence"

apt-get update
apt-get install -y git sqlite3 rclone

id "$APP_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$APP_USER"

# uv for the app user; it installs the pinned Python itself (see .python-version).
if [ ! -x "/home/$APP_USER/.local/bin/uv" ]; then
  sudo -u "$APP_USER" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

if [ -d "$APP_DIR/.git" ]; then
  sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only
else
  sudo -u "$APP_USER" git clone "$REPO" "$APP_DIR"
fi
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && ~/.local/bin/uv sync --frozen --no-dev"

install -m 644 "$APP_DIR"/deploy/systemd/*.service "$APP_DIR"/deploy/systemd/*.timer \
  /etc/systemd/system/
systemctl daemon-reload

# Migrate before the API restarts onto new code (needs .env, so skipped on first install).
if [ -f "$APP_DIR/.env" ]; then
  sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/fin-intel migrate"
fi
if systemctl is-active --quiet fin-intel-api; then
  systemctl restart fin-intel-api
fi

echo
echo "Installed. Next (see deploy/README.md): create $APP_DIR/.env, copy data/, then:"
echo "  sudo systemctl enable --now fin-intel-api fin-intel-daily.timer fin-intel-weekly.timer fin-intel-backup.timer"
