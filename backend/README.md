# Edge backend

FastAPI + PostgreSQL service that is the source of truth for sessions, tariffs, the ledger,
payments (UPI + controlled cash), passes, receipts, disputes, reports and live events.
Architecture: [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).

## Layout
```
app/
  models.py            SQLAlchemy models (money = paise, time = UTC)
  audit.py             audit log + append-only enforcement (session hooks)
  events.py, ws.py     post-commit event bus -> WebSocket hub
  domain/
    tariff.py          calculate_charge() – pure, versioned tariffs
    plates.py          normalise / validate / confusion-aware correction / Levenshtein
    sessions.py        ANPR ingest, entry/exit matching, review resolution, re-homing
    ledger.py          append-only ledger, balance verification
    payments.py        UPI online/offline, cash, overrides, reversals, refunds, reconciliation
    cash.py            shifts, cash-in-hand, handovers, deposits, cash reconciliation
    passes.py          pass windows, activation, expiry, reminders
    disputes.py        disputes auto-linked to the worker on duty
    receipts.py        digital receipts (short links)
    notify.py          queued SMS / WhatsApp
    reports.py         worker comparison + all dashboard reports
    privacy.py         retention purge, export, erasure
  adapters/            payment gateway (Razorpay, mock), SMS (MSG91), WhatsApp (Meta), no-op
  api/                 REST routers
  relay_sync.py        edge <-> cloud relay protocol
  jobs.py              background scheduler
sim/simulator.py       synthetic full-day simulator
loadtest/burst.py      burst load test
alembic/               migrations
```

## Run (development, SQLite)
```bash
pip install -r requirements-dev.txt
export PARK_DATABASE_URL=sqlite:///./dev.db PARK_DEMO_MODE=true
python -m app.seed --create-all --users     # demo users: admin/admin123, sup1/super123 (PIN 1234), w1..w4 PIN 1111..4444, guard1 PIN 5555
uvicorn app.main:app --reload --port 8000   # OpenAPI docs at http://localhost:8000/docs
```
Demo helpers (only with `PARK_DEMO_MODE=true`): `POST /api/demo/event` (inject an ANPR event),
`POST /api/demo/pay/{payment_id}` (customer completes a UPI payment on the mock gateway),
`POST /api/demo/gateway {"online": false}` (simulate an internet outage).

## Run (PostgreSQL)
```bash
export PARK_DATABASE_URL=postgresql+psycopg://parking:parking@localhost/parking
alembic upgrade head && python -m app.seed --users
uvicorn app.main:app --port 8000
```
Run **one** process (the WebSocket hub and the scheduler are in-process).

## Tests
```bash
python -m pytest -q                        # all tests incl. the 3,000-vehicle full-day simulation (~75 s)
SIM_VEHICLES=1000 python -m pytest -q      # quicker simulation
TEST_DATABASE_URL=postgresql+psycopg://parking:parking@localhost/parking_test python -m pytest -q --deselect tests/test_simulation.py
python -m sim.simulator --vehicles 3000    # prints the day's summary
python loadtest/burst.py --inprocess --rate 30 --seconds 10
python loadtest/burst.py --url http://edge:8000 --key $PARK_ANPR_API_KEY --rate 10 --seconds 60
```

## Configuration
Process settings are environment variables prefixed `PARK_` (see `app/config.py` and
`deploy/.env.example`). Site settings (cash limit, approximate-matching tolerance, alert thresholds,
retention, …) live in the `settings` table and are edited from the dashboard
(`app/domain/settings.py` lists every key with its default).
