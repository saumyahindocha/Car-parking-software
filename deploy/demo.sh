#!/usr/bin/env bash
# Laptop demo: backend (SQLite, demo mode, mock UPI gateway) + dashboard + synthetic ANPR traffic.
#   ./deploy/demo.sh                 # everything; Ctrl-C stops it all
#   ./deploy/demo.sh --no-anpr       # backend + dashboard only (inject events with POST /api/demo/event)
#   ./deploy/demo.sh --with-alert    # also open the Gate 2 exit alert unit window (needs a display)
#   ./deploy/demo.sh --with-relay    # also run the customer relay on :8080 and sync to it
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${DEMO_DIR:-$ROOT/.demo}"
PORT="${PORT:-8000}"
ANPR=1 ALERT=0 RELAY=0
for a in "$@"; do
  case $a in --no-anpr) ANPR=0 ;; --with-alert) ALERT=1 ;; --with-relay) RELAY=1 ;; esac
done
mkdir -p "$WORK/images" "$WORK/uploads"
pids=()
cleanup() { echo; echo "stopping demo"; for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null; }
trap cleanup EXIT INT TERM

echo "==> Python dependencies"
python3 -m pip install -q -r "$ROOT/backend/requirements.txt" -r "$ROOT/anpr/requirements.txt" 2>/dev/null || true

if command -v npm >/dev/null && [[ ! -f "$ROOT/dashboard/dist/index.html" ]]; then
  echo "==> Building dashboard"
  (cd "$ROOT/dashboard" && npm ci --no-audit --no-fund && npm run build)
fi

export PARK_DATABASE_URL="sqlite:///$WORK/demo.db" PARK_DEMO_MODE=true PARK_GATEWAY=mock \
       PARK_IMAGE_ROOT="$WORK/images" PARK_UPLOAD_ROOT="$WORK/uploads" PARK_DASHBOARD_DIST="$ROOT/dashboard/dist" \
       PARK_PUBLIC_RECEIPT_BASE="http://localhost:$PORT/r"
if [[ $RELAY == 1 ]]; then
  export PARK_RELAY_URL="http://localhost:8080" PARK_RELAY_API_KEY="demo-relay-key" PARK_PUBLIC_RECEIPT_BASE="http://localhost:8080/r"
fi

echo "==> Backend on http://localhost:$PORT"
(cd "$ROOT/backend" && python3 -m app.seed --create-all --users >/dev/null)
(cd "$ROOT/backend" && exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --log-level warning) &
pids+=($!)
for i in $(seq 1 30); do curl -fs "http://localhost:$PORT/api/health" >/dev/null && break; sleep 1; done

if [[ $RELAY == 1 ]]; then
  echo "==> Customer relay on http://localhost:8080"
  (cd "$ROOT/customer_web" && RELAY_DATABASE_URL="sqlite:///$WORK/relay.db" RELAY_RELAY_API_KEY="demo-relay-key" \
      RELAY_SECRET_KEY="demo-secret" RELAY_DEMO_MODE=true RELAY_COOKIE_SECURE=false RELAY_PUBLIC_BASE_URL="http://localhost:8080" \
      RELAY_GATEWAY=mock RELAY_SMS_PROVIDER=noop exec python3 -m uvicorn relay.main:app --port 8080 --log-level warning) &
  pids+=($!)
fi

if [[ $ALERT == 1 ]]; then
  echo "==> Exit alert unit window (Gate 2)"
  (cd "$ROOT/alert_unit" && exec python3 -m exit_alert --windowed --mock-gpio --edge-url "http://localhost:$PORT" \
      --device-key dev-device-key --gate G2) &
  pids+=($!)
fi

if [[ $ANPR == 1 ]]; then
  echo "==> Synthetic ANPR traffic: entries at Gate 1, exits at Gate 2 (new plates every round)"
  (
    cd "$ROOT/anpr"
    round=1
    while true; do
      out="$WORK/synth-$round"
      python3 -m anpr_service synth --out "$out" --seed "$round" >/dev/null
      BACKEND_URL="http://localhost:$PORT" ANPR_API_KEY=dev-anpr-key IMAGE_ROOT="$WORK/images" \
        ANPR_OUTBOX="$WORK/outbox.sqlite" python3 -m anpr_service replay --config "$out/site.synth.yaml" --realtime \
        >>"$WORK/anpr.log" 2>&1
      rm -rf "$out"
      round=$((round + 1))
    done
  ) &
  pids+=($!)
fi

cat <<INFO

  Dashboard   http://localhost:$PORT            admin / admin123   ·   sup1 / super123 (PIN 1234)
  Worker API  demo workers w1..w4 (PIN 1111..4444), guard1 (PIN 5555)
  API docs    http://localhost:$PORT/docs
  Demo calls  POST /api/demo/event {"gate_id":"G1","direction":"IN","plate":"MH43AB1234"}
              POST /api/demo/pay/<payment_id>        (customer completes UPI on the mock gateway)
              POST /api/demo/gateway {"online":false} (simulate an internet outage)
  Logs        $WORK/anpr.log
  Ctrl-C to stop.
INFO
wait
