/// UPI transaction references and intent URIs, matching the backend exactly so
/// that offline claims made on the phone can be reconciled by `txn_ref`.
///
/// * `backend/app/domain/payments.py::make_txn_ref` -> `P{S|P|V}{base36 id}X{6 hex}`
/// * `backend/app/adapters/gateway.py::upi_intent_uri`
library;

import 'dart:convert';
import 'dart:math';

const String _b36Chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';

/// Upper-case base-36 of a non-negative integer (Python `_b36`).
String base36(int n) {
  if (n < 0) throw ArgumentError('negative id');
  var out = '';
  do {
    out = _b36Chars[n % 36] + out;
    n ~/= 36;
  } while (n > 0);
  return out;
}

/// What a UPI reference pays for: S = parking session, P = pass, V = vehicle (dues / pass bought offline).
enum TxnKind { session, pass, vehicle }

String _kindCode(TxnKind k) => switch (k) {
      TxnKind.session => 'S',
      TxnKind.pass => 'P',
      TxnKind.vehicle => 'V',
    };

/// `P{kind}{base36(id)}X{6 upper hex}`, e.g. `PS2N9XA1B2C3`.
String makeTxnRef(TxnKind kind, int id, {Random? random}) {
  final rnd = random ?? Random.secure();
  final hex = List.generate(3, (_) => rnd.nextInt(256).toRadixString(16).padLeft(2, '0')).join().toUpperCase();
  return 'P${_kindCode(kind)}${base36(id)}X$hex';
}

final RegExp _refRe = RegExp(r'^P([SPV])([0-9A-Z]+)X[0-9A-F]+$');

/// Inverse of [makeTxnRef]: returns (kind letter, id) or null (Python `parse_txn_ref`).
(String, int)? parseTxnRef(String? ref) {
  final m = _refRe.firstMatch(ref ?? '');
  if (m == null) return null;
  return (m[1]!, int.parse(m[2]!, radix: 36));
}

/// Python `urllib.parse.quote(s)` with its default `safe='/'`: keeps
/// `A-Z a-z 0-9 _ . - ~ /` and percent-encodes every other UTF-8 byte (upper hex).
String pyQuote(String s) {
  final sb = StringBuffer();
  for (final b in utf8.encode(s)) {
    final isAlnum = (b >= 0x30 && b <= 0x39) || (b >= 0x41 && b <= 0x5A) || (b >= 0x61 && b <= 0x7A);
    if (isAlnum || b == 0x5F || b == 0x2E || b == 0x2D || b == 0x7E || b == 0x2F) {
      sb.writeCharCode(b);
    } else {
      sb.write('%${b.toRadixString(16).toUpperCase().padLeft(2, '0')}');
    }
  }
  return sb.toString();
}

/// Rupees with two decimals from integer paise, e.g. 1050 -> "10.50".
String paiseToAmount(int paise) {
  final neg = paise < 0;
  final p = paise.abs();
  return '${neg ? '-' : ''}${p ~/ 100}.${(p % 100).toString().padLeft(2, '0')}';
}

/// Standard UPI deep link (NPCI linking spec), identical to the backend's.
String upiIntentUri({
  required String vpa,
  required String payee,
  required int amountPaise,
  required String txnRef,
  String note = 'Parking',
}) =>
    'upi://pay?pa=${pyQuote(vpa)}&pn=${pyQuote(payee)}&am=${paiseToAmount(amountPaise)}&cu=INR'
    '&tr=${pyQuote(txnRef)}&tn=${pyQuote(note)}';
