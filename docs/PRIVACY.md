# Privacy and data protection (DPDP Act, 2023)

The system processes personal data: vehicle registration numbers, mobile numbers, optional names,
and images of vehicles. This page lists what is stored, why, for how long, and how requests are
handled. It is not legal advice; have the notice text reviewed before printing.

## What is stored and why

| Data | Purpose | Retention (default, configurable) |
|---|---|---|
| Plate crops (per event) | Identify the vehicle, resolve review items and disputes | 90 days |
| Full frames and overview images | Evidence for disputes, camera tuning | 30 days |
| Plate number, entry/exit times | Charging for actual time, passes, carry-forward of dues | With the financial records |
| Mobile number (optional, given by the customer) | Digital receipts, balance messages, pass reminders, OTP | Until erased on request |
| Payments, ledger, receipts | Accounting and audit | As required by accounting law (typically 8 years) |
| Staff actions (audit log) | Accountability | With the financial records |

## Controls implemented
* **Retention purge** — daily job deletes plate crops after `retention_plate_images_days` and full
  frames after `retention_full_frames_days` (Dashboard → Configuration → Retention).
* **Role-based access to images** — images are served only to authenticated staff; alert units use a
  device key.
* **Public pages** (cloud relay) show **masked plates** (e.g. `MH43••••34`) and **blurred** plate
  thumbnails; balance history is shown only after OTP verification of the mobile number on record.
  Phone numbers are sent to the relay only as salted hashes.
* **Export** — Dashboard → Privacy → vehicle → *Export* downloads everything held about a vehicle
  (JSON).
* **Erasure** — *Erase* deletes the vehicle's images, name, notes and phone number (also from
  payments, receipts and the message log). Financial records keep the plate number because the law
  requires them to be kept; this is explained to the requester.
* Customers can file a data request on the relay's privacy page; it reaches the supervisor queue.

## Signage notice (both gates; English, Hindi, Marathi)

> **ANPR cameras in operation.** This parking lot uses cameras that read vehicle number plates at
> entry and exit to calculate parking charges on actual time. Images are kept for up to 90 days;
> payment records are kept as required by law. If you give your mobile number we use it only for
> receipts, balance messages and pass reminders. To see or delete your data, scan the QR code or
> ask the supervisor. Data Fiduciary: *<operator name, address, grievance officer contact>*.

> **एएनपीआर कैमरे चालू हैं।** इस पार्किंग में वास्तविक समय के अनुसार शुल्क की गणना के लिए प्रवेश और निकास पर
> कैमरे वाहन नंबर प्लेट पढ़ते हैं। चित्र अधिकतम 90 दिन रखे जाते हैं; भुगतान रिकॉर्ड कानून के अनुसार रखे जाते हैं।
> आपका मोबाइल नंबर केवल रसीद, बकाया संदेश और पास रिमाइंडर के लिए उपयोग होता है। अपना डेटा देखने या हटाने के लिए
> QR कोड स्कैन करें या सुपरवाइज़र से संपर्क करें।

> **एएनपीआर कॅमेरे सुरू आहेत.** या पार्किंगमध्ये प्रत्यक्ष वेळेनुसार शुल्क मोजण्यासाठी प्रवेश व बाहेर पडताना
> कॅमेरे वाहन क्रमांक पाटी वाचतात. छायाचित्रे जास्तीत जास्त 90 दिवस ठेवली जातात; पेमेंट नोंदी कायद्यानुसार ठेवल्या
> जातात. तुमचा मोबाइल क्रमांक फक्त पावती, थकबाकी संदेश आणि पास स्मरणपत्रासाठी वापरला जातो. तुमचा डेटा पाहण्यासाठी किंवा
> हटवण्यासाठी QR कोड स्कॅन करा किंवा पर्यवेक्षकाशी संपर्क साधा.

## Cash receipt notice (gates, exits, collection points, handover desk)

> **Paid cash? You must receive a digital receipt. No receipt means your payment is not recorded.**
>
> **नकद भुगतान किया? आपको डिजिटल रसीद ज़रूर मिलनी चाहिए। रसीद नहीं, तो भुगतान दर्ज नहीं।**
>
> **रोख पैसे दिले? तुम्हाला डिजिटल पावती मिळालीच पाहिजे. पावती नाही म्हणजे पेमेंट नोंदवले नाही.**

Recommended size ≥ 600 × 900 mm at gates and exits, A3 at collection points; reflective or backlit.
