# Architecture — ANPR Parking System (railway-station lot)

Two-wheeler lot today (≈3,000 entries + 3,000 exits a day, 400–500 movements an hour at the
commuter peaks); cars later. Vehicles are never stopped: ANPR identifies them on the move,
workers collect UPI (default) or cash inside the lot, charges settle against actual time, and
differences are carried to the next visit. Monthly-pass holders pass with zero interaction.

## 1. Deployment view

```
                       ┌──────────────────────────── site LAN (192.168.10.0/24) ───────────────────────────┐
  4× ANPR cam  ─RTSP─┐ │  EDGE SERVER (Ubuntu 24.04, GPU, UPS)                                             │
  2× overview  ─RTSP─┤ │  ┌───────────────┐  events   ┌──────────────────────────────┐   ┌──────────────┐  │
                     └─┼─►│ anpr (Python) │──HTTP────►│ backend (FastAPI, 1 process) │◄─►│ PostgreSQL 16│  │
                       │  │ per-camera    │  JPEGs    │  REST + WebSocket hub        │   └──────────────┘  │
                       │  │ workers + gate│──────────►│  job scheduler thread        │   images: 4 TB HDD  │
                       │  │ aggregators   │ /data/img │  serves dashboard SPA        │                     │
                       │  └───────────────┘           └───────┬───────┬──────┬───────┘                     │
                       │   chrony (NTP for all devices)       │WS     │REST  │WS (exit topic per gate)     │
                       └──────────────────────────────────────┼───────┼──────┼─────────────────────────────┘
                                   supervisor PC (dashboard) ◄┘       │      └► 2× exit alert unit (Pi 5 +
                                   worker / guard phones (Flutter) ◄──┘         display + tower light)
                                                   │ outbound HTTPS only (edge initiates)
                                                   ▼
                        ┌───────────── CLOUD RELAY (customer_web) ─────────────┐
                        │ self-pay, dues lookup (OTP), pass purchase, receipts │◄── customers (QR standees, SMS links)
                        │ own gateway webhook; queue of customer actions       │◄── Razorpay webhooks
                        └──────────────────────────────────────────────────────┘
```

Everything that records entries/exits and matches sessions runs on the edge server and does not
need the internet. Internet is needed only for UPI confirmation, SMS/WhatsApp and the relay; each
of those degrades gracefully (offline UPI path, queued messages, relay catch-up).

## 2. Components

| Folder | What | Tech |
|---|---|---|
| `anpr/` | Camera ingest (RTSP/replay) → detect → track → plate → OCR → multi-frame vote → validate → cross-camera merge → event; durable outbox | Python 3.12, OpenCV, ONNX Runtime/TensorRT, pluggable `PlateRecognizer` |
| `backend/` | Source of truth: sessions, matching, tariff, ledger, payments, cash control, passes, receipts, disputes, reports, jobs, WebSocket hub | FastAPI, SQLAlchemy 2, PostgreSQL 16, Alembic |
| `dashboard/` | Admin/supervisor web app (live view, review queue, cash control, ledger, reports, config) | React + TypeScript + Vite, served by the backend |
| `worker_app/` | Worker / guard / supervisor Android app, offline-tolerant | Flutter, sqflite queue |
| `alert_unit/` | Exit display + tower light + buzzer per gate | Python on Raspberry Pi 5, pygame, gpiozero |
| `customer_web/` | Cloud relay + customer pages (self-pay, dues, passes, receipts) | FastAPI + Jinja2 |
| `deploy/` | Docker Compose, installer, NTP, backups, restore test | Docker, chrony, pg_dump, age, rclone |

## 3. Core domain (backend)

**Money** is integer paise everywhere. **Time** is UTC in the database (timezone-aware), local
time (Asia/Kolkata) only for tariff day logic and display.

### Data model (main tables)
`vehicle_classes` (BIKE, CAR — car disabled) · `tariffs` (versioned per class, effective-from) ·
`pass_types` · `gates` (direction + time-of-day schedule) · `cameras` (RTSP, ROI, capture line,
IN vector) · `zones` · `worker_zones` (zone assignment windows) · `users` (roles, PIN/password,
device binding) · `shifts` · `vehicles` (normalised + canonical plate, cached balance) ·
`anpr_events` · `plate_corrections` (every approximate match / manual correction) ·
`parking_sessions` · `ledger_entries` (append-only) · `payments` · `overrides` ·
`cash_handovers` · `bank_deposits` · `payment_disputes` · `passes` · `receipts` · `message_log` ·
`alerts` · `devices` · `audit_log` · `relay_inbox` · `settings`.

`cash_holdings` is **derived**, not stored: cash-in-hand for a shift = confirmed cash payments in
the shift − counted cash handed over in the shift (`domain/cash.py::cash_in_hand`).

### Invariants
* **Ledger is the truth.** Balance = Σ ledger amounts (positive = customer owes). `vehicles.balance_paise`
  is a cache updated in the same transaction by SQL increment and verified by `ledger.verify_balances`
  (and by the weekly restore test).
* **Append-only finance.** SQLAlchemy `before_flush` hook refuses deletes of financial rows and any
  update of ledger rows; corrections are new `ADJUSTMENT`/`REVERSAL`/`REFUND` entries with a reason.
* **Audit everything.** `after_flush` hook writes an `audit_log` row (before/after) for every insert,
  update and delete, with the acting user.
* **Amounts come from the tariff engine.** A different amount needs an `overrides` row approved by a
  supervisor PIN; reported daily.

### Tariff engine (`domain/tariff.py`)
`calculate_charge(vehicle_class, entry_time, exit_time, tariff_version)` is pure. Rules: optional
free minutes; stay split into blocks (default 12 h) each capped (₹30); inside a block the first slab
(₹10 for 2 h) plus ₹5 per *started* extra hour, with a grace (10 min) past every slab boundary;
optional daily cap and overnight charge. The tariff in force **at entry** applies to the whole stay.
The Flutter app carries a line-for-line Dart port (same unit-test cases) for offline quotes.

### Session matching (`domain/sessions.py`)
* Plates are normalised, then corrected position-aware using the confusion map
  (0/O/D/Q, 1/I/L, 2/Z, 5/S, 8/B, 6/G) against Indian formats (standard and BH) and state codes.
* **IN**: exact vehicle → else a known regular (pass holder or non-zero balance) by confusion-only
  match, or by one real edit **only when the read is doubtful** (invalid format or confidence < 0.9 —
  neighbouring registrations are common) → else new vehicle. Pass holder → `PASS` session.
  An already-open session is closed as `ORPHAN_ENTRY` for review.
* **OUT**: exact open session → else approximate among open sessions of the same class that entered
  earlier (canonical Levenshtein ≤ 1, configurable); exactly one best candidate is used and logged,
  several → `REVIEW` (AMBIGUOUS) with candidates; none → known regular → `ORPHAN_EXIT`; else `REVIEW`
  (NO_MATCH). If a confident exit read contradicts a low-confidence entry read that created a
  one-off vehicle, the session is **re-homed** to the true plate (payments follow via a balanced
  adjustment pair). Supervisor resolutions (typed plate) use the same logic.
* **Close**: charge by tariff at entry → `CHARGE` ledger entry; confirmed payments already offset it;
  the difference stays on the balance. Pass sessions post nothing (if the pass expired mid-stay, the
  time after expiry is charged). `UNREAD` events go straight to review.
* Backend-side de-duplication (same plate, gate, direction within 60 s) behind the ANPR aggregator.

### Payments (`domain/payments.py`)
* **UPI online**: dynamic QR from the gateway (Razorpay QR Codes API, single use, fixed amount),
  `txn_ref` = `P{S|P|V}{base36 id}X{hex}` encodes what is paid; `CONFIRMED` only by signed webhook
  or status check.
* **UPI offline**: if the gateway is unreachable (server) or the server is unreachable (phone), a
  standard `upi://pay` intent QR with the same `txn_ref` scheme; worker records `CLAIMED_OFFLINE`
  after seeing the customer's success screen. The reconciliation job matches settlement/bank lines by
  `txn_ref` → `CONFIRMED`; claims unconfirmed for 24 h raise a review alert (supervisor confirms with
  UTR or fails them).
* **Cash**: only for a session/pass/dues, for the system amount; needs cash enabled (optionally
  cash-desk-only); blocks at the per-worker cash-in-hand limit (warning at 80 %); `CONFIRMED` at
  record time; digital receipt issued immediately.
* Refunds (UPI) and reversals (cash) are supervisor-only with a reason, post ledger entries and an
  `overrides` row.

### Cash control (`domain/cash.py`)
Shifts open with zero cash. Two-party handover: worker declares amount + denominations, supervisor
counts (denominations + photo, note mandatory on variance). Shift close refuses while a handover
is pending and records any cash still in hand as negative variance (with a note). Daily bank
deposit with slip reference/photo; bank-credit marking. `cash_reconciliation(date)` shows collected
→ handed over → deposited → credited and every gap, per worker.

### Accountability (`domain/reports.py::worker_comparison`)
Per worker per shift: collections, UPI vs cash, cash share vs average, unpaid sessions in the zone
(attributed through `worker_zones` at entry time), disputes by outcome, handover variances,
receipts to phone vs shown, limit breaches. Flags: `HIGH_UNPAID_IN_ZONE` (binomial z-score vs other
shifts), `HIGH_UNPAID_LOW_CASH`, `LOW_CASH_SHARE`, `REPEATED_DISPUTES`, `SUSPECT_UNRECORDED_CASH`,
`CASH_SHORT`, `LIMIT_BREACH`, `LOW_RECEIPT_TO_PHONE`. Disputes are auto-linked to the worker who took
a payment for the session, else the worker covering the zone at entry.

## 4. Interfaces

### ANPR → backend
`POST /api/anpr/events` (header `X-Device-Key`), idempotent on `event_id`; payload in
[docs/ANPR.md](ANPR.md). `GET /api/anpr/config` gives gates (effective direction after the
schedule), cameras and matching settings. `POST /api/devices/heartbeat` from every camera worker
and alert unit (fps, last frame time, read rate) feeds the watchdog and the device-health panel.

### Apps → backend (REST, bearer token)
Login (`/api/auth/login`, PIN or password; worker/guard accounts bind to one phone),
`/api/bootstrap` (cached by the app for offline use: tariffs, pass types, settings, zone),
worker endpoints (`/api/collect/list`, `/api/vehicles/search`, quotes, `/api/payments/upi|cash`,
receipts, shifts, handovers, passes, disputes, alerts), and the idempotent offline batch
`POST /api/sync` (items `CASH`, `UPI_CLAIM`, `DISPUTE`, `HANDOVER`, `RECEIPT_SHOWN`, `CONTACT`, each
with a client UUID). Supervisor/admin endpoints under `/api/review`, `/api/cash/*`, `/api/disputes`,
`/api/zones`, `/api/config/*`, `/api/users`, `/api/reports/{name}?format=json|xlsx|pdf`,
`/api/audit`, `/api/privacy/*`. OpenAPI at `/docs` on the edge server.

### Live events (WebSocket)
`/ws?token=…` for users, `/ws/device?key=…&gate_id=G2` for alert units. Messages
`{"topic","data","ts"}`; topics: `anpr.event`, `session.opened|closed`, `payment.updated`,
`cash.updated`, `handover.pending|confirmed`, `alert`, `exit`, `review.new`, `dispute.new`,
`pass.activated`, `shift.closed`, `device.health`. Events are published only **after** the DB
commit. The hub lives in the single backend process (one uvicorn worker on purpose; the load is
far below one process's capacity — see §6).

### Edge ↔ cloud relay (`backend/app/relay_sync.py`)
The edge initiates everything (it sits behind NAT): **push** open sessions (masked plates, blurred
plate thumbnails, per-duration quotes including dues), changed vehicle balances/history (phone
stored only as a salted hash), new receipts, pass types and lot settings; **pull** queued customer
actions (`SELF_PAY_PAID`, `DUES_PAID`, `PASS_PAID`, `DISPUTE`, `CONTACT_VERIFIED`) and **ack** them.
Applying a message is idempotent (`relay_inbox` + unique `txn_ref`). The relay confirms self-pay
UPI with the gateway itself, so customers can pay while the site's internet is down; the edge
catches up on the next pull.

## 5. Background jobs (`backend/app/jobs.py`)
Message delivery (10 s, WhatsApp→SMS fallback, retries) · unpaid-after-30-min flag (60 s) ·
watchdog: camera stalled > 60 s, internet down > 5 min, gateway health (30 s) · UPI reconciliation
(10 min) · stale offline claims → review (hourly) · pass expiry + 5-day/1-day reminders (30 min) ·
retention purge (daily) · relay sync (15 s).

## 6. Reliability and performance
* Docker restart policies, health checks, systemd unit; UPS; dual WAN.
* ANPR outbox (SQLite) buffers events in order while the backend is down; the backend's ingest is
  idempotent so redelivery is safe. Phones queue actions in SQLite and sync idempotently.
* Measured: in-process ingest sustains 30 events/s (3× the 10 events/s burst requirement) with
  p99 ≈ 26 ms on SQLite; `backend/loadtest/burst.py` runs the same against a live server/Postgres.
  12,000 events/day with cars is ≈ 0.14 events/s average.
* Nightly `pg_dump` to an external disk, encrypted cloud copy, weekly automated restore test that
  checks ledger vs cached balances (`deploy/verify_backup.sh`).

## 7. Security and privacy
Bearer tokens (HMAC-SHA256, 14 h), PBKDF2 PIN/password hashes, per-user device binding for field
accounts, role checks on every endpoint, device keys for ANPR/alert units, relay key for sync,
signed gateway webhooks. Images are served only to authenticated roles; public pages show masked
plates and blurred thumbnails. Retention purge (plate images 90 d, full frames 30 d), customer data
export and erasure (financial records retained with phone removed). See [PRIVACY.md](PRIVACY.md).

## 7a. Cars later
Vehicle class is carried by every event, vehicle, session, tariff and pass type. Enabling cars =
switch on the CAR class and review its seeded tariff/pass prices in the dashboard; tune capture
lines for car plate height ([CAMERA_SETUP.md](CAMERA_SETUP.md)). No schema change.

## 8. Build phases (as delivered)
1. Data model, tariff engine, ledger, passes, approximate matching, full-day simulator — `backend/`, `backend/sim/`.
2. ANPR service in replay mode with side-by-side tracking and cross-camera merge — `anpr/`.
3. Admin dashboard with review queue and reports — `dashboard/`.
4. Worker app with UPI (mock + Razorpay), offline path, receipts, full cash pathway — `worker_app/`.
5. Exit alert unit — `alert_unit/`.
6. Customer self-service pages and cloud relay — `customer_web/`.
7. Deployment, backups, retention, documentation — `deploy/`, `docs/`.

See [ASSUMPTIONS.md](ASSUMPTIONS.md) for the decisions taken where the spec left room.
