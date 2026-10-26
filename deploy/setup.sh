#!/usr/bin/env bash
# Idempotent deployment for a fresh Ubuntu 24.04 VPS. Run as root from the extracted package:
#   sudo ./deploy/setup.sh
# Safe to re-run: it also serves as the "update code" step. Contains NO secrets.
set -euo pipefail

APP_USER="maplebot"
APP_DIR="/opt/maplestory-bot"
ENV_FILE="/etc/maplestory-bot.env"
SERVICE="maplestory-bot"
UNIT_FILE="/etc/systemd/system/${SERVICE}.service"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\n==> %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo ./deploy/setup.sh)" >&2
  exit 1
fi
if [ ! -f "${SRC_DIR}/bot.py" ] || [ ! -f "${SRC_DIR}/requirements.txt" ]; then
  echo "Cannot find bot.py/requirements.txt in ${SRC_DIR}; run this from the extracted package." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

log "Installing system packages"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip rsync ufw unattended-upgrades ca-certificates

log "Creating service user ${APP_USER}"
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/home/${APP_USER}" --shell /usr/sbin/nologin "${APP_USER}"
fi

log "Copying code to ${APP_DIR}"
mkdir -p "${APP_DIR}"
# Code is root-owned (the bot cannot modify its own source); only cache/ is writable by the bot.
# cache/, .venv and .env on the server are preserved across updates.
rsync -a --delete \
  --exclude '.venv/' --exclude '.env' --exclude 'cache/' --exclude '.git/' \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude 'bot.log' --exclude 'bot.pid' \
  --exclude 'deploy/' \
  "${SRC_DIR}/" "${APP_DIR}/"
mkdir -p "${APP_DIR}/cache"
chown -R root:root "${APP_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/cache"
chmod -R go-w "${APP_DIR}"
chmod 755 "${APP_DIR}"

log "Creating virtualenv and installing requirements"
if [ ! -x "${APP_DIR}/.venv/bin/python" ]; then
  python3 -m venv "${APP_DIR}/.venv"
fi
"${APP_DIR}/.venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/.venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
chown -R root:root "${APP_DIR}/.venv"

log "Environment file ${ENV_FILE}"
if [ ! -e "${ENV_FILE}" ]; then
  umask 077
  cat > "${ENV_FILE}" <<'ENVEOF'
# Secrets for maplestory-bot. Read by systemd (EnvironmentFile). No quotes needed.
DISCORD_BOT_TOKEN=
XAI_API_KEY=
# Optional overrides:
# XAI_MODEL=
# XAI_BASE_URL=
# ASK_COOLDOWN_SECONDS=15
# GUIDES_REFRESH_HOURS=6
# DISCORD_GUILD_ID=
ENVEOF
  echo "Created ${ENV_FILE} with empty placeholders - fill it in (see README-DEPLOY.md)."
fi
chown root:root "${ENV_FILE}"
chmod 600 "${ENV_FILE}"

log "Installing systemd unit ${UNIT_FILE}"
cat > "${UNIT_FILE}" <<UNITEOF
[Unit]
Description=MapleStory Classic Discord bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python -u bot.py
Restart=always
RestartSec=5

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${APP_DIR}/cache
ProtectKernelTunables=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
UNITEOF
chmod 644 "${UNIT_FILE}"
systemctl daemon-reload
systemctl enable "${SERVICE}.service"

log "Firewall (ufw: deny incoming, allow SSH only)"
SSH_PORT="$( (sshd -T 2>/dev/null || true) | awk '$1=="port"{print $2; exit}')"
SSH_PORT="${SSH_PORT:-22}"
ufw default deny incoming
ufw default allow outgoing
ufw allow "${SSH_PORT}/tcp" comment 'ssh'
ufw --force enable

log "Unattended security upgrades"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'AUEOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
AUEOF
systemctl enable --now unattended-upgrades.service >/dev/null 2>&1 || true

log "Starting service"
# Don't crash-loop on an unfilled placeholder: only (re)start when a token is present.
# (Checks for a non-empty value without printing it.)
if grep -Eq '^DISCORD_BOT_TOKEN=.+' "${ENV_FILE}"; then
  systemctl restart "${SERVICE}.service"
  sleep 3
  systemctl --no-pager --lines=5 status "${SERVICE}.service" || true
else
  echo "DISCORD_BOT_TOKEN is empty in ${ENV_FILE}; service enabled but NOT started."
  echo "Edit the file, then: systemctl start ${SERVICE}"
fi

log "Done."
