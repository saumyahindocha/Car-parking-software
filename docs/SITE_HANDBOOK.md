# Site handbook

Where the project stands, what to check on a site visit, and step-by-step setup of the site PC,
cameras, staff phones and exit alert units. The technical detail behind each step is in
[INSTALL.md](INSTALL.md), [CAMERA_SETUP.md](CAMERA_SETUP.md) and [ANPR.md](ANPR.md).

Addresses below assume the site server is `192.168.10.10`. If you choose another, use it everywhere.

---

## 1. Where we are

**Ready for a pilot install. Not yet ready to run unattended.** All the software is built and passes
its tests, and a full install has been rehearsed from scratch. Three things can only be proven on
site: how well it reads *real* plates, real UPI payments, and real SMS receipts. Run one gate as a
pilot, alongside the current way of working, before relying on it.

| Part | What it does | State |
|---|---|---|
| Central system (server + database) | Entries and exits, parking sessions, tariffs, monthly passes, dues, cash shifts and handovers, disputes, reports, audit log, privacy clean-up. Keeps working without internet. | Built · 87 tests pass |
| Dashboard (supervisor PC) | Live gates, review queue for unread plates, cash control, vehicle ledger, defaulters, reports, configuration, customer import. | Built |
| Phone app (workers, guards, supervisors) | One app; the login decides the screens. iPhone and Android in the browser, or the Android app. Keeps working when Wi-Fi drops. | Built · tried on iPhone |
| Number-plate reading | Our own models, trained on computer-generated plates. No paid service. Demo videos: 5 of 6 plates read correctly, no wrong plates. | Built · real plates not yet tested |
| Exit alert unit (Raspberry Pi at each exit) | Big screen + tower light when a bike leaves with dues. | Built · 31 tests · no real Pi yet |
| Installer, HTTPS, backups, health check | One-command install, secure phone connections, nightly backup with weekly restore test, `check.sh`. | Rehearsed end to end |
| UPI payments (Razorpay) | Worker shows a QR, payment turns green automatically; offline QR when the internet is down. | Built · needs a Razorpay account |
| SMS receipts (MSG91) | Digital receipt for every payment, including cash. | Built · needs MSG91 + DLT templates |
| GST invoices, Tally export | Only if the accountant needs them. | Not built |
| Alerts to the owner's phone | Camera offline, cash gap, disk filling up. | Not built |

**Plate reading today.** On computer-generated test plates it reads 78% of plates fully right from a
*single* frame, and 99.4% when it is confident. Each bike is seen in 5–15 frames by two cameras and the
system votes, so a passing bike is read more reliably. Real plates will be harder at first: run it for
a week on site, correct the plates it got wrong (about 2 hours), retrain (section 9). That is how it
reaches the target of **95% by day and 90% at night**. Until then, unread plates go to the review
queue and workers handle them in the app.

---

## 2. Site visit checklist

Photograph everything; bring back photos, videos and notes.

**Each gate**
- [ ] Gate width (two bikes side by side; cars later?).
- [ ] The **capture line**: where bikes are already moving straight and slowly, a few metres past the gate.
- [ ] The camera spot: **5–7 m behind** the capture line (cameras see the *rear* plate), beside the gate, **2.5–3 m high**. Wall, pillar or existing pole? Photo from there, phone held high.
- [ ] Sun direction: morning or evening sun straight into that spot is a problem.
- [ ] Night lighting at the gate.
- [ ] Power at the camera spot; cable route and distance to the server room (**over 90 m needs fibre**).
- [ ] Where a guard stands at each exit; where a big screen and tower light would be seen.
- [ ] Room for a rumble strip before the capture line.

**Test videos (most useful)**: lets us run our reader on real bikes before buying cameras.
- [ ] From the planned camera spot, phone high and steady, filming the **backs** of bikes leaving.
- [ ] Landscape, 1× lens, 1080p or 4K, 2–3 minutes at a busy time, some bikes side by side.
- [ ] One clip after dark if possible.
- [ ] Same at the second gate.

Then: `python anpr/tools/try_video.py <video>` lists the plates read and writes a contact sheet of the crops.

**Server room, network, Wi-Fi**
- [ ] Lockable, dry, ventilated spot for server, switch and UPS, with power and earthing.
- [ ] Fibre providers at the address; static IP possible?
- [ ] Mobile signal in the server room on two operators (backup internet).
- [ ] Walk the lot with a phone: note weak Wi-Fi/mobile spots (decides access point positions).
- [ ] Power cuts: how often, how long; inverter or generator?
- [ ] Cash handover desk position, with room for a drop safe and a camera above.

**Questions**
- [ ] Bikes per day, and in the busiest hour.
- [ ] Current tariff and monthly pass price.
- [ ] How pass holders and dues are recorded today (register, Excel); get a copy for the import tool.
- [ ] Workers per shift, shift times, supervisor, who handles cash.
- [ ] Staff phones: their own or bought? Android or iPhone?
- [ ] Bank account for UPI money (for Razorpay).
- [ ] Station authority rules on poles, signs and cameras.

---

## 3. Start these now (long lead times)

| Account | Why | Lead time |
|---|---|---|
| **Razorpay** business account | UPI QR payments that confirm automatically. KYC: PAN, bank account, business proof. Enable *QR Codes*. Test keys first. | 2–7 days |
| **MSG91** + **DLT registration** | SMS receipts. Sender and each template must be registered on a DLT portal (Jio, Vodafone Idea or Airtel). | 1–3 weeks |
| Business broadband + 4G/5G backup SIM | UPI confirmations and SMS need internet; recording entries and exits doesn't. | 1–2 weeks |

---

## 4. Hardware

The original hardware list stands with one change: **the server needs no graphics card**; our plate
models run on the processor. Buy for one gate first (the pilot), then copy it.

| Item | Pilot (1 gate) | Full site |
|---|---|---|
| Server PC: 8-core i7/Ryzen 7, 32 GB RAM, 1 TB NVMe + 4 TB HDD, 2 network ports. No GPU. | 1 | 1 (+1 spare later) |
| External USB backup disk (2–4 TB) | 1 | 1 |
| 4 MP ANPR cameras (1/1000 s shutter, IR, WDR, varifocal, PoE) | 2 | 4 + 1 spare |
| 4 MP wide overview camera | 1 | 2 |
| PoE+ switch, 8–16 ports, 150 W+ | 1 | 1 |
| Router with 4G/5G failover + outdoor Wi-Fi 6 access point | 1 + 1 | 1 + 2 |
| Online UPS 3 kVA, 60 min | 1 | 1 |
| Exit alert kit: Raspberry Pi 5 (4 GB) + PSU + 64 GB card, 24–32" bright screen, 24 V tower light + relay | 1 | 2 |
| Staff phones if provided: Android 12+, 4 GB RAM | 2–3 | 6 |

Before buying cameras, ask for a live demo at the gate: two bikes side by side, a two-row plate, after dark.

---

## 5. Setting up the site PC (1–2 hours, needs internet)

1. **Install Ubuntu 24.04 LTS Server** from a USB stick (balenaEtcher or Rufus): fixed address
   `192.168.10.10`, hostname `parking-edge`, tick *Install OpenSSH server*. Then:
   ```bash
   sudo timedatectl set-timezone Asia/Kolkata
   ```
2. **Disks**: image disk at `/mnt/images`, backup disk at `/mnt/backup` (find them with `lsblk`;
   replace `sdb1`/`sdc1`; a new disk first needs `sudo mkfs.ext4 /dev/sdX1`, which erases it):
   ```bash
   sudo mkdir -p /mnt/images /mnt/backup
   echo "UUID=$(sudo blkid -s UUID -o value /dev/sdb1) /mnt/images ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab
   echo "UUID=$(sudo blkid -s UUID -o value /dev/sdc1) /mnt/backup ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab
   sudo mount -a
   ```
3. **Get the software**:
   ```bash
   sudo apt update && sudo apt install -y git
   sudo git clone https://github.com/saumyahindocha/Car-parking-software /opt/parking
   cd /opt/parking/deploy
   ```
   Until this work is merged into `main`, add `-b claude/software-system-applications-8182vd` after `clone`.
4. **Install** (15–25 min; asks for the admin username and password). Installs Docker, database,
   server, HTTPS, phone app, plate reading, camera time sync, firewall, nightly backups, start at boot:
   ```bash
   sudo ./install.sh
   ```
5. **Payment and SMS keys** when available, then re-run (keeps all data). Until the Razorpay keys are
   in, UPI cannot work: collect cash only.
   ```bash
   sudo nano .env      # PARK_RAZORPAY_KEY_ID, PARK_RAZORPAY_KEY_SECRET, PARK_MSG91_AUTH_KEY, templates
   sudo ./install.sh
   ```
6. **Check**: every line should say PASS:
   ```bash
   sudo ./check.sh
   ```
7. **Supervisor PC**: open `http://192.168.10.10/site-ca.crt`, double-click, *Install Certificate →
   Local Machine → Trusted Root Certification Authorities*. Open `https://192.168.10.10`, log in as admin.
8. **Dashboard → Configuration**, tab by tab: *Settings* (lot name, UPI ID, receipt footer, cash
   limit) · *Tariffs* (defaults ₹10 for 2 h, ₹5 per extra hour, ₹30 cap per 12 h, 10 min grace) ·
   *Pass types* (monthly ₹500 default) · *Gates & cameras* · *Zones* · *Users & roles* (one user per
   person: name, username, 4-digit PIN; role Worker, Guard or Supervisor).
9. **Dashboard → Import customers**: existing pass holders and dues from Excel/CSV, with a preview.

Useful later:
```bash
cd /opt/parking/deploy
sudo ./check.sh --quick              # health check without the backup test
sudo docker compose logs -f anpr     # watch plate reading live
sudo systemctl restart parking       # restart everything
cd /opt/parking && sudo git pull && cd deploy && sudo ./install.sh   # update
```

To practise first on any PC (Windows + WSL2 works): `sudo ./install.sh --test`
([INSTALL.md §1b](INSTALL.md)). Demo videos act as cameras and payments are pretend.

---

## 6. Cameras

1. **Mount** 2.5–3 m high, 5–7 m behind the capture line, facing the backs of bikes. Left camera
   covers the left 65% of the gate, right camera the right 65% (overlap in the middle).
2. **In each camera's web page**: fixed address (gate 1: left `.11`, right `.12`, overview `.13`;
   gate 2: `.21`, `.22`, `.23`), NTP server `192.168.10.10`, shutter **1/1000 s** or faster and fixed,
   IR on, WDR on, main stream H.264/H.265 25 fps full resolution, a view-only user.
3. **Dashboard → Configuration → Gates & cameras**: each camera's stream (RTSP) address. These
   override `deploy/config/site.yaml`.
4. Take one still image per camera with a bike passing; set the reading area and capture line from
   it ([CAMERA_SETUP.md](CAMERA_SETUP.md)).
5. `sudo ./check.sh`: every camera reports in, bikes appear on the dashboard's Live page.

---

## 7. Staff phones

Everyone uses the same app; the login decides the screens. **Workers** collect fees, take cash, sell
passes, hand over shifts. **Guards** get live alerts when a bike leaves with dues. **Supervisors** see
cash holdings, handovers, deposits and disputes. Phones must be on the lot Wi-Fi.

**iPhone**
1. Join the lot Wi-Fi.
2. Once: Safari → `http://192.168.10.10/site-ca.crt` → *Allow*. Settings → *Profile Downloaded* →
   *Install*. Settings → General → About → *Certificate Trust Settings* → turn on **Station Parking Site CA**.
3. Safari → `https://192.168.10.10/app` → Share → **Add to Home Screen**; open it from the icon.
4. Log in with the username and PIN.

**Android**
1. Join the lot Wi-Fi.
2. Once: Chrome → `http://192.168.10.10/site-ca.crt` (downloads). Settings → search **"CA certificate"**
   (usually Security → Encryption & credentials → Install a certificate → CA certificate) → *Install
   anyway* → pick the file.
3. Chrome → `https://192.168.10.10/app` → ⋮ → **Add to Home screen** / *Install app*.
4. Log in with the username and PIN.

Optional for Android: the Android app (`parking-worker.apk` from the GitHub release `demo-latest`)
adds plate scanning with the phone camera. The browser version is simpler to keep up to date.

**Every phone**: automatic date and time; no VPN or iCloud Private Relay on this Wi-Fi; high
brightness (customers scan UPI QR codes off the screen); a power bank for double shifts. Guards keep
the alerts screen open with sound on.

**Test each phone**
- [ ] Log in: name and role show at the top.
- [ ] Worker: search a parked plate, see the amount due.
- [ ] Worker: ₹10 test cash payment appears on the dashboard's Cash page.
- [ ] Guard: a test exit with dues appears as an alert.
- [ ] Wi-Fi off for a minute, take a payment: it is sent when Wi-Fi returns (sync screen).

---

## 8. Exit alert units

1. Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager; enable SSH, hostname `au-g2`.
2. Wire screen, tower light and relay ([alert_unit/README.md](../alert_unit/README.md)).
3. Copy `alert_unit/` to the Pi and run (device key = `PARK_DEVICE_API_KEY` in the server's `deploy/.env`):
   ```bash
   sudo ./install.sh --edge-url https://192.168.10.10 \
     --ca-url http://192.168.10.10/site-ca.crt \
     --key <device key> --gate G2 --ntp 192.168.10.10
   ```
4. Dashboard → Live: `AU-G2` online. Test bike out with dues: screen red, light and buzzer, guard alert.

---

## 9. Pilot week and go-live

1. **Week 1, one gate**, alongside the current way of working. The supervisor clears the dashboard's
   *Review queue* daily.
2. **End of week 1**: copy the saved plate photos, correct the wrong reads (pre-filled; about 2 hours
   for 3,000), retrain the reader (30–60 min on the site PC): [ANPR.md §9.2](ANPR.md).
3. Tune camera angle, zoom and shutter until day reads reach 95% and night 90%; copy to gate 2.

**Go-live checklist**
- [ ] `sudo ./check.sh`: every line PASS (includes a real backup and restore test).
- [ ] Camera clocks match the dashboard clock.
- [ ] Test rides at each gate: single bike, two side by side, two-row plate, after dark.
- [ ] A real UPI payment turns green; a cash payment sends an SMS receipt.
- [ ] Internet unplugged: offline UPI QR works; plugged back: it confirms.
- [ ] Unpaid exit: alert screen red, guard's phone alerts.
- [ ] A shift handover and bank deposit balance on the Cash page.
- [ ] Pass holders imported; a pass holder rides in with nothing to pay.
- [ ] Signs up: tariff, camera and data notice, "Paid cash? You must receive a digital receipt".

---

## 10. What's left

| When | Work |
|---|---|
| After the site visit | Run the reader on the gate videos; adjust camera positions and hardware from the photos. |
| Razorpay test keys arrive | Test real UPI QR payments, refunds and the offline path end to end. |
| DLT templates approved | Test SMS receipts and pass reminders. |
| Install day | Camera reading areas from still images; confirm the PC keeps up with all cameras. |
| End of pilot week | Retrain the plate reader on real plates. |
| Optional | GST invoices and Tally export; alerts to the owner's phone. |
