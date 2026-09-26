#!/usr/bin/env bash
# Laptop demo: central system (backend, SQLite, mock UPI gateway) + dashboard + phone app + synthetic
# camera traffic. Only Python 3.11+ is needed; the dashboard and phone app are downloaded ready-made.
#
#   ./deploy/demo.sh              laptop only:  http://localhost:8000 (dashboard), http://localhost:8000/app (phone app)
#   ./deploy/demo.sh --phone      + a secure public link and a QR code: scan it with any phone (iPhone or Android)
#   ./deploy/demo.sh --update     re-download the latest dashboard / phone app builds first
#   ./deploy/demo.sh --no-anpr    no synthetic camera traffic (add events from the API docs instead)
#   ./deploy/demo.sh --with-alert also open the Gate 2 exit display window
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${DEMO_DIR:-$ROOT/.demo}"
PORT="${PORT:-8000}"
ANPR=1 ALERT=0 PHONE=0 UPDATE=0
for a in "$@"; do
  case $a in
    --no-anpr) ANPR=0 ;; --with-alert) ALERT=1 ;; --phone) PHONE=1 ;; --update) UPDATE=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option $a (see --help)"; exit 1 ;;
  esac
done
mkdir -p "$WORK/images" "$WORK/uploads" "$WORK/apps" "$WORK/bin"
pids=()
cleanup() { echo; echo "stopping demo"; for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null; }
trap cleanup EXIT INT TERM
say() { echo -e "\033[1;34m==>\033[0m $*"; }

# ---------------------------------------------------------------- Python packages (first run only)
reqs=("$ROOT/backend/requirements.txt" "$ROOT/anpr/requirements.txt")
stamp="$WORK/.deps-$(cat "${reqs[@]}" | md5sum | cut -c1-12)"
if [[ ! -f "$stamp" ]]; then
  say "Installing Python packages (first run only)"
  python3 -m pip install --progress-bar on -r "${reqs[0]}" -r "${reqs[1]}" qrcode
  touch "$stamp"
fi

# ---------------------------------------------------------------- ready-made apps (built on GitHub)
remote="$(git -C "$ROOT" config --get remote.origin.url 2>/dev/null || true)"
repo="$(echo "$remote" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
[[ "$repo" == */* ]] || repo="saumyahindocha/Car-parking-software"
release="https://github.com/$repo/releases/download/demo-latest"

fetch_app() {  # name  zip  target-dir
  local name=$1 zip=$2 dir=$3
  if [[ $UPDATE == 1 || ! -f "$dir/index.html" ]]; then
    say "Downloading the $name (ready-made build)"
    if curl -fL --progress-bar "$release/$zip" -o "$WORK/apps/$zip.tmp"; then
      rm -rf "$dir" && mkdir -p "$dir"
      python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$WORK/apps/$zip.tmp" "$dir"
      rm -f "$WORK/apps/$zip.tmp"
    else
      echo "   could not download $release/$zip"
    fi
  fi
}
fetch_app "dashboard" dashboard.zip "$WORK/apps/dashboard"
fetch_app "phone app" worker-web.zip "$WORK/apps/worker-web"

DASH="$WORK/apps/dashboard"
if [[ ! -f "$DASH/index.html" ]]; then            # offline fallback: local build
  DASH="$ROOT/dashboard/dist"
  if [[ ! -f "$DASH/index.html" ]] && command -v npm >/dev/null; then
    say "Building the dashboard locally"
    (cd "$ROOT/dashboard" && npm ci --no-audit --no-fund && npm run build)
  fi
fi
WEBAPP="$WORK/apps/worker-web"
[[ -f "$WEBAPP/index.html" ]] || WEBAPP="$ROOT/worker_app/build/web"

# ---------------------------------------------------------------- central system
export PARK_DATABASE_URL="sqlite:///$WORK/demo.db" PARK_DEMO_MODE=true PARK_GATEWAY=mock \
       PARK_IMAGE_ROOT="$WORK/images" PARK_UPLOAD_ROOT="$WORK/uploads" \
       PARK_DASHBOARD_DIST="$DASH" PARK_WORKER_WEB_DIST="$WEBAPP"
say "Starting the central system"
(cd "$ROOT/backend" && python3 -m app.seed --create-all --users >/dev/null)
(cd "$ROOT/backend" && exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --log-level warning) &
pids+=($!)
for i in $(seq 1 30); do curl -fs "http://localhost:$PORT/api/health" >/dev/null && break; sleep 1; done

# ---------------------------------------------------------------- phone access: public HTTPS link + QR
PUBLIC=""
if [[ $PHONE == 1 ]]; then
  cf="$WORK/bin/cloudflared"
  if [[ ! -x "$cf" ]]; then
    arch=$(uname -m); [[ $arch == aarch64 || $arch == arm64 ]] && arch=arm64 || arch=amd64
    say "Downloading the secure-link tool (Cloudflare, first run only)"
    curl -fL --progress-bar "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$arch" -o "$cf"
    chmod +x "$cf"
  fi
  say "Opening a secure public link to this demo"
  "$cf" tunnel --no-autoupdate --url "http://localhost:$PORT" >"$WORK/tunnel.log" 2>&1 &
  pids+=($!)
  for i in $(seq 1 40); do
    PUBLIC=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$WORK/tunnel.log" | head -1 || true)
    [[ -n "$PUBLIC" ]] && break; sleep 1
  done
  [[ -n "$PUBLIC" ]] || echo "   could not open the public link (see $WORK/tunnel.log); the laptop links still work"
fi

# ---------------------------------------------------------------- optional extras
if [[ $ALERT == 1 ]]; then
  (cd "$ROOT/alert_unit" && exec python3 -m exit_alert --windowed --mock-gpio --edge-url "http://localhost:$PORT" \
      --device-key dev-device-key --gate G2) &
  pids+=($!)
fi
if [[ $ANPR == 1 ]]; then
  (
    cd "$ROOT/anpr"
    round=1
    while true; do
      out="$WORK/synth-$round"
      python3 -m anpr_service synth --out "$out" --seed "$round" >/dev/null 2>&1
      BACKEND_URL="http://localhost:$PORT" ANPR_API_KEY=dev-anpr-key IMAGE_ROOT="$WORK/images" \
        ANPR_OUTBOX="$WORK/outbox.sqlite" python3 -m anpr_service replay --config "$out/site.synth.yaml" --realtime \
        >>"$WORK/anpr.log" 2>&1
      rm -rf "$out"
      round=$((round + 1))
    done
  ) &
  pids+=($!)
fi

# ---------------------------------------------------------------- how to use it
echo
echo "  ┌──────────────────────────────────────────────────────────────────────────────┐"
echo "    Dashboard (laptop)   http://localhost:$PORT            admin / admin123"
echo "    Phone app (laptop)   http://localhost:$PORT/app        w1 / PIN 1111"
if [[ -n "$PUBLIC" ]]; then
  echo
  echo "    Phone app (any phone, scan this QR or open the link):"
  echo "    $PUBLIC/app"
  python3 -c "import qrcode,sys; q=qrcode.QRCode(border=1); q.add_data(sys.argv[1]); q.print_ascii(invert=True)" "$PUBLIC/app" 2>/dev/null \
    | sed 's/^/    /' || true
  echo "    iPhone: open in Safari → Share → Add to Home Screen for a full-screen app."
  echo "    Dashboard from anywhere: $PUBLIC  (anyone with the link can see the demo while it runs)"
fi
echo
echo "    Logins   workers w1..w4 (PIN 1111..4444) · guard1 (5555) · sup1 (PIN 1234 / password super123)"
echo "    UPI      the demo gateway is simulated: after showing the QR, mark it paid at"
echo "             http://localhost:$PORT/docs → POST /api/demo/pay/{payment_id}. Cash works end to end."
echo "  └──────────────────────────────────────────────────────────────────────────────┘"
echo "  Ctrl-C to stop."
wait
