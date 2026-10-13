#!/usr/bin/env bash
# Site health check: run after install.sh, after any change, and on the site PC before go-live.
#   sudo ./check.sh            # everything, including a real backup + restore test
#   sudo ./check.sh --quick    # skip the backup/restore test
# Prints PASS / WARN / FAIL per item; exits non-zero if anything FAILED.
set -uo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo "no deploy/.env: run install.sh first"; exit 1; }
set -a; source .env; set +a
QUICK=0; [[ "${1:-}" == "--quick" ]] && QUICK=1
fails=0 warns=0
pass() { echo -e "  \033[32mPASS\033[0m  $*"; }
warn() { echo -e "  \033[33mWARN\033[0m  $*"; warns=$((warns + 1)); }
fail() { echo -e "  \033[31mFAIL\033[0m  $*"; fails=$((fails + 1)); }
export NO_PROXY='*' no_proxy='*'   # everything checked here is local
sql() { docker compose exec -T db psql -U parking -d parking -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }

echo "Services"
running=$(docker compose ps --status running --services 2>/dev/null | sort | tr '\n' ' ')
for s in db backend gateway anpr; do
  [[ " $running " == *" $s "* ]] && pass "$s running" || fail "$s not running (docker compose logs $s)"
done
curl -fs http://localhost:8000/api/health >/dev/null && pass "backend answers" || fail "backend health endpoint"

echo "HTTPS for phones and PCs (https://$PARK_SITE_IP)"
ca="${CADDY_DATA_DIR:-/srv/parking/caddy}/caddy/pki/authorities/local/root.crt"
curl -fs "http://$PARK_SITE_IP/site-ca.crt" -o /dev/null && pass "site certificate downloadable at http://$PARK_SITE_IP/site-ca.crt" \
  || fail "http://$PARK_SITE_IP/site-ca.crt not reachable"
if [[ -f "$ca" ]] && curl -fs --cacert "$ca" "https://$PARK_SITE_IP/api/health" -o /dev/null; then
  pass "HTTPS works with the site certificate"
else
  fail "HTTPS on https://$PARK_SITE_IP (is PARK_SITE_IP this PC's LAN address? hostname -I says: $(hostname -I | awk '{print $1}'))"
fi
code=$(curl -s -o /dev/null -w '%{http_code}' --cacert "$ca" "https://$PARK_SITE_IP/app/" 2>/dev/null)
[[ "$code" == 200 ]] && pass "phone app served at https://$PARK_SITE_IP/app" || fail "phone app missing (HTTP $code): re-run install.sh with internet"
code=$(curl -s -o /dev/null -w '%{http_code}' --cacert "$ca" "https://$PARK_SITE_IP/" 2>/dev/null)
[[ "$code" == 200 ]] && pass "dashboard served at https://$PARK_SITE_IP" || fail "dashboard (HTTP $code)"

echo "Cameras and number-plate reading"
cams=$(sql "select count(*) from devices where kind='CAMERA' and last_seen > timezone('utc', now()) - interval '60 seconds'")
bad=$(sql "select string_agg(id, ',') from devices where kind='CAMERA' and (last_seen < timezone('utc', now()) - interval '60 seconds' or (metrics->>'stream_ok')::boolean is false)")
[[ "${cams:-0}" -gt 0 ]] && pass "$cams camera(s) reporting" || fail "no camera heartbeat in the last minute (docker compose logs anpr)"
[[ -z "$bad" ]] || warn "cameras offline or stalled: $bad"
fps=$(sql "select string_agg(id || ' ' || round((metrics->>'fps')::numeric, 1) || ' fps', ', ') from devices where kind='CAMERA' and metrics ? 'fps'")
[[ -n "$fps" ]] && echo "        $fps"
n=$(sql "select count(*) from anpr_events where received_at > timezone('utc', now()) - interval '15 minutes'")
r=$(sql "select count(*) from anpr_events where received_at > timezone('utc', now()) - interval '15 minutes' and status <> 'UNREAD'")
if [[ "${n:-0}" -gt 0 ]]; then
  pass "$n vehicle events in the last 15 minutes, $r with a plate read"
else
  warn "no vehicle events in the last 15 minutes (fine at night / before cameras are aimed)"
fi
lat=$(sql "select percentile_cont(0.95) within group (order by latency_ms) from anpr_events where received_at > timezone('utc', now()) - interval '1 hour' and latency_ms is not null")
[[ -n "$lat" ]] && { [[ ${lat%.*} -le 3000 ]] && pass "95% of events reach the server within ${lat%.*} ms" || warn "slow events: 95th percentile ${lat%.*} ms"; }
kind=$(grep -E '^\s*kind:' config/site.yaml | head -1 | sed -E 's/.*kind:\s*//')
echo "        plate engine: $kind"

echo "Disk and time"
for d in "$IMAGE_DIR" "$PG_DATA_DIR" "$BACKUP_DIR"; do
  use=$(df --output=pcent "$d" 2>/dev/null | tail -1 | tr -dc 0-9)
  [[ -z "$use" ]] && { fail "$d missing"; continue; }
  [[ $use -lt 85 ]] && pass "$d ${use}% used" || warn "$d ${use}% used"
done
if command -v chronyc >/dev/null; then
  off=$(chronyc tracking 2>/dev/null | awk '/System time/ {print $4}')
  [[ -n "$off" ]] && pass "clock synchronised (offset ${off}s)" || warn "chrony not tracking a time source"
else
  warn "chrony not installed (normal for a --test rehearsal)"
fi

if [[ $QUICK == 0 ]]; then
  echo "Backup and restore (a real backup, then restored into a scratch database)"
  if ALLOW_LOCAL_BACKUP=${ALLOW_LOCAL_BACKUP:-0} ./backup.sh >/tmp/parking-check-backup.log 2>&1; then
    pass "backup: $(tail -1 /tmp/parking-check-backup.log | sed 's/^[^ ]* //')"
    if ./verify_backup.sh >/tmp/parking-check-restore.log 2>&1; then
      pass "restore test: $(grep 'restore test of' /tmp/parking-check-restore.log | sed 's/^.*: schema/schema/')"
    else
      fail "restore test (see /tmp/parking-check-restore.log)"
    fi
  else
    fail "backup (see /tmp/parking-check-backup.log)"
  fi
fi

echo
if [[ $fails -gt 0 ]]; then echo "RESULT: $fails FAILED, $warns warnings"; exit 1; fi
echo "RESULT: all checks passed ($warns warnings)"
