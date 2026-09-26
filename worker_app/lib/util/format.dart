import 'package:intl/intl.dart';

import '../tariff/tariff.dart' show istOffset;

/// "₹1,250" (or "₹12.50" when there are paise).
String rupees(int? paise) {
  final p = paise ?? 0;
  final neg = p < 0;
  final a = p.abs();
  final whole = NumberFormat.decimalPattern('en_IN').format(a ~/ 100);
  final frac = a % 100;
  return '${neg ? '-' : ''}₹$whole${frac == 0 ? '' : '.${frac.toString().padLeft(2, '0')}'}';
}

/// Site-local (IST) wall-clock time for a UTC instant.
DateTime toIst(DateTime t) => t.toUtc().add(istOffset);

String timeIst(DateTime? t) => t == null ? '—' : DateFormat('HH:mm').format(toIst(t));

String dateTimeIst(DateTime? t) => t == null ? '—' : DateFormat('d MMM HH:mm').format(toIst(t));

String dateIst(DateTime? t) => t == null ? '—' : DateFormat('d MMM yyyy').format(toIst(t));

/// "2 h 15 m ago"-style age.
String ago(DateTime? t, {DateTime? now}) {
  if (t == null) return '—';
  final d = (now ?? DateTime.now()).difference(t);
  if (d.inMinutes < 1) return 'just now';
  if (d.inMinutes < 60) return '${d.inMinutes} m ago';
  if (d.inHours < 24) return '${d.inHours} h ${d.inMinutes % 60} m ago';
  return '${d.inDays} d ago';
}

String durationLabel(int minutes) {
  if (minutes >= 1440 && minutes % 1440 == 0) {
    return minutes == 1440 ? 'Full day' : '${minutes ~/ 1440} days';
  }
  if (minutes % 60 == 0) return '${minutes ~/ 60} h';
  return '${minutes ~/ 60} h ${minutes % 60} m';
}

/// Keeps the last 10 digits of an Indian mobile number, or null if not 10 digits.
String? normalisePhone(String? raw) {
  if (raw == null) return null;
  final digits = raw.replaceAll(RegExp(r'[^0-9]'), '');
  if (digits.length < 10) return null;
  return digits.substring(digits.length - 10);
}

DateTime? parseTime(dynamic v) => v is String && v.isNotEmpty ? DateTime.tryParse(v) : null;
