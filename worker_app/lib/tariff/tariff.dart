/// Dart port of `backend/app/domain/tariff.py` (`calculate_charge`).
///
/// The phone computes amounts on-device from the cached tariff when the edge
/// server is unreachable, so this MUST stay rule-for-rule identical to the
/// Python engine. The unit tests in `test/tariff_test.dart` mirror
/// `backend/tests/test_tariff.py` case by case.
///
/// Rules (all values come from the versioned tariff row):
/// * free_minutes: stays up to this length cost nothing (0 by default).
/// * The stay is split into blocks of `block_minutes` (default 12 h). Each full
///   block costs `block_cap_paise`. Within a block: `first_slab_paise` covers the
///   first `first_slab_minutes`; each *started* additional hour costs
///   `per_hour_paise`; the block total never exceeds `block_cap_paise`.
/// * Grace: `grace_minutes` past every slab boundary before the next unit is
///   charged. A remainder of <= grace after full blocks is free.
/// * daily_cap_paise (optional): each started 24 h period never exceeds it.
/// * overnight_paise: added once for every local `overnight_cutoff_hour`
///   crossed during the stay.
///
/// The tariff in force at *entry* applies to the whole stay (see [tariffFor]).
library;

/// India Standard Time has had a fixed +05:30 offset (no DST) since 1945, so a
/// fixed offset is an exact stand-in for `ZoneInfo("Asia/Kolkata")`.
const Duration istOffset = Duration(hours: 5, minutes: 30);

class Tariff {
  const Tariff({
    this.id,
    this.vehicleClass = 'BIKE',
    DateTime? effectiveFrom,
    this.version = 1,
    this.firstSlabMinutes = 120,
    this.firstSlabPaise = 1000,
    this.perHourPaise = 500,
    this.graceMinutes = 10,
    this.blockMinutes = 720,
    this.blockCapPaise = 3000,
    this.dailyCapPaise,
    this.overnightPaise = 0,
    this.overnightCutoffHour = 0,
    this.freeMinutes = 0,
    // ignore: prefer_initializing_formals
  }) : _effectiveFrom = effectiveFrom;

  final int? id;
  final String vehicleClass;
  final DateTime? _effectiveFrom;
  final int version;
  final int firstSlabMinutes;
  final int firstSlabPaise;
  final int perHourPaise;
  final int graceMinutes;
  final int blockMinutes;
  final int? blockCapPaise;
  final int? dailyCapPaise;
  final int overnightPaise;
  final int overnightCutoffHour;
  final int freeMinutes;

  /// Defaults to 2000-01-01 UTC like the Python `TariffSpec`.
  DateTime get effectiveFrom => _effectiveFrom ?? DateTime.utc(2000, 1, 1);

  Tariff copyWith({
    DateTime? effectiveFrom,
    int? version,
    int? firstSlabPaise,
    int? blockCapPaise,
    bool clearBlockCap = false,
    int? blockMinutes,
    int? dailyCapPaise,
    int? overnightPaise,
    int? overnightCutoffHour,
    int? freeMinutes,
  }) => Tariff(
    id: id,
    vehicleClass: vehicleClass,
    effectiveFrom: effectiveFrom ?? _effectiveFrom,
    version: version ?? this.version,
    firstSlabMinutes: firstSlabMinutes,
    firstSlabPaise: firstSlabPaise ?? this.firstSlabPaise,
    perHourPaise: perHourPaise,
    graceMinutes: graceMinutes,
    blockMinutes: blockMinutes ?? this.blockMinutes,
    blockCapPaise: clearBlockCap ? null : (blockCapPaise ?? this.blockCapPaise),
    dailyCapPaise: dailyCapPaise ?? this.dailyCapPaise,
    overnightPaise: overnightPaise ?? this.overnightPaise,
    overnightCutoffHour: overnightCutoffHour ?? this.overnightCutoffHour,
    freeMinutes: freeMinutes ?? this.freeMinutes,
  );

  /// From the `/api/bootstrap` `tariffs[]` JSON.
  factory Tariff.fromJson(Map<String, dynamic> j) => Tariff(
    id: (j['id'] as num?)?.toInt(),
    vehicleClass: j['vehicle_class'] as String? ?? 'BIKE',
    version: (j['version'] as num?)?.toInt() ?? 1,
    effectiveFrom: j['effective_from'] == null ? null : DateTime.parse(j['effective_from'] as String),
    firstSlabMinutes: (j['first_slab_minutes'] as num?)?.toInt() ?? 120,
    firstSlabPaise: (j['first_slab_paise'] as num?)?.toInt() ?? 0,
    perHourPaise: (j['per_hour_paise'] as num?)?.toInt() ?? 0,
    graceMinutes: (j['grace_minutes'] as num?)?.toInt() ?? 0,
    blockMinutes: (j['block_minutes'] as num?)?.toInt() ?? 0,
    blockCapPaise: (j['block_cap_paise'] as num?)?.toInt(),
    dailyCapPaise: (j['daily_cap_paise'] as num?)?.toInt(),
    overnightPaise: (j['overnight_paise'] as num?)?.toInt() ?? 0,
    overnightCutoffHour: (j['overnight_cutoff_hour'] as num?)?.toInt() ?? 0,
    freeMinutes: (j['free_minutes'] as num?)?.toInt() ?? 0,
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'vehicle_class': vehicleClass,
    'version': version,
    'effective_from': effectiveFrom.toUtc().toIso8601String(),
    'first_slab_minutes': firstSlabMinutes,
    'first_slab_paise': firstSlabPaise,
    'per_hour_paise': perHourPaise,
    'grace_minutes': graceMinutes,
    'block_minutes': blockMinutes,
    'block_cap_paise': blockCapPaise,
    'daily_cap_paise': dailyCapPaise,
    'overnight_paise': overnightPaise,
    'overnight_cutoff_hour': overnightCutoffHour,
    'free_minutes': freeMinutes,
  };
}

/// Python `divmod(x, y)` for non-negative doubles: (floor(x / y), remainder).
(int, double) _divmod(double x, int y) {
  final full = (x / y).floor();
  var rem = x - full * y;
  if (rem < 0) rem = 0; // guards float noise; inputs are never negative
  return (full, rem);
}

/// Charge for `minutes` (> 0) within a single block.
int _blockCharge(Tariff t, double minutes) {
  final over = minutes - t.firstSlabMinutes - t.graceMinutes;
  final extraHours = over > 0 ? (over / 60).ceil() : 0;
  var charge = t.firstSlabPaise + extraHours * t.perHourPaise;
  if (t.blockCapPaise != null && charge > t.blockCapPaise!) {
    charge = t.blockCapPaise!;
  }
  return charge;
}

/// Charge for a span made of full blocks plus a remainder.
int _spanCharge(Tariff t, double minutes) {
  if (minutes <= 0) return 0;
  final block = t.blockMinutes > 0 ? t.blockMinutes : null;
  if (block == null) return _blockCharge(t, minutes);
  final (full, rem) = _divmod(minutes, block);
  final cap = t.blockCapPaise ?? _blockCharge(t, block.toDouble());
  var total = full * cap;
  if (rem > 0 && !(full > 0 && rem <= t.graceMinutes)) {
    total += _blockCharge(t, rem);
  }
  return total;
}

/// Number of local `cutoffHour:00` instants in (entry, exit].
int countOvernights(DateTime entry, DateTime exit, int cutoffHour, {Duration tzOffset = istOffset}) {
  // Shift to "local wall clock" expressed as UTC DateTimes, then compare.
  final le = entry.toUtc().add(tzOffset);
  final lx = exit.toUtc().add(tzOffset);
  var d = DateTime.utc(le.year, le.month, le.day);
  final lastDay = DateTime.utc(lx.year, lx.month, lx.day);
  var n = 0;
  while (!d.isAfter(lastDay)) {
    final instant = DateTime.utc(d.year, d.month, d.day, cutoffHour);
    if (le.isBefore(instant) && !instant.isAfter(lx)) n++;
    d = DateTime.utc(d.year, d.month, d.day + 1);
  }
  return n;
}

/// Charge in paise for a stay. Pure: depends only on its arguments.
///
/// Throws [ArgumentError] if the tariff is for another class or exit < entry.
int calculateCharge(
  String vehicleClass,
  DateTime entryTime,
  DateTime exitTime,
  Tariff tariff, {
  Duration tzOffset = istOffset,
}) {
  final t = tariff;
  if (t.vehicleClass != vehicleClass) {
    throw ArgumentError('tariff is for ${t.vehicleClass}, not $vehicleClass');
  }
  var minutes = exitTime.difference(entryTime).inMicroseconds / 60e6;
  if (minutes < 0) throw ArgumentError('exit before entry');
  if (t.freeMinutes > 0 && minutes <= t.freeMinutes) return 0;
  if (minutes < 1e-9) minutes = 1e-9;

  int total;
  if (t.dailyCapPaise != null) {
    const day = 1440;
    final cap = t.dailyCapPaise!;
    final (fullDays, rem) = _divmod(minutes, day);
    final perDay = _min(_spanCharge(t, day.toDouble()), cap);
    total = fullDays * perDay;
    if (rem > 0 && !(fullDays > 0 && rem <= t.graceMinutes)) {
      total += _min(_spanCharge(t, rem), cap);
    }
  } else {
    total = _spanCharge(t, minutes);
  }

  if (t.overnightPaise != 0) {
    total += t.overnightPaise * countOvernights(entryTime, exitTime, t.overnightCutoffHour, tzOffset: tzOffset);
  }
  return total;
}

int _min(int a, int b) => a < b ? a : b;

/// The tariff version in force at [at] (latest effective_from <= at) for the class.
/// Throws [StateError] when none applies (Python raises LookupError).
Tariff tariffFor(Iterable<Tariff> tariffs, String vehicleClass, DateTime at) {
  Tariff? best;
  for (final t in tariffs) {
    if (t.vehicleClass != vehicleClass || t.effectiveFrom.isAfter(at)) continue;
    if (best == null || t.effectiveFrom.isAfter(best.effectiveFrom)) best = t;
  }
  if (best == null) {
    throw StateError('no tariff for $vehicleClass at ${at.toIso8601String()}');
  }
  return best;
}

/// Device-side quote for a session when the server is unreachable. Mirrors
/// `payments.quote_session`: base from the tariff in force at entry for the
/// chosen duration, plus previous dues, minus any credit (never below 0).
class LocalQuote {
  const LocalQuote({
    required this.basePaise,
    required this.duesPaise,
    required this.creditPaise,
    required this.amountPaise,
    required this.durationMinutes,
  });
  final int basePaise;
  final int duesPaise;
  final int creditPaise;
  final int amountPaise;
  final int durationMinutes;
}

LocalQuote localSessionQuote({
  required Iterable<Tariff> tariffs,
  required String vehicleClass,
  required DateTime entryAt,
  required int durationMinutes,
  int duesPaise = 0,
  int creditPaise = 0,
  Duration tzOffset = istOffset,
}) {
  final t = tariffFor(tariffs, vehicleClass, entryAt);
  final base = calculateCharge(
    vehicleClass,
    entryAt,
    entryAt.add(Duration(minutes: durationMinutes)),
    t,
    tzOffset: tzOffset,
  );
  final amount = base + duesPaise - creditPaise;
  return LocalQuote(
    basePaise: base,
    duesPaise: duesPaise,
    creditPaise: creditPaise,
    amountPaise: amount < 0 ? 0 : amount,
    durationMinutes: durationMinutes,
  );
}
