# Installation

Order on site: network + NTP → edge server → cameras (pilot gate first) → exit alert units →
phones → cloud relay → go-live checks. Section 1 is a laptop demo that needs none of the hardware.

Network plan used in the examples (adjust to your site):

| Device | Address |
|---|---|
| Edge server | 192.168.10.10 (static) |
| ANPR cameras G1-L, G1-R, overview G1-O | 192.168.10.11–13 |
| ANPR cameras G2-L, G2-R, overview G2-O | 192.168.10.21–23 |
| Exit alert units AU-G1, AU-G2 | 192.168.10.31–32 (DHCP reservation) |
| Worker phones | Wi-Fi DHCP, 192.168.20.0/24 |

---

## 1. Laptop demo (no cameras, no Pi, no gateway)

Requirements: Python 3.11+ (on Windows: inside WSL). Nothing else — the dashboard and phone app are
downloaded ready-made.

```bash
./deploy/demo.sh --phone
```
Scan the printed QR code with any phone (iPhone or Android) to open the worker app; log in as
w1 / PIN 1111. On the QR screen of a UPI payment, tap *Simulate customer paying (demo)*.
This builds the dashboard, starts the backend on SQLite in demo mode with the mock UPI gateway and
demo users, generates synthetic gate video, and replays it through the ANPR service in real time
(entries at Gate 1, exits at Gate 2). Open http://localhost:8000 — admin/admin123 (dashboard),
sup1/super123. Optional extras shown by the script: the alert-unit window for Gate 2
(`python -m exit_alert --windowed --mock-gpio …`) and the customer relay on :8080.

Things to try: pay for a vehicle from the worker flow (`POST /api/payments/upi`, then
`POST /api/demo/pay/{id}`), switch the gateway off (`POST /api/demo/gateway {"online":false}`) to see
the offline UPI path, run the full-day simulator (`cd backend && python -m sim.simulator`).

## 1b. Rehearsal install on any PC (Windows + WSL2 is fine)

This is the **real installer** (same Docker images, database, HTTPS gateway, phone app, ANPR with
our plate models, backups) with three differences: demo videos loop as the cameras, payments and
SMS are pretend, and demo staff logins are added. Use it to practise the install and to try the
phone app over Wi-Fi before the site PC arrives. Nothing here touches Windows itself.

**Windows 11 only, once — let phones on your Wi-Fi reach WSL2** ("mirrored" networking):
1. In Windows, open Notepad, paste the two lines below and save as `%UserProfile%\.wslconfig`
   (File name `".wslconfig"` with the quotes, Save as type *All files*):
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
2. PowerShell **as administrator** (lets the phone connect to WSL2; run once):
   ```powershell
   Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
   wsl --shutdown
   ```
3. Open Ubuntu again. `hostname -I` should now print your PC's Wi-Fi address (e.g. `192.168.1.23`),
   not `172.x.x.x`.

**Install** (Ubuntu terminal; Docker Desktop with WSL integration, or none — the installer adds Docker):
```bash
cd ~/Car-parking-software && git pull
cd deploy && sudo ./install.sh --test     # 15–25 min the first time; asks for an admin name + password
sudo ./check.sh                           # every line should say PASS (a couple of WARN is fine)
```
Then:
* **PC**: open `http://<address>/site-ca.crt`, install it (§2a), then `https://<address>` → log in as your admin.
* **iPhone** on the same Wi-Fi: install the certificate (§2a), open `https://<address>/app` in Safari
  → Share → *Add to Home Screen*. Log in as worker `w1`, PIN `1111`.
* Within a minute, bikes enter at Gate 1 and leave at Gate 2 every ~80 s; collect a fee in the app
  (the pretend gateway marks UPI as paid), take cash, hand over a shift, look at the dashboard.

Stop / start: `cd deploy && sudo docker compose stop` / `sudo docker compose up -d`.
Remove everything: `sudo docker compose down && sudo rm -rf /srv/parking deploy/.env deploy/config/site.yaml`.

---

## 2. Edge server

Hardware: 8-core CPU, 32 GB RAM, 1 TB NVMe (OS + DB), 4 TB HDD (images), dual NIC, online UPS,
dual WAN (broadband + 4G/5G failover router). A GPU is **optional**: our plate models run on the
CPU. (It is only used by the alternative `onnx` engine; the installer enables it when `nvidia-smi` works.)

1. Install **Ubuntu 24.04 LTS Server**. Set the static IP, hostname `parking-edge`, timezone
   `Asia/Kolkata`. Only with a GPU: install the NVIDIA driver (`sudo ubuntu-drivers install`, reboot, check `nvidia-smi`).
2. Mount the 4 TB HDD at `/mnt/images` and the external backup disk at `/mnt/backup` (add to
   `/etc/fstab` with `nofail`).
3. Clone this repository to `/opt/parking` and run the installer:
   ```bash
   sudo git clone <repo-url> /opt/parking && cd /opt/parking/deploy
   sudo ./install.sh          # writes deploy/.env with generated secrets, builds, starts, asks for the admin
   sudo nano .env             # gateway + SMS credentials, storage paths, backup settings
   sudo ./install.sh          # re-run after editing .env (safe: keeps data, secrets and the admin)
   sudo ./check.sh            # health check incl. a real backup + restore test: all PASS before go-live
   ```
   The installer sets up Docker, the phone app, the NVIDIA container toolkit (only with a GPU), **chrony as the site NTP server**,
   the firewall (API and NTP open to the LAN only), nightly backups (02:30) and a weekly automated
   restore test (Sunday 04:00), and a systemd unit (`parking.service`) that starts the stack at boot.
4. Install the site certificate on your PC (§2a), open `https://192.168.10.10`, log in as the admin, then in **Configuration**:
   * Site settings: lot name, GSTIN (if any), UPI VPA and payee name (used by the offline QR),
     receipt footer, cash limit, cash on/off, alert threshold, retention.
   * Tariffs and pass prices (defaults are seeded: ₹10 / 2 h, ₹5 per extra hour, ₹30 cap per 12 h,
     10 min grace; monthly pass ₹500; car class disabled).
   * Gates: direction per gate and time-of-day schedule; cameras (RTSP URLs, ROI, capture line,
     IN vector) — see [CAMERA_SETUP.md](CAMERA_SETUP.md). **The camera addresses entered here win over
     `deploy/config/site.yaml`** (the seeded ones are placeholders: replace them with your cameras').
   * Zones, users (workers, guards, supervisors with PINs).
5. Payment gateway (Razorpay): create API keys, enable **QR Codes**, add a webhook to
   `https://<relay-domain>/webhooks/razorpay` for the relay and — if the edge is reachable over a VPN/tunnel —
   `https://<edge-public>/api/payments/webhook` with events `qr_code.credited`, `payment.captured`,
   `payment.failed`; put the webhook secret in `.env`. Without an inbound path to the edge, payments
   are still confirmed by status polling and the 10-minute settlement reconciliation.
6. SMS (MSG91): register DLT templates for receipt, settlement, pass reminder and OTP; put the flow
   template ids in `.env`. WhatsApp (optional): Meta Cloud API token and phone number id; approve
   templates named `parking_receipt`, `parking_settlement`, `parking_pass_reminder`.

Useful commands:
```bash
cd /opt/parking/deploy
docker compose ps                     # health of db / backend / anpr
docker compose logs -f backend anpr
sudo systemctl restart parking
./backup.sh && ./verify_backup.sh     # manual backup + restore test
```

### 2a. Site HTTPS (secure connections on the lot Wi-Fi)

Everything on the LAN talks to the edge server over HTTPS through the gateway container (Caddy):
`https://<edge IP>` for the dashboard, `https://<edge IP>/app` for the phone app, and the same for the
exit alert units. The certificate is issued by the site's own certificate authority, created on first
start ("Station Parking Site CA"). Each device trusts it **once**:

| Device | Steps |
|---|---|
| **iPhone** | Safari → `http://<edge IP>/site-ca.crt` → *Allow* → Settings → *Profile Downloaded* → Install. Then Settings → General → About → **Certificate Trust Settings** → turn on "Station Parking Site CA". |
| **Android** | Chrome → `http://<edge IP>/site-ca.crt` → it downloads → Settings → Security → *Encryption & credentials* → **Install a certificate → CA certificate** → pick the file. |
| **Windows PC** (supervisor desk) | Download `http://<edge IP>/site-ca.crt` → double-click → *Install Certificate* → Local Machine → **Trusted Root Certification Authorities**. |
| **Exit alert unit** | `install.sh … --edge-url https://<edge IP> --ca-url http://<edge IP>/site-ca.crt` (§4). |

Notes:
* The CA lives in `CADDY_DATA_DIR` (default `/srv/parking/caddy`) and is included in the nightly backup
  (`site-ca-*.tgz`). If it is lost, every device must trust the new one.
* Only `/site-ca.crt` is served over plain HTTP; every other HTTP request is redirected to HTTPS. Port
  8000 is no longer reachable from the LAN.
* To use certificates from your own or a public CA instead, put `site.crt`/`site.key` in
  `CADDY_CERTS_DIR` and change `tls internal` to `tls /certs/site.crt /certs/site.key` in
  `deploy/caddy/Caddyfile`.

---

## 3. Cameras

1. Mount per [CAMERA_SETUP.md](CAMERA_SETUP.md) (2.5–3 m, 5–7 m behind the capture line, ~30 %
   overlap between the left and right camera, shutter ≤ 1/1000 s, IR on, WDR on).
2. In each camera's web UI: static IP, NTP server = edge server, H.264/H.265 25 fps main stream,
   an RTSP user with view-only rights.
3. Put the RTSP URLs in Dashboard → Configuration → Cameras (or `deploy/config/site.yaml`), set the
   ROI, capture line and IN vector, and `gate_span` (fraction of the gate width each camera covers,
   used to merge unread detections) in `site.yaml`.
4. Load recognition models: copy the ONNX detector/OCR models to `/srv/parking/models` and select
   the engine in `site.yaml` (`onnx`, or `platerecognizer` with an API key) — see
   [ANPR.md](ANPR.md). Restart: `docker compose restart anpr`.
5. Pilot one gate for two weeks; measure read rate with `python -m anpr_service evaluate` on labelled
   clips and on the dashboard's ANPR accuracy report (targets: ≥ 95 % day, ≥ 90 % night).

---

## 4. Exit alert unit (Raspberry Pi 5, one per gate)

Hardware: Pi 5 (4 GB) + official PSU + 64 GB high-endurance microSD, 24–32" high-brightness display
(HDMI), 24 V tower light with buzzer, 2-channel relay module, 24 V PSU. Wiring:
[alert_unit/README.md](../alert_unit/README.md#hardware-and-wiring).

1. Flash **Raspberry Pi OS Lite (64-bit, Bookworm)**, enable SSH, set hostname `au-g2`.
2. Copy `alert_unit/` to the Pi and run:
   ```bash
   sudo ./install.sh --edge-url https://192.168.10.10 --ca-url http://192.168.10.10/site-ca.crt \
        --key <PARK_DEVICE_API_KEY> --gate G2 --ntp 192.168.10.10
   ```
   It installs the packages, writes `/etc/alert-unit/config.yaml`, points NTP at the edge server,
   disables screen blanking and enables the `alert-unit` systemd service (fullscreen kiosk).
3. Check on the dashboard (Live → devices) that `AU-G2` is online. Drive a test exit in demo mode or
   by riding a test bike through the gate.

---

## 5. Worker phones

**Any phone, no install (iPhone or Android):** install the site certificate (§2a), then open `https://<edge server>/app` on the lot Wi-Fi
and add it to the home screen. It talks to the server it was opened from.

**Android app (best offline behaviour and camera plate scan):** download `parking-worker.apk` from the
GitHub release `demo-latest` (built automatically), or build it yourself:
   ```bash
   cd worker_app && flutter pub get && flutter test && flutter build apk --release
   ```
   Release signing: [worker_app/README.md](../worker_app/README.md#release-signing).
2. On each phone: enable *Install unknown apps* for the file manager, install
   `app-release.apk`, grant camera permission, connect to the site Wi-Fi, set the phone's time to
   automatic.
3. Install the site certificate (§2a). First launch: the server URL defaults to `https://192.168.10.10`, log in with the user's username
   and PIN. **The account binds to that phone**; to move a user to a new phone, the admin resets the
   device binding (Dashboard → Configuration → Users).
4. Give each worker a lockable cash pouch; supervisors use the same app in supervisor mode.

---

## 6. Cloud relay (customer pages) — optional, not deployed by default

Skip this section unless you decide to offer customer self-service online. Without it, receipts are
self-contained (SMS text / on-screen QR) and passes are sold and renewed by workers in the app.

A small VM (1 vCPU, 1 GB) with a domain, e.g. `pay.example-parking.in`, behind Caddy or NGINX
with TLS. Details and environment variables: [customer_web/README.md](../customer_web/README.md).

1. Deploy the `customer_web` container (Dockerfile provided) with `RELAY_RELAY_API_KEY` = the edge's
   `PARK_RELAY_API_KEY`, Razorpay keys (same merchant account as the edge) and the MSG91 OTP template
   (all variables: `customer_web/.env.example`).
2. Point the Razorpay webhook for self-pay at `https://pay.example-parking.in/webhooks/razorpay`.
3. On the edge, set `PARK_RELAY_URL=https://pay.example-parking.in` and
   `PARK_PUBLIC_RECEIPT_BASE=https://pay.example-parking.in/r`, `PARK_PUBLIC_SITE_URL=https://pay.example-parking.in`
   in `deploy/.env`, then
   `docker compose up -d backend`. The edge pushes state every 15 s and pulls customer actions; no
   inbound port is needed at the site.
4. Print QR standees pointing to `https://pay.example-parking.in/` (self-pay) and `/pass`.

---

## 6a. Bring over existing customers

Before go-live, import your current monthly-pass holders, regular customers and any unpaid dues from the
old register: Dashboard → **Import customers** (admin). Download the template, fill it in (or export
from your old system and rename the columns), upload it, review the row-by-row check, then confirm.
Re-uploading the same file is safe. From a server shell the same import is
`docker compose exec backend python -m app.import_customers /path/file.xlsx [--commit]`.

## 7. Go-live checklist
- [ ] `sudo ./check.sh` in `deploy/`: every line PASS (it includes a real backup + restore test).
- [ ] All devices show the edge server's time (NTP) — check a camera OSD against the dashboard clock.
- [ ] Live page: 4 ANPR + 2 overview cameras streaming, 2 alert units online, internet + gateway green.
- [ ] Test ride in and out at each gate (single bike, two side by side, a two-line plate).
- [ ] Worker flow: UPI payment turns green; cash payment sends an SMS receipt; receipt link opens.
- [ ] Unplug the WAN: offline UPI QR works; plug back: the claim is confirmed by reconciliation.
- [ ] Unpaid exit: alert unit turns red, guard phone alerts, dispute from the alert works.
- [ ] Handover with photo, bank deposit entry, cash reconciliation for the day shows no gap.
- [ ] Existing pass holders and dues imported (§6a); a test pass holder enters without interaction.
- [ ] Backup log shows last night's backup; run `./verify_backup.sh` once by hand.
- [ ] Signage installed (tariff, ANPR/DPDP notice, cash-receipt notice) — texts in [PRIVACY.md](PRIVACY.md).

## 8. Restore from backup
```bash
cd /opt/parking/deploy
docker compose stop backend anpr                      # ANPR buffers nothing while stopped; do it off-peak
./restore.sh /mnt/backup/parking/parking-YYYYMMDD-HHMMSS.dump      # type YES to overwrite 'parking'
docker compose start backend anpr
```
The encrypted cloud copies are decrypted with `age -d -i <key> file.dump.age > file.dump`.
