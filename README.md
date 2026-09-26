# Station Parking — ANPR parking management system

Complete system for a two-wheeler parking lot next to a railway station (≈3,000 entries and
3,000 exits a day, sharp commuter peaks), ready for cars later. Vehicles are **never stopped**:
ANPR cameras identify them on the move, floor workers collect **UPI first** (with a controlled
cash pathway) inside the lot, charges settle on **actual time** with differences carried to the
next visit, monthly-pass holders pass with **zero interaction**, and every receipt is **digital**
(SMS with the full receipt, or a receipt QR shown on the worker's phone). Passes are sold and renewed
by workers in the app; no customer website is needed.

| Component | Folder | Runs on |
|---|---|---|
| ANPR edge service (side-by-side tracking, two-line plates, cross-camera merge, replay mode) | [`anpr/`](anpr/) | Edge server (GPU) |
| Backend: sessions, tariff engine, ledger, UPI + cash control, passes, receipts, disputes, reports, WebSocket | [`backend/`](backend/) | Edge server |
| Admin / supervisor dashboard | [`dashboard/`](dashboard/) | Browser (served by the backend) |
| Worker / guard / supervisor app (offline-tolerant) | [`worker_app/`](worker_app/) | Android app, or any phone's browser (iPhone too) at `/app` |
| Exit alert unit (display + tower light + buzzer) | [`alert_unit/`](alert_unit/) | Raspberry Pi 5 per gate |
| *Optional, not deployed by default:* customer website + cloud relay (self-pay, dues, passes) | [`customer_web/`](customer_web/) | Small cloud VM |
| Deployment, NTP, backups, restore test | [`deploy/`](deploy/) | Edge server |

## Documentation
* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — design, data model, invariants, interfaces, phases
* [docs/INSTALL.md](docs/INSTALL.md) — edge server, cameras, Raspberry Pi, phones, cloud relay
* [docs/OPERATIONS.md](docs/OPERATIONS.md) — daily routines for supervisors, workers and guards
* [docs/CAMERA_SETUP.md](docs/CAMERA_SETUP.md) — mounting, overlap, capture line, tuning, cars later
* [docs/ANPR.md](docs/ANPR.md) — recognition pipeline, event contract, **model/library licences**
* [docs/PRIVACY.md](docs/PRIVACY.md) — DPDP Act controls and signage text
* [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) — decisions taken where the spec left room

## Quick start (laptop demo, no cameras; needs only Python 3.11+)
```bash
./deploy/demo.sh --phone    # central system + dashboard + phone app + simulated camera traffic
```
* Dashboard: http://localhost:8000 (admin / admin123)
* Phone app: scan the QR code the script prints (iPhone or Android), log in as w1 / PIN 1111.
  On iPhone: Safari → Share → *Add to Home Screen*.
* The dashboard, phone app and Android APK are built by GitHub Actions and published as release
  `demo-latest`; the script downloads them, so no Flutter, Node.js or Android SDK is needed.

## Tests at a glance
```bash
cd backend  && python -m pytest -q     # unit + API + load + 3,000-vehicle full-day simulation
cd anpr     && python -m pytest -q     # pipeline, tracker, merge, outbox, end-to-end replay
cd dashboard && npm test               # UI helpers and API client
cd worker_app && flutter test          # Dart tariff port, offline queue, UPI QR builder
cd alert_unit && python -m pytest -q
cd customer_web && python -m pytest -q # relay + edge integration
```
