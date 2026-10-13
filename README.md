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
| ANPR edge service: our own plate finder + reader (trained on computer-generated plates), side-by-side tracking, two-line plates, cross-camera merge, replay mode | [`anpr/`](anpr/) | Edge server (CPU; no GPU needed) |
| Backend: sessions, tariff engine, ledger, UPI + cash control, passes, receipts, disputes, reports, WebSocket | [`backend/`](backend/) | Edge server |
| Admin / supervisor dashboard | [`dashboard/`](dashboard/) | Browser (served by the backend) |
| Worker / guard / supervisor app (offline-tolerant) | [`worker_app/`](worker_app/) | Android app, or any phone's browser (iPhone too) at `/app` |
| Exit alert unit (display + tower light + buzzer) | [`alert_unit/`](alert_unit/) | Raspberry Pi 5 per gate |
| *Optional, not deployed by default:* customer website + cloud relay (self-pay, dues, passes) | [`customer_web/`](customer_web/) | Small cloud VM |
| Deployment: one-command installer, rehearsal mode, site HTTPS, NTP, backups, restore test, health check | [`deploy/`](deploy/) | Edge server |

## Project status

**Ready for a pilot install at one gate; not yet ready to run unattended.** All components are built
and tested, and the full install has been rehearsed from scratch (`install.sh --test` + `check.sh`:
every check passes, including a real backup and restore). What can only be proven on site: plate
reading on real bikes, real UPI payments (needs a Razorpay account) and SMS receipts (needs MSG91 +
DLT templates). Full report, site-visit checklist and setup guide: **[docs/SITE_HANDBOOK.md](docs/SITE_HANDBOOK.md)**.

| Part | State |
|---|---|
| Central system (sessions, tariffs, passes, dues ledger, cash control, disputes, reports, audit, privacy) | Built · 87 tests pass |
| Admin / supervisor dashboard, customer import | Built |
| Phone app for workers, guards and supervisors (browser on iPhone/Android, or Android app) | Built · tried on iPhone |
| Number-plate reading with our own models (no paid service) | Built · 134 tests pass · not yet tested on real plates |
| Exit alert unit (Raspberry Pi) | Built · 31 tests pass · not yet on real hardware |
| Installer, site HTTPS, backups, health check | Rehearsed end to end |
| UPI payments (Razorpay) and SMS receipts (MSG91) | Built · waiting for accounts |
| GST invoices / Tally export, owner phone alerts | Not built (optional) |

**Plate reading.** No commercial recogniser: a plate finder and a whole-plate reader trained only on
Indian plates we generate (fonts, HSRP/old/commercial/EV styles, dirt, glare, night IR, blur). On
held-out generated plates the reader gets 78% of plates fully right from a single frame (99.4% when
confident); on the demo gate videos, drawn by a different program, it reads 5 of 6 plates with no
wrong plates. The service votes over many frames and two cameras per gate. The plan to reach 95% day /
90% night is one week of real footage at the site, a correction pass and a retrain
([docs/ANPR.md §9](docs/ANPR.md)). `anpr/tools/try_video.py` gives a first look at any phone video.

**Next steps:** site visit (photos, gate measurements, test videos) → open Razorpay and MSG91/DLT
accounts → buy pilot hardware for one gate → install the site PC → pilot week → retrain → second gate.

## Documentation
* [docs/SITE_HANDBOOK.md](docs/SITE_HANDBOOK.md) — **start here**: status, site visit, site PC, cameras, staff phones, pilot, go-live* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — design, data model, invariants, interfaces, phases
* [docs/INSTALL.md](docs/INSTALL.md) — edge server, rehearsal install on any PC, cameras, Raspberry Pi, phones, cloud relay
* [docs/OPERATIONS.md](docs/OPERATIONS.md) — daily routines for supervisors, workers and guards
* [docs/CAMERA_SETUP.md](docs/CAMERA_SETUP.md) — mounting, overlap, capture line, tuning, cars later
* [docs/ANPR.md](docs/ANPR.md) — recognition pipeline, our plate models (training and site fine-tuning), event contract, **model/library licences**
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

## Install
```bash
cd deploy
sudo ./install.sh --test    # rehearsal on any PC (also Windows + WSL2): demo videos as cameras, pretend payments
sudo ./install.sh           # real site server (Ubuntu 24.04)
sudo ./check.sh             # health check: services, HTTPS, phone app, cameras, latency, disk, backup + restore
```

## Tests at a glance
```bash
cd backend  && python -m pytest -q     # unit + API + load + 3,000-vehicle full-day simulation
cd anpr     && python -m pytest -q     # pipeline, tracker, merge, outbox, our plate models, end-to-end replay
cd dashboard && npm test               # UI helpers and API client
cd worker_app && flutter test          # Dart tariff port, offline queue, UPI QR builder
cd alert_unit && python -m pytest -q
cd customer_web && python -m pytest -q # relay + edge integration
```
