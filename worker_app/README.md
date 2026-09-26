# Parking Worker (Android app)

Flutter app for the **floor workers, guards and supervisors** of the station two-wheeler lot.
One APK, three modes chosen by the account's role at login:

| Role | Home | What they do |
|---|---|---|
| `WORKER` | **To collect** | Collect UPI / cash for open sessions, sell passes, hand over cash, open/close shift |
| `GUARD` | **Exit alerts** | See unpaid exits with the plate image; one tap "Customer says paid" raises a dispute. Guards never stop traffic |
| `SUPERVISOR` | Worker screens + **Supervisor** | Confirm handovers (count + photo), live cash-in-hand of every worker, disputes, zones, reversal/refund, bank deposit |

Android 12+ only (`minSdk 31`). App name on the phone: **Parking Worker**.

## Build

Requires the Flutter SDK (stable; built with Flutter 3.47.5 / Dart 3.13.4) and the Android SDK
(platform 36 + build-tools, installed with Android Studio or `sdkmanager`).

```bash
cd worker_app
flutter pub get
flutter analyze          # must report "No issues found!"
flutter test             # tariff port, UPI/txn-ref, plates, offline queue, widgets
flutter build apk --release
# -> build/app/outputs/flutter-apk/app-release.apk
```

### Release signing

By default the release build is signed with the debug key so it installs by sideloading. For a
stable signature across updates (Android refuses to update an app signed with a different key),
create a keystore once and keep it safe:

```bash
keytool -genkey -v -keystore ~/parking-worker.jks -keyalg RSA -keysize 2048 -validity 10000 -alias parking
```

then add `android/key.properties` (git-ignored) and a `signingConfigs.release` block in
`android/app/build.gradle.kts` reading it (see https://docs.flutter.dev/deployment/android#sign-the-app).

## Install on the phones (sideloading)

1. On each phone: *Settings → Security → Install unknown apps* → allow your file manager (or `adb`).
2. Copy `app-release.apk` to the phone (USB, or download it from the edge server's LAN share) and
   open it, **or** with USB debugging on: `adb install -r app-release.apk`.
3. Connect the phone to the lot Wi-Fi (the edge server is on the LAN).
4. Open **Parking Worker**. The server URL defaults to `https://192.168.10.10` (install the site certificate first, see docs/INSTALL.md §2a); tap
   *Server: …* on the login screen to change it and *Save & test connection*. It is stored on the phone.
5. Grant the camera permission when first scanning a plate / photographing cash.

Updating: install the new APK over the old one (same signing key). The offline queue and the
login survive an update.

## Device binding

The app creates a random device id (UUID) on first launch and keeps it in app storage. It is
sent with every login (`POST /api/auth/login` with `username`, `pin`, `device_id`):

* The **first** login of a `WORKER` / `GUARD` / `SUPERVISOR` binds the account to that phone.
* Logging in to the same account from another phone is refused ("this account is bound to another
  phone"). If a phone is lost/replaced, the admin resets the binding (`POST /api/users/{id}/reset-device`
  in the dashboard). Uninstalling the app or clearing its data also creates a new device id, so
  it needs a reset too.
* Tokens carry the device id; a token from another phone is rejected.

## Offline behaviour

The phone keeps working when the edge server (or the Wi-Fi) is down. A red banner shows
**OFFLINE** with the number of actions waiting; the **Sync** tab shows details.

* **Cached for offline use** (SQLite): `/api/bootstrap` (tariffs, pass types, settings incl. UPI
  payee VPA, cash limit, duration buttons, zone), the To-collect list, the worker's cash holding
  and last shift.
* **Amounts**: computed on the phone with a Dart port of the backend tariff engine
  (`lib/tariff/tariff.dart`, tested case-for-case against `backend/tests/test_tariff.py`) from the
  tariff in force at entry. Previous dues come from the cached list. Overrides need the server.
* **Cash** works fully offline: the payment is saved in the local queue as a `CASH` sync item with
  a `client_uuid` and the time it was taken. The **cash-in-hand limit is enforced on the phone**:
  server holding + cash still waiting to sync (and cash whose sync failed) — collection is blocked at
  the limit, with a warning banner from 80 %. The mobile number is asked every time; if declined, the
  receipt QR is shown full-screen and the worker must tap **Customer scanned** (queued as
  `RECEIPT_SHOWN`). The phone generates the 8-character receipt code and sends it in the `CASH` item
  (`receipt_code`); the server adopts it, so the offline QR is already the real link
  `${public_receipt_base}/<code>` (from the cached bootstrap), with the details also shown as text.
* **UPI offline**: the phone builds a standard UPI intent QR (`upi://pay?pa=…&am=…&tr=…`) for the exact
  amount with a transaction reference in the backend format `P{S|V}{base36 id}X{6 hex}` (session id,
  or vehicle id for dues/pass). When the customer shows the success screen the worker taps
  **Customer shows success** → queued as `UPI_CLAIM` (recorded `CLAIMED_OFFLINE`, confirmed later by
  reconciliation against the settlement by reference). If only the payment gateway is down but the
  server is up, the server returns an offline intent QR and the claim goes through
  `POST /api/payments/{id}/claim-offline`; if the server disappears before that, the claim is queued
  with that payment's `txn_ref` and amount and the server claims the same payment on sync.
* **Switching from UPI to cash / backing out of the QR screen** cancels the unpaid UPI payment
  (`POST /api/payments/{id}/cancel`). If the customer actually paid meanwhile, the server confirms it
  instead and the screen turns green.
* **Also queued offline**: guard disputes (`DISPUTE`), cash handover declarations (`HANDOVER`),
  receipt shown (`RECEIPT_SHOWN`), customer mobile numbers (`CONTACT`), alert acknowledgements
  (`ALERT_ACK`), plate corrections (`PLATE_CORRECTION`), shift open/close (`SHIFT_OPEN`, `SHIFT_CLOSE`,
  applied after every earlier queued item).
* **Sync**: `POST /api/sync` in creation order, every 20 s, on reconnect, and on *Retry now*. Each
  item is idempotent on its `client_uuid` (a request that timed out after reaching the server is
  safely re-sent). An item the server rejects is marked **failed** with the server's reason and
  stays visible in the Sync tab; the server also raises a `SYNC_FAILED` alert, listed for supervisors
  under *Supervisor → Failed syncs*. The rest of the batch still applies. Synced
  items are kept 7 days as history. Pending items are never deleted, even on logout.
* Needs the server (clear message shown): amount overrides, selling a pass to a plate never seen
  before, supervisor screens. After an offline plate correction, the corrected plate's previous dues
  are not shown (they stay on its balance for the next visit).
* Times, zone windows and the overnight tariff rule use the site's `site_timezone` from bootstrap
  (fixed-offset zones; IST if unknown).

## Live updates

A WebSocket to `/ws?token=…` (auto-reconnect) keeps the To-collect list, UPI confirmation, cash
holdings, handovers and guard alerts live; lists are also polled every 30 s and support
pull-to-refresh.

## Code layout

```
lib/
  main.dart                 app + role-based routing
  api/                      REST client (typed models), WebSocket client
  domain/                   plates (normalise/correct/OCR extraction), UPI refs & URIs,
                            cash-limit maths, payment request payloads
  tariff/tariff.dart        Dart port of backend calculate_charge
  offline/                  SQLite store (queue + cache), SyncService
  state/                    AppState (login, bootstrap, connectivity, sync), To-collect model,
                            payment service (cash / offline UPI / receipts)
  screens/                  collect, collect_flow, upi_pay, receipt, pass_sell, search, vehicle,
                            cash, handover, shift (+ zone), sync_status, guard_alerts,
                            supervisor/ (home, holdings, handovers, disputes, zones, deposit)
  widgets/                  shared UI, dialogs (phone prompt, override + supervisor PIN),
                            plate scanner (camera + ML Kit OCR), denomination grid
test/                       tariff_test, upi_test, plates_test, offline_queue_test, widgets_test
```
