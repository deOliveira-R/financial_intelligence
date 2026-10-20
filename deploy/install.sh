#!/usr/bin/env bash
# Server setup and deploys on Ubuntu 24.04 (run with sudo): installs Docker, clones or pulls
# the repo, builds the image, migrates, and (re)starts the containers (compose.yaml).
# See deploy/README.md for the steps around it.
set -euo pipefail

REPO="${REPO:-https://github.com/deOliveira-R/financial_intelligence.git}"
APP_USER=finintel
APP_DIR="/home/$APP_USER/financial_intelligence"

apt-get update
apt-get install -y git docker.io docker-compose-v2
systemctl enable --now docker

id "$APP_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$APP_USER"

if [ -d "$APP_DIR/.git" ]; then
  sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only
else
  sudo -u "$APP_USER" git clone "$REPO" "$APP_DIR"
fi
sudo -u "$APP_USER" mkdir -p "$APP_DIR/data/raw"

if [ ! -f "$APP_DIR/.env" ]; then
  echo
  echo "Cloned. Next (see deploy/README.md): create $APP_DIR/.env, then re-run this script."
  exit 0
fi

cd "$APP_DIR"
export CODE_VERSION="$(sudo -u "$APP_USER" git -C "$APP_DIR" rev-parse --short HEAD)"
docker compose build
# Migrate before the API restarts onto new code.
docker compose up -d --wait postgres
docker compose run --rm -T api fin-intel migrate
docker compose up -d --remove-orphans
docker image prune -f >/dev/null
docker compose ps
