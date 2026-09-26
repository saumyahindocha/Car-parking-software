#!/usr/bin/env bash
# Nightly PostgreSQL backup: custom-format dump to the external disk, retention, and an
# age-encrypted copy uploaded with rclone (if configured). Safe to run by hand.
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] && { set -a; source .env; set +a; }
: "${BACKUP_DIR:?BACKUP_DIR not set}"
PGHOST=${PGHOST:-127.0.0.1} PGPORT=${PGPORT:-5432} PGUSER=${PGUSER:-parking} PGDATABASE=${PGDATABASE:-parking}
export PGHOST PGPORT PGUSER PGDATABASE PGPASSWORD=${PGPASSWORD:-$DB_PASSWORD}
mkdir -p "$BACKUP_DIR"
if ! mountpoint -q "$(df --output=target "$BACKUP_DIR" | tail -1)" && [[ "${ALLOW_LOCAL_BACKUP:-0}" != 1 ]]; then
  echo "WARNING: $BACKUP_DIR is not on a mounted external disk" >&2
fi
stamp=$(date +%Y%m%d-%H%M%S)
out="$BACKUP_DIR/parking-$stamp.dump"
pg_dump -Fc -Z 6 --no-owner -f "$out.tmp"
mv "$out.tmp" "$out"
sha256sum "$out" > "$out.sha256"
# the site certificate authority: if it is lost, every phone and alert unit must re-trust a new one
if [[ -d "${CADDY_DATA_DIR:-/srv/parking/caddy}/caddy/pki" ]]; then
  tar -czf "$BACKUP_DIR/site-ca-$stamp.tgz" -C "${CADDY_DATA_DIR:-/srv/parking/caddy}/caddy" pki
fi
echo "$(date -Is) backup ok: $out ($(du -h "$out" | cut -f1))"
if [[ -n "${BACKUP_AGE_RECIPIENT:-}" && -n "${BACKUP_RCLONE_REMOTE:-}" ]]; then
  age -r "$BACKUP_AGE_RECIPIENT" -o "$out.age" "$out"
  rclone copy "$out.age" "$BACKUP_RCLONE_REMOTE/" && rm -f "$out.age"
  echo "$(date -Is) encrypted cloud copy uploaded to $BACKUP_RCLONE_REMOTE"
fi
find "$BACKUP_DIR" \( -name 'parking-*.dump*' -o -name 'site-ca-*.tgz' \) -mtime +"${BACKUP_KEEP_DAYS:-30}" -delete
