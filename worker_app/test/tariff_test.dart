// Mirrors backend/tests/test_tariff.py case for case: the phone computes
// offline cash/UPI amounts with this port, so it must agree with the server.
import 'package:flutter_test/flutter_test.dart';
import 'package:parking_worker/tariff/tariff.dart';

/// An IST wall-clock time as a UTC instant (like the Python `ist()` helper).
DateTime ist(int y, int mo, int d, [int h = 0, int mi = 0]) => DateTime.utc(y, mo, d, h, mi).subtract(istOffset);

const t0 = Tariff(); // seed default: ₹10 / 2 h, ₹5 per extra hour, ₹30 cap per 12 h, 10 min grace
final e0 = ist(2026, 3, 10, 8, 0);

int ch(num minutes, {Tariff t = t0, DateTime? entry}) {
  final e = entry ?? e0;
  return calculateCharge('BIKE', e, e.add(Duration(microseconds: (minutes * 60e6).round())), t);
}

void main() {
  group('default bike tariff', () {
    const cases = <List<int>>[
      [0, 1000], [1, 1000], [60, 1000], [120, 1000], [130, 1000], // within first slab + grace
      [131, 1500], [190, 1500], [191, 2000], [250, 2000], [251, 2500],
      [310, 2500], [311, 3000], [600, 3000], [720, 3000], // capped at ₹30 per 12 h block
      [730, 3000], // grace after a full block
      [731, 4000], [850, 4000], [851, 4500], // second block restarts slab
      [1440, 6000], [1450, 6000], [1451, 7000], [3 * 1440, 18000],
    ];
    for (final c in cases) {
      test('${c[0]} min -> ${c[1]} paise', () => expect(ch(c[0]), c[1]));
    }
  });

  test('day boundary same price as daytime', () {
    final night = ist(2026, 3, 10, 23, 0);
    expect(ch(180, entry: night), ch(180));
  });

  test('month and year boundary', () {
    expect(ch(24 * 60 + 30, entry: ist(2026, 1, 31, 20, 0)), ch(24 * 60 + 30));
    expect(ch(300, entry: ist(2026, 12, 31, 22, 0)), 2500);
  });

  test('leap day', () {
    expect(ch(2 * 1440, entry: ist(2028, 2, 28, 12, 0)), 12000);
  });

  test('free minutes', () {
    final t = t0.copyWith(freeMinutes: 5);
    expect(ch(5, t: t), 0);
    expect(ch(6, t: t), 1000);
  });

  test('daily cap', () {
    final t = t0.copyWith(dailyCapPaise: 5000);
    expect(ch(1440, t: t), 5000);
    expect(ch(1440 + 60, t: t), 6000);
    expect(ch(2 * 1440, t: t), 10000);
  });

  test('overnight charge', () {
    final t = t0.copyWith(overnightPaise: 2000, overnightCutoffHour: 0);
    expect(ch(180, t: t, entry: ist(2026, 3, 10, 22, 0)), 1500 + 2000); // crosses midnight once
    expect(ch(180, t: t, entry: ist(2026, 3, 10, 9, 0)), 1500);
    expect(countOvernights(ist(2026, 3, 10, 22), ist(2026, 3, 13, 1), 0), 3);
  });

  test('no block cap', () {
    final t = t0.copyWith(clearBlockCap: true, blockMinutes: 0);
    expect(ch(600, t: t), 1000 + 8 * 500);
  });

  test('validation', () {
    expect(() => calculateCharge('CAR', e0, e0, t0), throwsArgumentError);
    expect(() => calculateCharge('BIKE', e0, e0.subtract(const Duration(minutes: 1)), t0), throwsArgumentError);
    // (Python's naive-datetime check has no Dart equivalent: DateTime is always an instant.)
  });

  test('tariff in force at entry applies; mid-stay change ignored', () {
    final old = Tariff(effectiveFrom: ist(2025, 1, 1), version: 1);
    final neu = Tariff(effectiveFrom: ist(2026, 3, 10, 12, 0), version: 2, firstSlabPaise: 2000, blockCapPaise: 5000);
    final entry = ist(2026, 3, 10, 10, 0);
    final exit = ist(2026, 3, 10, 15, 0);
    final t = tariffFor([old, neu], 'BIKE', entry);
    expect(t.version, 1);
    expect(calculateCharge('BIKE', entry, exit, t), 2500);
    final t2 = tariffFor([old, neu], 'BIKE', ist(2026, 3, 10, 13, 0));
    expect(t2.version, 2);
    expect(() => tariffFor([neu], 'BIKE', entry), throwsStateError);
  });

  test('UTC vs IST inputs equivalent (pure)', () {
    final eUtc = e0.toUtc();
    expect(calculateCharge('BIKE', eUtc, eUtc.add(const Duration(hours: 5)), t0), ch(300));
    final eLocal = e0.toLocal();
    expect(calculateCharge('BIKE', eLocal, eLocal.add(const Duration(hours: 5)), t0), ch(300));
  });

  group('bootstrap JSON + local quote', () {
    final json = {
      'id': 1,
      'vehicle_class': 'BIKE',
      'version': 1,
      'effective_from': '2025-01-01T00:00:00+00:00',
      'first_slab_minutes': 120,
      'first_slab_paise': 1000,
      'per_hour_paise': 500,
      'grace_minutes': 10,
      'block_minutes': 720,
      'block_cap_paise': 3000,
      'daily_cap_paise': null,
      'overnight_paise': 0,
      'overnight_cutoff_hour': 0,
      'free_minutes': 0,
    };

    test('parses the server tariff', () {
      final t = Tariff.fromJson(json);
      expect(t.blockCapPaise, 3000);
      expect(t.dailyCapPaise, isNull);
      expect(t.effectiveFrom, DateTime.utc(2025));
      expect(calculateCharge('BIKE', e0, e0.add(const Duration(hours: 4)), t), 2000);
    });

    test('duration buttons with dues and credit (mirrors payments.quote_session)', () {
      final ts = [Tariff.fromJson(json)];
      final q = localSessionQuote(tariffs: ts, vehicleClass: 'BIKE', entryAt: e0, durationMinutes: 240, duesPaise: 1500);
      expect(q.basePaise, 2000);
      expect(q.amountPaise, 3500);
      final full = localSessionQuote(tariffs: ts, vehicleClass: 'BIKE', entryAt: e0, durationMinutes: 1440);
      expect(full.amountPaise, 6000);
      final credit = localSessionQuote(tariffs: ts, vehicleClass: 'BIKE', entryAt: e0, durationMinutes: 120, creditPaise: 1500);
      expect(credit.amountPaise, 0);
    });
  });
}
