// The offline UPI QR must use exactly the backend's txn-ref format
// (payments.make_txn_ref / parse_txn_ref) and intent URI
// (gateway.upi_intent_uri) so reconciliation can match claims by reference.
// Expected strings below were produced by the Python backend.
import 'dart:math';

import 'package:flutter_test/flutter_test.dart';
import 'package:parking_worker/domain/pay_request.dart';
import 'package:parking_worker/domain/upi.dart';

void main() {
  test('base36 matches Python _b36', () {
    expect(base36(0), '0');
    expect(base36(35), 'Z');
    expect(base36(36), '10');
    expect(base36(123456789), '21I3V9');
  });

  test('txn ref format P{S|P|V}{base36}X{6 hex} and round trip', () {
    final re = RegExp(r'^P([SPV])([0-9A-Z]+)X[0-9A-F]{6}$');
    for (final id in [1, 35, 3429, 987654321]) {
      final ref = makeTxnRef(TxnKind.session, id);
      expect(re.hasMatch(ref), isTrue, reason: ref);
      expect(parseTxnRef(ref), ('S', id));
    }
    expect(parseTxnRef(makeTxnRef(TxnKind.vehicle, 42)), ('V', 42));
    expect(parseTxnRef(makeTxnRef(TxnKind.pass, 7)), ('P', 7));
    expect(makeTxnRef(TxnKind.session, 3429, random: Random(1)).startsWith('PS2N9X'), isTrue);
  });

  test('parse_txn_ref parity', () {
    expect(parseTxnRef('PS2N9XA1B2C3'), ('S', 3429));
    expect(parseTxnRef('PS2N9XA1B2C3-1F2E'), isNull); // suffixed duplicates are not parseable server-side either
    expect(parseTxnRef('garbage'), isNull);
    expect(parseTxnRef(null), isNull);
  });

  test('pyQuote = urllib.parse.quote(safe="/")', () {
    expect(pyQuote('parking@upi'), 'parking%40upi');
    expect(pyQuote('Station Parking'), 'Station%20Parking');
    expect(pyQuote('a-b_c.d~e/f'), 'a-b_c.d~e/f');
    expect(pyQuote('Rāj & Sons / Lot'), 'R%C4%81j%20%26%20Sons%20/%20Lot');
  });

  test('upi intent URI identical to backend', () {
    expect(
      upiIntentUri(vpa: 'parking@upi', payee: 'Station Parking', amountPaise: 4500, txnRef: 'PS2N9XA1B2C3', note: 'Parking MH43AB1234'),
      'upi://pay?pa=parking%40upi&pn=Station%20Parking&am=45.00&cu=INR&tr=PS2N9XA1B2C3&tn=Parking%20MH43AB1234',
    );
    expect(
      upiIntentUri(vpa: 'stn.parking-1@okicici', payee: 'Rāj & Sons / Lot', amountPaise: 1005, txnRef: 'PV0XFFFFFF'),
      'upi://pay?pa=stn.parking-1%40okicici&pn=R%C4%81j%20%26%20Sons%20/%20Lot&am=10.05&cu=INR&tr=PV0XFFFFFF&tn=Parking',
    );
  });

  test('amount formatting', () {
    expect(paiseToAmount(0), '0.00');
    expect(paiseToAmount(5), '0.05');
    expect(paiseToAmount(123456), '1234.56');
  });

  group('PayRequest payloads', () {
    const target = CollectTarget(
        vehicleId: 9, plate: 'MH43AB1234', displayPlate: 'MH 43 AB 1234', vehicleClass: 'BIKE', sessionId: 77, duesPaise: 1500);

    test('session: txn ref encodes the session id', () {
      const r = PayRequest(
          purpose: PayPurpose.session, target: target, basePaise: 2000, duesPaise: 1500, creditPaise: 0, amountPaise: 3500, durationMinutes: 240);
      expect(parseTxnRef(r.newTxnRef()), ('S', 77));
      final d = r.syncData(phone: '9876543210', txnRef: 'PS25X000000');
      expect(d, {
        'session_id': 77,
        'vehicle_id': 9,
        'duration_minutes': 240,
        'dues_paise': 1500,
        'amount_paise': 3500,
        'phone': '9876543210',
        'txn_ref': 'PS25X000000',
      });
      // backend _offline_quote accepts amount == max(0, base + dues)
      expect(d['amount_paise'], r.basePaise + r.duesPaise);
      final body = r.paymentBody(clientUuid: 'u1');
      expect(body['purpose'], 'SESSION');
      expect(body['expected_amount_paise'], 3500);
      expect(body.containsKey('override_paise'), isFalse);
    });

    test('override fields only when the amount differs', () {
      const r = PayRequest(
          purpose: PayPurpose.session, target: target, basePaise: 2000, duesPaise: 0, creditPaise: 0, amountPaise: 2000, durationMinutes: 240);
      final o = r.withOverride(1000, 'regular customer', '4321');
      expect(o.payablePaise, 1000);
      final b = o.paymentBody(clientUuid: 'u2');
      expect(b['override_paise'], 1000);
      expect(b['override_reason'], 'regular customer');
      expect(b['supervisor_pin'], '4321');
      expect(r.withOverride(2000, 'x', '1').hasOverride, isFalse);
    });

    test('pass and dues use the vehicle id in the txn ref', () {
      const p = PayRequest(
          purpose: PayPurpose.pass, target: target, basePaise: 50000, duesPaise: 1500, creditPaise: 0, amountPaise: 51500, passTypeId: 3);
      expect(parseTxnRef(p.newTxnRef()), ('V', 9));
      expect(p.syncData()['pass_type_id'], 3);
      expect(p.syncData().containsKey('session_id'), isFalse);
      expect(p.passSellBody(mode: 'CASH', clientUuid: 'u')['vehicle_id'], 9);
      const newPlate = CollectTarget(vehicleId: 0, plate: 'KA01AB1234', displayPlate: 'KA 01 AB 1234', vehicleClass: 'BIKE');
      const p2 = PayRequest(
          purpose: PayPurpose.pass, target: newPlate, basePaise: 50000, duesPaise: 0, creditPaise: 0, amountPaise: 50000, passTypeId: 3);
      expect(p2.passSellBody(mode: 'UPI', clientUuid: 'u')['plate'], 'KA01AB1234');
    });
  });
}
