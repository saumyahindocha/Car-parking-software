# Customer web: cloud relay + self-service pages

A small FastAPI app on a cloud VM that customers reach over the internet (spec Section 9). The edge
server stays the source of truth; it is behind NAT and **initiates all sync** (`backend/app/relay_sync.py`,
every 15 s when `PARK_RELAY_URL` is set). Customers can pay here while the site's internet is down: the
relay confirms payments with its own gateway adapter and the edge catches up on the next pull.

```
 customer phone ──HTTPS──> Caddy/NGINX ──> relay (this app) <──push/pull/ack── edge server (NAT)
                                              │
                                  Razorpay (UPI QR + webhook), MSG91 (OTP SMS)
```

## Pages (server-rendered Jinja2, ~6 KB CSS + 1 KB JS, no frameworks, English / हिन्दी / मराठी)

| Path | What |
|---|---|
| `/` | landing for the QR standees: pay now, dues & balance, monthly pass; cash-receipt notice |
| `/pay` | recently entered vehicles (last 4 h): **masked plates** (`MH12••••34`), blurred thumbnails, entry time; or type your plate (exact, then approximate: 1 edit or OCR-confusable characters, shown masked) |
| `/pay/s/{session}` | durations with prices from the edge's quotes (quotes include previous dues) → UPI |
| `/pay/i/{token}` | UPI QR (inline SVG) + "Open UPI app" intent link on phones; polls status; success page with provisional details, then the edge-issued receipt link as soon as it is synced |
| `/dues` | plate + mobile → OTP → balance + history if the mobile matches the number on record; balance only if no number is on record (linked after the first payment via `CONTACT_VERIFIED`); nothing if it belongs to someone else. Pay dues by UPI. "I paid cash / I disagree" → dispute form → `DISPUTE` |
| `/pass` (`?plate=` from reminder SMS) | plate + mobile → OTP → pass types for the vehicle's class → UPI → `PASS_PAID`; "activates when payment reaches the parking system" |
| `/r/{code}` | receipt from pushed receipt data (lot, plate, class, entry time, duration paid, amount, previous dues cleared, UPI ref, receipt no., GST if configured, receipt footer). Unknown code → 404 "try again in a minute" |
| `/privacy` | DPDP Act 2023 notice; OTP-verified data request form → `DATA_REQUEST` |

Full plates appear only after the customer has typed them (self-pay search) or verified by OTP.

## Sync contract (relay side of `backend/app/relay_sync.py`)

All `/sync/*` calls need `X-Relay-Key` (constant-time compare; same value as the edge's `PARK_RELAY_API_KEY`).

* `POST /sync/push` – upserts `entries` (by `session_id`), `vehicles` (by plate), `receipts` (by code),
  `pass_types` (missing ones deactivated), `settings` (merged). `thumb_b64: null` in delta pushes keeps
  the stored thumbnail. `full: true` removes entries not in the list (exited/closed). The edge's pass
  dict includes the owner's phone – the relay drops it and stores only type/dates/status.
* `GET /sync/pull?after=<cursor>` → `{"messages":[{"id","kind","payload","created_at"}],"cursor":"<seq>"}`.
  Returns **every un-acked message**, oldest first (max 200); the cursor is informational. This is
  deliberate: `sync_once` advances its cursor even when a message fails to apply, so a cursor-filtered
  pull would lose it. The edge dedupes by message id (`relay_inbox`) and payments by `txn_ref`.
* `POST /sync/ack {"ids":[...]}` – marks delivered (idempotent).
* `GET /sync/status` – last push times, pending/stuck message counts (monitoring).
* `GET /sync/data-requests` – DPDP requests for the operator (they are also queued to the edge).

Message payloads (exactly what `apply_message` / `_apply_payment` read):

| kind | payload |
|---|---|
| `SELF_PAY_PAID` | plate, session_id, vehicle_class, duration_minutes, amount_paise, base_paise, dues_paise, txn_ref, gateway_ref, utr, phone, paid_at |
| `DUES_PAID` | plate, amount_paise, dues_paise, txn_ref, gateway_ref, utr, phone, paid_at |
| `PASS_PAID` | plate, vehicle_class, pass_type_id, amount_paise, txn_ref, gateway_ref, utr, phone, paid_at |
| `DISPUTE` | plate, claimed_paise, claimed_mode (CASH/UPI), claimed_when (ISO local time), note |
| `CONTACT_VERIFIED` | plate, phone |
| `DATA_REQUEST` | plate, phone, request_type (ACCESS/CORRECT/DELETE/WITHDRAW_CONSENT), note, requested_at |

Payment messages are queued **only after the gateway confirms** (signed webhook or status poll).
`phone` is sent only when it was verified by OTP in this browser; self-pay without OTP sends `null`.
`base_paise`/`dues_paise` split: dues = min(amount, vehicle balance at last push).

Transaction references (`tr`, ≤35 chars): self-pay `PS<session id base36>X<6 hex>` (same shape as the edge's
`make_txn_ref("S", …)`), dues `PRD<relay intent id base36>X<6 hex>`, pass `PRP<…>X<6 hex>`. The `PR*`
prefixes intentionally do not match the edge's `^P[SPV]` parser, so a relay credit that reaches the edge's
settlement report before its message is pulled is listed as an unknown credit rather than attributed to a
wrong vehicle id.

Optional fast path: with `RELAY_EDGE_URL` set (e.g. over WireGuard), confirmed messages are also POSTed to
`{EDGE_URL}/api/relay/apply` in a background task; they stay queued until acked by a pull, so this is safe.

## Security and privacy

* CSRF: double-submit cookie (`cw_csrf`, HttpOnly, Secure, SameSite=Lax) checked on every form POST.
* Cookies are HMAC-signed (`RELAY_SECRET_KEY`): `cw_otp` (5 min) and `cw_auth` (30 min, plate + phone).
* OTP: 6 digits, 5 min expiry, 5 attempts, single use, stored as HMAC only; 3 per 15 min per phone,
  10 per hour per IP. Plate searches: 30 per 10 min per IP. Disputes 5/day/plate, data requests 3/day.
* Headers: strict CSP (no inline script/style), X-Frame-Options DENY, Referrer-Policy no-referrer
  (receipt codes are in URLs), HSTS, `noindex`.
* Logs never contain phone numbers, OTPs or plates (masked where needed).
* Housekeeping (startup + hourly on push): expired OTPs, rate-limit rows, phone numbers on payment intents
  older than `RELAY_PHONE_RETENTION_DAYS` (30), acked messages older than that (DATA_REQUEST kept).

## Run locally / demo

```bash
cd customer_web
pip install -r requirements-dev.txt
export RELAY_DATABASE_URL=sqlite:///./relay.db RELAY_DEMO_MODE=true RELAY_COOKIE_SECURE=false
python -m relay.demo --reset                 # demo entries, a vehicle with dues, pass types, a receipt
uvicorn relay.main:app --port 8080           # http://localhost:8080
```

Demo mode shows a "Simulate payment" button on the QR page (mock gateway) and the OTP on screen.
Dues demo: plate `MH14CD5678`, mobile `9876543210`; pass demo: `MH12AB1234` (any mobile); receipt `/r/DemoRc01`.

With the real edge instead of `relay.demo`: run the backend with `PARK_RELAY_URL=http://localhost:8080`
(and the same `PARK_RELAY_API_KEY` / `RELAY_RELAY_API_KEY`, default `dev-relay-key`); its 15 s job pushes
entries and pulls customer payments.

## Tests

```bash
cd customer_web && python -m pytest -q
```

Sync push/pull/ack (delta push keeps thumbnails, full push removes closed entries, redelivery of un-acked
messages), OTP (hashing, expiry, attempts, per-phone and per-IP limits, CSRF, secure cookies, no PII in logs),
self-pay with the mock gateway (exact `SELF_PAY_PAID` payload, webhook signature + idempotency, status poll,
amount mismatch, gateway down, expiry), dues/pass/dispute/data-request messages, receipts, masking, i18n,
Razorpay and MSG91 adapters (mocked HTTP), and `test_integration_edge.py`: the real backend's
`relay_sync.sync_once` against this app in-process – a customer self-pay on the relay becomes a CONFIRMED
SELF_PAY payment on the edge and the edge's receipt then renders on the relay; dues + CONTACT_VERIFIED,
pass, dispute and data request all apply on the edge.

## Deploy (small cloud VM, e.g. 1 vCPU / 1 GB, Mumbai region)

1. `docker build -t parking-relay customer_web` and run it with an env file (see `.env.example`) and a
   volume for `/data` (SQLite) – or point `RELAY_DATABASE_URL` at Postgres for more than one container.
   `RELAY_WORKERS` defaults to 1 (keep 1 in demo mode; 2-4 with Razorpay + Postgres).

   ```bash
   docker run -d --name relay --restart unless-stopped -p 127.0.0.1:8080:8080 \
     --env-file /etc/parking-relay.env -v relay-data:/data parking-relay
   ```
2. TLS in front, e.g. Caddy (automatic HTTPS):

   ```
   pay.example.in {
       encode gzip
       reverse_proxy 127.0.0.1:8080
   }
   ```

   or NGINX with `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header
   X-Forwarded-Proto https;`. Set `RELAY_TRUST_PROXY_HEADERS=true` so rate limits see the client IP.
   Optionally restrict `/sync/` to the site's static IP(s) at the proxy.
3. Razorpay dashboard: webhook `https://pay.example.in/webhooks/razorpay`, events `qr_code.credited`,
   `payment.captured`, `payment.failed`, secret = `RELAY_RAZORPAY_WEBHOOK_SECRET`. If the edge uses the same
   Razorpay account, both webhooks can be configured; each side ignores QR codes it did not create.
4. MSG91: DLT-registered OTP template with variable `##otp##` → `RELAY_MSG91_TEMPLATE_OTP`.
5. Edge: `PARK_RELAY_URL=https://pay.example.in`, `PARK_RELAY_API_KEY=<same key>`,
   `PARK_PUBLIC_RECEIPT_BASE=https://pay.example.in/r` (receipt links in SMS; the edge derives the pass
   reminder link `https://pay.example.in/pass?plate=...` from it).
6. Print the standee QR codes pointing at `https://pay.example.in/` (or `/pay`).
7. Back up the relay DB daily (it holds unsynced payments only transiently; the edge is the system of record).

## Environment variables

`RELAY_DATABASE_URL`, `RELAY_RELAY_API_KEY`, `RELAY_SECRET_KEY`, `RELAY_PUBLIC_BASE_URL`, `RELAY_SITE_TIMEZONE`,
`RELAY_EDGE_URL`, `RELAY_GATEWAY` (mock|razorpay), `RELAY_RAZORPAY_KEY_ID`, `RELAY_RAZORPAY_KEY_SECRET`,
`RELAY_RAZORPAY_WEBHOOK_SECRET`, `RELAY_QR_EXPIRY_S`, `RELAY_SMS_PROVIDER` (noop|msg91), `RELAY_MSG91_AUTH_KEY`,
`RELAY_MSG91_TEMPLATE_OTP`, `RELAY_OTP_*` (ttl, attempts, per-phone/per-IP limits and windows),
`RELAY_AUTH_TTL_S`, `RELAY_RECENT_HOURS`, `RELAY_RECENT_LIMIT`, `RELAY_SEARCH_PER_IP*`,
`RELAY_STALE_PUSH_WARN_S`, `RELAY_GRIEVANCE_CONTACT`, `RELAY_COOKIE_SECURE`, `RELAY_TRUST_PROXY_HEADERS`,
`RELAY_DEMO_MODE`, `RELAY_PHONE_RETENTION_DAYS`, `RELAY_WORKERS` (container only). See `relay/config.py`.
