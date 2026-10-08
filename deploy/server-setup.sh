#!/usr/bin/env bash
# Runs ON the server (Debian / Ubuntu). Idempotent: safe to run on every deployment.
#   bash server-setup.sh <domain>
set -euo pipefail
DOMAIN="${1:?usage: server-setup.sh <domain>}"
APP_DIR="$HOME/fastimpact"
cd "$APP_DIR"

SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

# Big data disk (Infomaniak VPS: extra volume mounted on /mnt/data) for Docker and the app data
BIG=""
if mountpoint -q /mnt/data 2>/dev/null; then BIG=/mnt/data; fi

# 1) Docker Engine + compose plugin from Docker's official apt repository (first run only)
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "==> Installing Docker"
  if [ -n "$BIG" ] && [ ! -f /etc/docker/daemon.json ]; then
    $SUDO mkdir -p /etc/docker "$BIG/docker"
    echo "{\"data-root\": \"$BIG/docker\"}" | $SUDO tee /etc/docker/daemon.json >/dev/null
  fi
  . /etc/os-release
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq ca-certificates curl
  $SUDO install -m 0755 -d /etc/apt/keyrings
  $SUDO curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  $SUDO chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  $SUDO usermod -aG docker "$USER" || true
fi
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="$SUDO docker"

# 2) Configuration (created once, never overwritten)
echo "FIP_DOMAIN=${DOMAIN}" > .env
if [ -n "$BIG" ]; then
  $SUDO mkdir -p "$BIG/fastimpact"
  $SUDO chown 1000:1000 "$BIG/fastimpact"   # the container runs as uid 1000
  echo "FIP_DATA_PATH=$BIG/fastimpact" >> .env
fi
if [ ! -f .env.server ]; then
  echo "==> Creating .env.server with a random app password"
  umask 077
  {
    echo "FIP_APP_PASSWORD=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20)"
    echo "FIP_SESSION_SECRET=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  } > .env.server
fi
chmod 600 .env.server

# 3) Build and (re)start
echo "==> Building and starting (first build downloads ~1.5 GB of Python packages)"
$DOCKER compose up -d --build --remove-orphans
$DOCKER image prune -f >/dev/null

echo
echo "==> Done. Open https://${DOMAIN}"
echo "    App password: run  grep FIP_APP_PASSWORD ~/fastimpact/.env.server  on the server."
