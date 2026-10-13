#!/usr/bin/env bash
# Install the exit alert unit on Raspberry Pi OS Bookworm (64-bit, Lite recommended).
# Usage: sudo ./install.sh [--edge-url https://192.168.10.10] [--key DEVICE_KEY] [--gate G2] [--ntp 192.168.10.10]
#                         [--ca-url http://192.168.10.10/site-ca.crt]   (trust the site's HTTPS certificate)
set -euo pipefail

EDGE_URL="" KEY="" GATE="" NTP="" CA_URL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --edge-url) EDGE_URL="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    --gate) GATE="$2"; shift 2 ;;
    --ntp) NTP="$2"; shift 2 ;;
    --ca-url) CA_URL="$2"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/alert-unit

echo "==> packages"
apt-get update -y
apt-get install -y --no-install-recommends python3 python3-venv python3-pygame python3-gpiozero python3-lgpio \
  python3-yaml python3-websocket fonts-dejavu-core

echo "==> user"
id alertunit >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin alertunit
for g in gpio video render input; do getent group "$g" >/dev/null && usermod -aG "$g" alertunit; done

echo "==> application"
mkdir -p "$DEST"
rm -rf "$DEST/exit_alert"
cp -r "$SRC/exit_alert" "$DEST/"
[[ -d "$DEST/venv" ]] || python3 -m venv --system-site-packages "$DEST/venv"
# the apt packages above satisfy everything; pip only fills gaps (no network needed if they are present)
"$DEST/venv/bin/python" -c "import pygame, websocket, yaml" 2>/dev/null || \
  "$DEST/venv/bin/pip" install --no-cache-dir -r "$SRC/requirements.txt"
chown -R root:root "$DEST"

echo "==> configuration"
mkdir -p /etc/alert-unit
if [[ ! -f /etc/alert-unit/config.yaml ]]; then
  cp "$SRC/config.example.yaml" /etc/alert-unit/config.yaml
fi
[[ -n "$EDGE_URL" ]] && sed -i "s|^edge_url:.*|edge_url: $EDGE_URL|" /etc/alert-unit/config.yaml
[[ -n "$KEY" ]] && sed -i "s|^device_key:.*|device_key: $KEY|" /etc/alert-unit/config.yaml
if [[ -n "$GATE" ]]; then
  sed -i "s|^gate_id:.*|gate_id: $GATE|; s|^device_id:.*|device_id: AU-$GATE|" /etc/alert-unit/config.yaml
fi
if [[ -n "$CA_URL" ]]; then
  echo "==> site certificate"
  curl -fsSL "$CA_URL" -o /etc/alert-unit/site-ca.crt
  grep -q "BEGIN CERTIFICATE" /etc/alert-unit/site-ca.crt || { echo "not a certificate: $CA_URL"; exit 1; }
  if grep -q "^ca_file:" /etc/alert-unit/config.yaml; then
    sed -i "s|^ca_file:.*|ca_file: /etc/alert-unit/site-ca.crt|" /etc/alert-unit/config.yaml
  else
    echo "ca_file: /etc/alert-unit/site-ca.crt" >> /etc/alert-unit/config.yaml
  fi
fi
chown root:alertunit /etc/alert-unit/config.yaml
chmod 640 /etc/alert-unit/config.yaml

echo "==> kiosk tweaks"
# never blank the console / HDMI output
CMDLINE=/boot/firmware/cmdline.txt
if [[ -f $CMDLINE ]] && ! grep -q "consoleblank=0" $CMDLINE; then
  sed -i 's/$/ consoleblank=0 vt.global_cursor_default=0/' $CMDLINE
fi
# time from the edge server (NTP)
if [[ -n "$NTP" ]]; then
  mkdir -p /etc/systemd/timesyncd.conf.d
  printf '[Time]\nNTP=%s\n' "$NTP" > /etc/systemd/timesyncd.conf.d/edge.conf
  systemctl restart systemd-timesyncd || true
fi

echo "==> service"
install -m 644 "$SRC/deploy/alert-unit.service" /etc/systemd/system/alert-unit.service
systemctl daemon-reload
systemctl enable alert-unit.service
systemctl restart alert-unit.service
echo "done. logs: journalctl -u alert-unit -f"
