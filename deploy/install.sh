#!/usr/bin/env bash
# One-command install of the edge server (Ubuntu 24.04 LTS; an NVIDIA GPU is optional).
#   sudo ./install.sh            # real site install
#   sudo ./install.sh --test     # rehearsal on any PC (also WSL2): demo videos stand in for the
#                                  cameras, pretend payments, demo staff logins, no firewall/cron
#   sudo ./install.sh --no-gpu   # ignore an NVIDIA GPU even if one is present
# Safe to re-run: existing .env, site.yaml, database and admin are kept.
set -euo pipefail
cd "$(dirname "$0")"
TEST=0 GPU=auto
for a in "$@"; do
  case "$a" in
    --test) TEST=1 ;;
    --no-gpu) GPU=0 ;;
    *) echo "unknown option $a"; exit 1 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)"; exit 1; }

log() { echo -e "\n\033[1;32m==> $*\033[0m"; }
warn() { echo -e "\033[1;33m!!  $*\033[0m"; }
WSL=0; grep -qi microsoft /proc/version 2>/dev/null && WSL=1
SYSTEMD=0; [[ -d /run/systemd/system ]] && SYSTEMD=1
if [[ $GPU == auto ]]; then
  GPU=0; command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1 && GPU=1
fi
[[ $TEST == 1 ]] && GPU=0

log "Base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg openssl unzip age rclone < /dev/null
[[ $TEST == 0 ]] && apt-get install -y chrony ufw

if ! docker compose version >/dev/null 2>&1; then
  log "Docker"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
if ! docker info >/dev/null 2>&1; then
  if [[ $SYSTEMD == 1 ]]; then systemctl enable --now docker; else service docker start || true; fi
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
  docker info >/dev/null 2>&1 || { echo "Docker is not running. On WSL2 start Docker Desktop (or 'sudo service docker start') and re-run."; exit 1; }
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

if [[ $TEST == 0 && $SYSTEMD == 1 ]]; then
  log "NTP: serve time to the site LAN"
  cp config/chrony-lan.conf /etc/chrony/conf.d/parking-lan.conf
  systemctl restart chrony
fi

log "Secrets and .env"
setenv() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
if [[ ! -f .env ]]; then
  cp .env.example .env
  for k in DB_PASSWORD PARK_SECRET_KEY PARK_ANPR_API_KEY PARK_DEVICE_API_KEY PARK_RELAY_API_KEY; do
    setenv "$k" "$(openssl rand -hex 24)"
  done
  setenv PARK_SITE_IP "$(hostname -I | awk '{print $1}')"
  if [[ $TEST == 1 ]]; then
    # rehearsal: everything on this PC's disk, pretend payments and SMS, small database memory
    setenv IMAGE_DIR /srv/parking/images
    setenv BACKUP_DIR /srv/parking/backup
    setenv PARK_GATEWAY mock
    setenv PARK_SMS_PROVIDER noop
    setenv PG_SHARED_BUFFERS 256MB
    setenv PG_CACHE_SIZE 1GB
    setenv ALLOW_LOCAL_BACKUP 1
    setenv PARK_TEST_INSTALL 1
  fi
  chmod 600 .env
  echo "Generated deploy/.env with random secrets."
fi
[[ $GPU == 1 ]] && setenv COMPOSE_FILE docker-compose.yml:docker-compose.gpu.yml
set -a; source .env; set +a
TEST_ENV=${PARK_TEST_INSTALL:-0}
if [[ $TEST == 0 && $TEST_ENV == 1 ]]; then
  echo "This deploy/.env was made for a --test rehearsal. For the real site: move deploy/.env away and re-run."; exit 1
fi
if [[ $TEST == 0 && ( -z "${PARK_RAZORPAY_KEY_ID:-}" || -z "${PARK_MSG91_AUTH_KEY:-}" ) ]]; then
  warn "Razorpay / MSG91 keys are empty in deploy/.env: UPI and SMS will not work until you fill them in and re-run."
fi
if [[ $WSL == 1 && "$PARK_SITE_IP" == 172.* ]]; then
  warn "WSL2 is using NAT networking ($PARK_SITE_IP): phones on your Wi-Fi cannot reach it."
  warn "Turn on mirrored networking (docs/INSTALL.md §1b), then: rm deploy/.env and re-run."
fi

log "Storage"
WORKER_WEB_DIR=${WORKER_WEB_DIR:-/srv/parking/worker-web}
mkdir -p "$PG_DATA_DIR" "$IMAGE_DIR" "$UPLOAD_DIR" "$ANPR_STATE_DIR" "$MODEL_DIR" "$BACKUP_DIR" "$WORKER_WEB_DIR" \
         "${CADDY_DATA_DIR:-/srv/parking/caddy}" "${CADDY_CERTS_DIR:-/srv/parking/certs}"
# backend and ANPR both run as uid 1001: ANPR writes plate images, the backend deletes old ones
chown -R 1001 "$IMAGE_DIR" "$UPLOAD_DIR" "$ANPR_STATE_DIR"

log "Phone app (browser build from the project's GitHub release)"
remote="$(git -C .. config --get remote.origin.url 2>/dev/null || true)"
repo="$(echo "$remote" | sed -E 's#^.*github.com[:/]##; s#\.git$##')"
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || repo="saumyahindocha/Car-parking-software"
if [[ ! -f "$WORKER_WEB_DIR/index.html" || "${UPDATE_APP:-0}" == 1 ]]; then
  tmp=$(mktemp -d)
  if curl -fsSL "https://github.com/$repo/releases/download/demo-latest/worker-web.zip" -o "$tmp/w.zip"; then
    rm -rf "${WORKER_WEB_DIR:?}"/* && unzip -q "$tmp/w.zip" -d "$WORKER_WEB_DIR"
    # the zip holds either the files or one top-level folder
    if [[ ! -f "$WORKER_WEB_DIR/index.html" ]]; then
      sub=$(dirname "$(find "$WORKER_WEB_DIR" -maxdepth 2 -name index.html | head -1)")
      [[ -n "$sub" && "$sub" != "." ]] && { mv "$sub"/* "$WORKER_WEB_DIR"/; rmdir "$sub"; }
    fi
    echo "phone app installed in $WORKER_WEB_DIR"
  else
    warn "could not download the phone app; https://<server>/app will be empty until you re-run with internet"
  fi
  rm -rf "$tmp"
fi

log "Build (first time: 10-20 minutes)"
docker compose build < /dev/null

if [[ ! -s config/site.yaml ]]; then
  if [[ $TEST == 1 ]]; then
    log "Test cameras: rendering demo videos for Gate 1 (entry) and Gate 2 (exit)"
    : > config/site.yaml   # the anpr container mounts this file: it must exist before any run
    docker compose run --rm --no-deps -T --entrypoint sh anpr < /dev/null -c '
      set -e
      python3 -m anpr_service synth --out /data/anpr/testcams --fps 15 --no-overview --compact --wrong-way --ext .avi >/dev/null
      python3 - <<PY
import cv2, numpy as np, yaml
c = yaml.safe_load(open("/data/anpr/testcams/site.synth.yaml"))
c["site_id"] = "rehearsal"
c.pop("image_root", None); c.pop("outbox_path", None); c.pop("emitter", None)
c["backend"] = {"url": "\${BACKEND_URL:-http://backend:8000}", "api_key": "\${ANPR_API_KEY:-}"}
c["recognizer"] = {"kind": "trained", "trained": {"finder_width": 640}}
for g in c["gates"]:
    for cam in g["cameras"]:
        cam["replay_loop"] = True
        # add ~65 s of empty road so each loop is longer than the 60 s same-plate dedupe window
        cap = cv2.VideoCapture(cam["replay_file"]); fps = cap.get(cv2.CAP_PROP_FPS) or 10; frames = []
        while True:
            ok, f = cap.read()
            if not ok: break
            frames.append(f)
        cap.release()
        road = np.median(np.stack(frames[::4]), axis=0).astype(np.uint8)
        h, w = road.shape[:2]
        out = cv2.VideoWriter(cam["replay_file"], cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
        for f in frames + [road] * int(65 * fps):
            out.write(f)
        out.release()
yaml.safe_dump(c, open("/data/anpr/testcams/site.yaml", "w"), sort_keys=False)
PY'
    { echo "# REHEARSAL cameras: looping demo videos (install.sh --test). Real site: see site.yaml.example"
      cat "$ANPR_STATE_DIR/testcams/site.yaml"; } > config/site.yaml
  else
    cp config/site.yaml.example config/site.yaml
    warn "Edit deploy/config/site.yaml: camera RTSP addresses, ROI and capture lines (docs/CAMERA_SETUP.md)."
  fi
fi

if [[ $TEST == 0 && $WSL == 0 ]]; then
  log "Firewall: SSH, HTTPS gateway (LAN), NTP"
  ufw allow OpenSSH
  ufw allow from 192.168.0.0/16 to any port 80 proto tcp
  ufw allow from 192.168.0.0/16 to any port 443 proto tcp
  ufw allow from 192.168.0.0/16 to any port 123 proto udp
  ufw --force enable
fi

log "Start"
docker compose up -d < /dev/null
ok=0
for _ in $(seq 1 90); do curl -fs http://localhost:8000/api/health >/dev/null && { ok=1; break; }; sleep 2; done
[[ $ok == 1 ]] || { echo "backend did not come up; see: docker compose logs backend"; exit 1; }
curl -fs http://localhost:8000/api/health && echo

log "First admin user"
if [[ -z "$(docker compose exec -T db psql < /dev/null -U parking -d parking -tAc "select 1 from users where role='ADMIN' limit 1")" ]]; then
  read -rp "Admin username: " AU; read -rsp "Admin password: " AP; echo
  docker compose exec -T -e AU="$AU" -e AP="$AP" backend python < /dev/null -c "
import os
from app.db import session_scope
from app.models import User
from app.security import hash_secret
with session_scope() as db:
    db.add(User(username=os.environ['AU'], name='Administrator', role='ADMIN', password_hash=hash_secret(os.environ['AP'])))
print('admin created')"
else
  echo "admin already exists"
fi

if [[ $TEST == 1 ]]; then
  log "Rehearsal logins (demo staff)"
  docker compose exec -T backend python < /dev/null -c "
from app.db import session_scope
from app.seed import seed_users
with session_scope() as db:
    seed_users(db)
print('demo staff ready (existing usernames kept)')"
fi
if [[ $TEST == 0 ]]; then
  log "Nightly backup (02:30) with weekly restore test (Sunday 04:00)"
  cat > /etc/cron.d/parking-backup <<CRON
30 2 * * * root $(pwd)/backup.sh >> /var/log/parking-backup.log 2>&1
0 4 * * 0 root $(pwd)/verify_backup.sh >> /var/log/parking-backup.log 2>&1
CRON
fi
if [[ $SYSTEMD == 1 ]]; then
  cp systemd/parking.service /etc/systemd/system/parking.service
  sed -i "s|__DIR__|$(pwd)|g" /etc/systemd/system/parking.service
  systemctl daemon-reload && systemctl enable parking.service
fi

log "Done."
echo "  Dashboard:        https://$PARK_SITE_IP"
echo "  Phone app:        https://$PARK_SITE_IP/app"
echo "  Site certificate: http://$PARK_SITE_IP/site-ca.crt  (install once on each phone / PC, see docs/INSTALL.md §2a)"
if [[ $TEST == 1 ]]; then
  echo "  Rehearsal logins: admin (the one you just made) · workers w1..w4 PIN 1111..4444 · sup1 PIN 1234"
  echo "  Cameras:          demo videos loop at both gates; entries/exits appear within a minute"
  echo "  Check it:         sudo ./check.sh"
fi
