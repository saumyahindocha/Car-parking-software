# Daily operations

Roles: **Admin** (configuration, users, audit), **Supervisor** (zones, handovers, review queue,
disputes, overrides, bank deposit), **Worker** (collections in an assigned zone), **Guard** (exit
alerts, dispute capture). Align local SOP wording with this document; the system enforces the
controls marked 🔒.

## Before opening (supervisor, ~10 min)
1. Dashboard → **Live**: all cameras green (fps, last frame < 60 s), both exit alert units online,
   internet and payment gateway status green. A red camera tile means a stalled stream — check power/
   PoE and the camera's web UI; the watchdog also raises a `CAMERA_STALL` alert.
2. Dashboard → **Review**: clear anything left overnight (unread plates, orphan sessions, offline
   UPI claims older than 24 h, open disputes).
3. **Assign zones** for the shift (Dashboard → Configuration → Zones, or supervisor mode in the app):
   one worker per zone per time window. 🔒 Unpaid sessions and disputes are attributed through these
   assignments, so they must match who is actually on the floor.
4. Check the handover desk: note counting machine, CCTV view of the desk, drop safe locked.

## Worker shift
1. Log in on **your own phone** (accounts are bound to one phone 🔒). Open shift — opening cash is
   always zero 🔒.
2. **To collect** list shows vehicles that entered without paying, newest first. Previous dues are in
   red. For each customer (target < 20 s):
   * Tap the vehicle (or search / scan the plate). **Compare the plate on the bike with the app** and
     correct it if the camera misread it (logged, reviewed by the supervisor).
   * Ask the expected duration and tap the button. The amount comes from the tariff; you cannot type
     an amount 🔒 (an exception needs a reason and the supervisor's PIN on your phone).
   * **UPI first**: show the QR; the screen turns green when the payment is confirmed. If the app shows
     *offline QR*, wait for the customer's success screen, then tap *Customer shows success*.
   * **Cash**: tap *Cash received ₹X*. Always ask for the mobile number for the SMS receipt; if the
     customer declines, the receipt QR is shown full-screen and must be scanned before you continue 🔒.
   * Offer the monthly pass to vehicles marked *pass candidate*.
3. **Cash in hand**: the app shows it against the limit (default ₹2,000). At 80 % you get a warning; at
   the limit cash is blocked (UPI still works) until you hand over 🔒.
4. **Handover** at the fixed desk only (under CCTV): enter the denomination count in the app; the
   supervisor counts with the machine, enters their count and takes a photo 🔒. Any difference needs
   the supervisor's note and is recorded against your shift.
5. **Close shift**: hand over all cash first. The app shows UPI and cash totals; a shift cannot close
   while a handover is pending, and cash still held is recorded as a variance 🔒.
6. Offline: collections continue. Cash and offline UPI claims are queued on the phone and sync
   automatically; watch the sync indicator and do not uninstall the app or clear its data.

## Guard
* The phone and the exit display alert on vehicles leaving unpaid or with dues above the threshold
  (red light, short buzzer). **Never stop traffic** — note the vehicle; dues are recovered at its
  next entry.
* If the customer says they paid (cash or UPI), tap **Customer says paid** on the alert. The dispute
  is linked automatically to the worker on duty in that zone.

## Supervisor during the day
* Review queue (keyboard shortcuts in the dashboard): unread plates (type from the image), ambiguous
  exits (pick the session), worker corrections (confirm), orphan sessions (charge with an estimated
  time or waive with a note), offline UPI claims (confirm with UTR or fail), disputes.
* Cash control: live cash-in-hand per worker, pending handovers, collected vs handed over.
* Overrides, cash reversals and UPI refunds: supervisor only, reason mandatory 🔒; all appear in the
  daily overrides report and the audit log.

## End of day (supervisor)
1. All workers' shifts closed; no pending handovers.
2. Dashboard → Cash control → **Cash reconciliation** for today: collected = handed over. Investigate
   any gap before leaving.
3. Count the safe, deposit at the bank next morning, record the **bank deposit** with slip reference
   and photo. When the bank statement shows the credit, mark it credited — the reconciliation then
   ties cash from system → handover → deposit → bank.
4. **UPI reconciliation** runs automatically every 10 minutes; check Reports → UPI reconciliation
   for mismatches, unknown credits or missing settlements.
5. Glance at the **worker comparison** report. One dispute is noise; repeated upheld/unresolved
   disputes, high unpaid-in-zone with low cash share, or cash shortages are flagged automatically.

## Weekly / monthly (admin)
* Reports: revenue (walk-in vs pass, UPI vs cash), sessions with no payment, defaulters (export),
  unrecovered one-time dues, pass sales and renewals, ANPR accuracy per camera, peak hours.
* Check backups: `/var/log/parking-backup.log` shows nightly backups and the Sunday restore test.
* Review users and device bindings; reset a binding when a phone is replaced.
* Tariff changes: create a **new version** with an effective date — past charges never change.

## Incidents
| Symptom | Action |
|---|---|
| Internet down | Nothing stops. UPI switches to offline QR (claims reconciled later), SMS queue waits. `INTERNET_DOWN` alert after 5 min. |
| Backend down | ANPR buffers events on disk and replays them in order; phones queue actions. Restart: `sudo systemctl restart parking`. |
| Camera stalled | `CAMERA_STALL` alert; the other camera at the gate still covers most of the width. Check PoE / camera. |
| Alert unit offline | Stays green (never stops traffic). Guard phones still get alerts. Power-cycle the Pi. |
| Phone lost | Admin disables the user or resets the device binding; cash held on that shift is visible in cash control. |
| Restore needed | `deploy/restore.sh <dump>` (stop backend first) — see INSTALL.md §8. |
