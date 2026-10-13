// Backend extensions: receipt codes made on the phone, site timezone, public
// receipt base, last_plate_image, pass price check, claim of a server-made QR.
import 'dart:math';

import 'package:flutter_test/flutter_test.dart';
import 'package:parking_worker/api/models.dart';
import 'package:parking_worker/domain/pay_request.dart';
import 'package:parking_worker/domain/upi.dart';
import 'package:parking_worker/offline/local_store.dart';
import 'package:parking_worker/tariff/tariff.dart';

void main() {
  test('receipt codes use the backend alphabet (8 chars, no 0/1/I/O/l)', () {
    final rnd = Random(3);
    for (var i = 0; i < 500; i++) {
      final c = newReceiptCode(random: rnd);
      expect(isValidReceiptCode(c), isTrue, reason: c);
      expect(RegExp(r'[01IOl]').hasMatch(c), isFalse);
    }
    expect(isValidReceiptCode('abc'), isFalse);
    expect(isValidReceiptCode('0BCDEFGH'), isFalse);
    expect(receiptCodeAlphabet, '23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz');
  });

  test('bootstrap: site timezone and receipt link base', () {
    final b = Bootstrap({
      'user': {'id': 1, 'username': 'w', 'role': 'WORKER'},
      'site_timezone': 'Asia/Kolkata',
      'public_receipt_base': 'https://pay.example-parking.in/r/',
      'settings': <String, dynamic>{},
    });
    expect(b.siteTimezone, 'Asia/Kolkata');
    expect(b.receiptLink('Ab3dEf7h'), 'https://pay.example-parking.in/r/Ab3dEf7h');
    final old = Bootstrap({
      'user': {'id': 1},
      'settings': <String, dynamic>{},
    });
    expect(old.receiptLink('Ab3dEf7h'), isNull);
    expect(old.siteTimezone, isNull);
  });

  test('timezone lookup falls back to IST', () {
    expect(tzOffsetFor('Asia/Kolkata'), istOffset);
    expect(tzOffsetFor('Asia/Kathmandu'), const Duration(hours: 5, minutes: 45));
    expect(tzOffsetFor('UTC'), Duration.zero);
    expect(tzOffsetFor(null), istOffset);
    expect(tzOffsetFor('Mars/Olympus'), istOffset);
    expect(isKnownZone('Mars/Olympus'), isFalse);
    // overnight rule follows the site zone: 22:00 UTC + 3 h crosses UTC midnight
    const t = Tariff(overnightPaise: 2000);
    final e = DateTime.utc(2026, 3, 10, 22);
    expect(calculateCharge('BIKE', e, e.add(const Duration(hours: 3)), t, tzOffset: Duration.zero), 1500 + 2000);
    expect(calculateCharge('BIKE', e, e.add(const Duration(hours: 3)), t), 1500); // 03:30-06:30 IST
  });

  test('vehicle thumbnail prefers last_plate_image', () {
    final v = VehicleInfo({
      'id': 1,
      'plate': 'MH43AB1234',
      'last_plate_image': '/api/images/a.jpg',
      'open_session': {
        'id': 2,
        'status': 'OPEN',
        'entry_images': {'plate_crop': '/api/images/b.jpg'},
      },
    });
    expect(v.plateImage, '/api/images/a.jpg');
    final w = VehicleInfo({
      'id': 1,
      'last_plate_image': null,
      'open_session': {
        'id': 2,
        'entry_images': {'plate_crop': '/api/images/b.jpg'},
      },
    });
    expect(w.plateImage, '/api/images/b.jpg');
  });

  group('payloads', () {
    const target = CollectTarget(
      vehicleId: 9,
      plate: 'MH43AB1234',
      displayPlate: 'MH 43 AB 1234',
      vehicleClass: 'BIKE',
      sessionId: 77,
    );

    test('pass sale sends expected_amount_paise (price + dues)', () {
      const p = PayRequest(
        purpose: PayPurpose.pass,
        target: target,
        basePaise: 50000,
        duesPaise: 1500,
        creditPaise: 0,
        amountPaise: 51500,
        passTypeId: 3,
      );
      expect(p.passSellBody(mode: 'UPI', clientUuid: 'u')['expected_amount_paise'], 51500);
      expect(p.passSellBody(mode: 'CASH', clientUuid: 'u', receiptCode: 'Ab3dEf7h')['receipt_code'], 'Ab3dEf7h');
    });

    test('offline CASH carries the phone-made receipt code', () {
      const r = PayRequest(
        purpose: PayPurpose.session,
        target: target,
        basePaise: 2000,
        duesPaise: 0,
        creditPaise: 0,
        amountPaise: 2000,
        durationMinutes: 240,
      );
      expect(r.syncData(receiptCode: 'Ab3dEf7h')['receipt_code'], 'Ab3dEf7h');
      expect(r.syncData().containsKey('receipt_code'), isFalse);
      final online = r.paymentBody(clientUuid: 'u1', receiptCode: 'Ab3dEf7h');
      expect(online['receipt_code'], 'Ab3dEf7h');
      expect(online['client_uuid'], 'u1');
      expect(r.paymentBody(clientUuid: 'u1').containsKey('receipt_code'), isFalse);
    });

    test('new sync type names match the backend', () {
      expect(
        [SyncType.alertAck, SyncType.plateCorrection, SyncType.shiftOpen, SyncType.shiftClose],
        ['ALERT_ACK', 'PLATE_CORRECTION', 'SHIFT_OPEN', 'SHIFT_CLOSE'],
      );
    });
  });
}
