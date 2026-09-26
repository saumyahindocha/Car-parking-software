#!/usr/bin/env bash
# One-command install of the edge server (Ubuntu 24.04 LTS, NVIDIA GPU).
#   sudo ./install.sh            # full install
#   sudo ./install.sh --no-gpu   # skip NVIDIA container toolkit (demo / test box)
set -euo pipefail
cd "$(dirname "$0")"
GPU=1
[[ "${1:-}" == "--no-gpu" ]] && GPU=0
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)"; exit 1; }

log() { echo -e "\n\033[1;32m==> $*\033[0m"; }

log "Base packages, Docker, chrony (NTP server for cameras / phones / Pis), PostgreSQL client"
apt-get update
apt-get install -y ca-certificates curl gnupg chrony postgresql-client-16 age rclone ufw
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
if [[ $GPU == 1 ]] && ! command -v nvidia-ctk >/dev/null; then
  log "NVIDIA container toolkit"
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update && apt-get install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker && systemctl restart docker
fi

log "NTP: serve time to the site LAN"
cp config/chrony-lan.conf /etc/chrony/conf.d/parking-lan.conf
systemctl restart chrony

log "Secrets and .env"
if [[ ! -f .env ]]; then
  cp .env.example .env
  for k in DB_PASSWORD PARK_SECRET_KEY PARK_ANPR_API_KEY PARK_DEVICE_API_KEY PARK_RELAY_API_KEY; do
    sed -i "s|^$k=.*|$k=$(openssl rand -hex 24)|" .env
  done
  chmod 600 .env
  echo "Generated deploy/.env — fill in gateway/SMS credentials, relay URL and storage paths, then re-run."
fi
set -a; source .env; set +a

log "Storage"
mkdir -p "$PG_DATA_DIR" "$IMAGE_DIR" "$UPLOAD_DIR" "$ANPR_STATE_DIR" "$MODEL_DIR" "$BACKUP_DIR"
chown -R 1001 "$IMAGE_DIR" "$UPLOAD_DIR"
chown -R 10001 "$ANPR_STATE_DIR"
[[ -f config/site.yaml ]] || cp config/site.yaml.example config/site.yaml

log "Firewall: SSH, API (LAN), NTP"
ufw allow OpenSSH
ufw allow from 192.168.0.0/16 to any port 8000 proto tcp
ufw allow from 192.168.0.0/16 to any port 123 proto udp
ufw --force enable

log "Build and start"
docker compose build
docker compose up -d
for i in $(seq 1 60); do curl -fs http://localhost:8000/api/health >/dev/null && break; sleep 2; done
curl -fs http://localhost:8000/api/health && echo

log "First admin user"
if [[ -z "$(docker compose exec -T db psql -U parking -tAc "select 1 from users where role='ADMIN' limit 1")" ]]; then
  read -rp "Admin username: " AU; read -rsp "Admin password: " AP; echo
  docker compose exec -T backend python - <<PY
from app.db import session_scope
from app.models import User
from app.security import hash_secret
with session_scope() as db:
    db.add(User(username="$AU", name="Administrator", role="ADMIN", password_hash=hash_secret("$AP")))
print("admin created")
PY
fi

log "Nightly backup (02:30) with weekly restore test (Sunday 04:00)"
cat > /etc/cron.d/parking-backup <<CRON
30 2 * * * root $(pwd)/backup.sh >> /var/log/parking-backup.log 2>&1
0 4 * * 0 root $(pwd)/verify_backup.sh >> /var/log/parking-backup.log 2>&1
CRON
cp systemd/parking.service /etc/systemd/system/parking.service
sed -i "s|__DIR__|$(pwd)|g" /etc/systemd/system/parking.service
systemctl daemon-reload && systemctl enable parking.service

log "Done. Dashboard: http://$(hostname -I | awk '{print $1}'):8000"
