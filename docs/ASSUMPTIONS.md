# Assumptions and decisions

Decisions taken where the specification left room. All values marked *(setting)* are editable in
Dashboard → Configuration without code changes.

## Phase 1 — data model, tariff, ledger, passes, matching
1. **Money in paise** (integers) end to end; no floating point in any amount.
2. **Tariff rounding**: "each started hour after a grace period" is implemented as: the first slab
   covers `first_slab_minutes + grace`; after that each hour is charged once more than `grace`
   minutes of it have elapsed (2 h 10 m → ₹10, 2 h 11 m → ₹15, 3 h 10 m → ₹15, 3 h 11 m → ₹20).
   The ₹30 cap applies per 12-hour block; each new block restarts the slab; a remainder within the
   grace after a full block is free.
3. **A zero-length stay costs the first slab** (a vehicle that enters and immediately leaves is
   still a movement); a free period can be configured with `free_minutes`.
4. **Tariff at entry applies** to the whole stay; tariff changes are new versions with a future
   effective date (past dates are rejected by the API).
5. **Duration buttons** quote from the entry time (not from the moment of collection): a customer
   who says "4 hours" at 08:20 after entering at 08:05 pays for 08:05–12:05.
6. **Quote = tariff estimate + current balance** (positive balance = previous dues, shown as a
   separate line; a credit reduces the amount, never below zero).
7. **Exit alert** is red when the exiting session was charged but nothing was paid for it, or when
   the balance after exit exceeds `alert_balance_threshold_paise` *(setting, default ₹20)* — so a
   customer who paid for 2 h and stayed 4 h leaves green with ₹10 carried forward, while one who paid
   nothing leaves red. Offline UPI claims count as paid for the alert until reconciled.
8. **Approximate matching** at entry only merges an unknown plate into a known regular (pass holder
   or non-zero balance) when the difference is confusion-only (B/8, 0/O…) or when the read is doubtful
   (invalid format or confidence < `approx_regular_max_confidence` 0.9) — consecutive registrations
   (…1234 / …1235) are common and must stay distinct. At exit, open sessions are matched with
   Levenshtein ≤ `approx_tolerance` *(setting, 1)* after the confusion map.
9. **Re-homing**: if a confident exit read contradicts a low-confidence entry read that created a
   one-off vehicle, the session moves to the exit plate (balanced adjustment pair for payments). The
   same happens when a supervisor types the plate for an unread exit.
10. **Passes** start at local midnight on the purchase day (renewals start when the current pass
    ends); a monthly pass ends on the same date next month. A pass bought during a visit covers that
    day's stays, including ones that closed before an offline payment was confirmed.
11. **One-time visitor dues**: the defaulters report separates vehicles seen once and not for 7 days
    ("unrecovered one-time dues"); no recovery process.

## Phase 2 — ANPR
12. No trained model weights are shipped. The `ClassicalRecognizer` reads only the synthetic demo
    video; production uses `LocalOnnxRecognizer` with models trained on site footage (scripts
    provided) or the commercial Plate Recognizer adapter. See the licence table in `docs/ANPR.md`.
13. `gate_span` (which fraction of the gate width each camera covers) is an ANPR-side camera setting
    used to merge unread detections across the two cameras.
14. Late reads arriving after an event was emitted are suppressed (the event contract has no update
    call).

## Phase 4 — payments and cash
15. **Payment gateway**: Razorpay (QR Codes API: dynamic single-use UPI QR, `qr_code.credited`
    webhook, settlement recon API, refunds). Any other gateway fits the `PaymentGateway` interface.
16. **UPI transaction reference** `P{S|P|V}{base36 id}X{6 hex}` encodes the session, pass or vehicle so
    that bank-statement lines can be matched even for offline intent QRs.
17. **Cash-in-hand is per shift**: collected cash in the shift minus cash counted by the supervisor
    at handovers. A handover's variance is `counted − declared`; cash still held at shift close is
    recorded as a negative shift variance and requires a note.
18. Cash recorded offline on a phone that had already reached the limit is accepted on sync (money
    was physically taken) but flagged `limit_breach` and shows in the worker report.
19. Workers can correct a plate only on a session without payments; otherwise a supervisor fixes it.
20. Dispute attribution: the worker who took a payment for the session if any, else the worker
    assigned to the session's zone at entry time; zone is the entry gate's zone unless the worker
    tags a parked location.
21. SMS via MSG91 Flow API (DLT template ids per message type), WhatsApp via Meta Cloud API
    templates named `parking_<type>`; both queued and retried; WhatsApp failures fall back to SMS.

## Phase 6 — relay
22. The edge server initiates all sync (outbound HTTPS only) — no inbound ports are opened at the site.
23. The relay stores only masked/blurred data for public listing, phone numbers as salted hashes, and
    balance history only for vehicles with a verified phone.

## Operations
24. The backend runs as **one** process (uvicorn, async + thread pool) so the WebSocket hub and job
    scheduler are in-process; measured capacity is far above the site's peak (see ARCHITECTURE §6).
25. Both gates can be set to `BOTH`; the simulator does so. The seed data sets Gate 1 = IN, Gate 2 = OUT.
