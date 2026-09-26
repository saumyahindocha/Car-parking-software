/// Typed views over the backend JSON (see backend/app/api/*.py and serial.py).
/// Parsing is lenient: unknown/missing fields fall back to sensible defaults so
/// a newer server never crashes an older phone.
library;

import '../tariff/tariff.dart';
import '../util/format.dart';

int _i(dynamic v, [int d = 0]) => v is num ? v.toInt() : (v is String ? int.tryParse(v) ?? d : d);
int? _in(dynamic v) => v is num ? v.toInt() : (v is String ? int.tryParse(v) : null);
bool _b(dynamic v) => v == true;
String? _s(dynamic v) => v?.toString();
Map<String, dynamic> _m(dynamic v) => v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};
List<dynamic> _l(dynamic v) => v is List ? v : const [];

class Role {
  static const admin = 'ADMIN';
  static const supervisor = 'SUPERVISOR';
  static const worker = 'WORKER';
  static const guard = 'GUARD';
}

class UserInfo {
  UserInfo({
    required this.id,
    required this.username,
    required this.name,
    required this.role,
    this.phone,
    this.deviceBound = false,
    this.active = true,
  });
  final int id;
  final String username;
  final String name;
  final String role;
  final String? phone;
  final bool deviceBound;
  final bool active;

  bool get isGuard => role == Role.guard;
  bool get isSupervisor => role == Role.supervisor || role == Role.admin;
  bool get isCollector => role == Role.worker || isSupervisor;

  factory UserInfo.fromJson(Map<String, dynamic> j) => UserInfo(
    id: _i(j['id']),
    username: _s(j['username']) ?? '',
    name: _s(j['name']) ?? _s(j['username']) ?? '',
    role: _s(j['role']) ?? Role.worker,
    phone: _s(j['phone']),
    deviceBound: _b(j['device_bound']),
    active: j['active'] != false,
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'username': username,
    'name': name,
    'role': role,
    'phone': phone,
    'device_bound': deviceBound,
    'active': active,
  };
}

class ZoneInfo {
  ZoneInfo({required this.id, required this.name, this.gateId, this.until});
  final int id;
  final String name;
  final String? gateId;
  final DateTime? until;
  factory ZoneInfo.fromJson(Map<String, dynamic> j) =>
      ZoneInfo(id: _i(j['id']), name: _s(j['name']) ?? '', gateId: _s(j['gate_id']), until: parseTime(j['until']));
}

class PassTypeInfo {
  PassTypeInfo({
    required this.id,
    required this.vehicleClass,
    required this.name,
    required this.periodUnit,
    required this.periodValue,
    required this.pricePaise,
    this.isDefault = false,
  });
  final int id;
  final String vehicleClass;
  final String name;
  final String periodUnit;
  final int periodValue;
  final int pricePaise;
  final bool isDefault;
  factory PassTypeInfo.fromJson(Map<String, dynamic> j) => PassTypeInfo(
    id: _i(j['id']),
    vehicleClass: _s(j['vehicle_class']) ?? 'BIKE',
    name: _s(j['name']) ?? 'Pass',
    periodUnit: _s(j['period_unit']) ?? 'MONTH',
    periodValue: _i(j['period_value'], 1),
    pricePaise: _i(j['price_paise']),
    isDefault: _b(j['is_default']),
  );
}

class SiteSettings {
  SiteSettings(this.raw);
  final Map<String, dynamic> raw;
  String get lotName => _s(raw['lot_name']) ?? 'Station Parking';
  String get upiVpa => _s(raw['upi_vpa']) ?? '';
  String get upiPayeeName => _s(raw['upi_payee_name']) ?? lotName;
  bool get cashEnabled => raw['cash_enabled'] != false;
  int get cashLimitPaise => _i(raw['cash_limit_paise'], 200000);
  double get cashWarnRatio => raw['cash_warn_ratio'] is num ? (raw['cash_warn_ratio'] as num).toDouble() : 0.8;
  List<int> get durationButtons {
    final l = _l(raw['duration_buttons']).map((e) => _i(e)).where((e) => e > 0).toList();
    return l.isEmpty ? const [120, 240, 480, 720, 1440] : l;
  }

  List<String> get stateCodes {
    final l = _l(raw['state_codes']).map((e) => e.toString()).toList();
    return l;
  }

  int get toCollectHours => _i(raw['to_collect_hours'], 6);
  String get receiptFooter =>
      _s(raw['receipt_footer']) ??
      'Final charge is calculated on actual time; any difference is adjusted on your next visit.';
  int get passCandidateVisits => _i(raw['pass_candidate_visits'], 8);
}

class Bootstrap {
  Bootstrap(this.raw)
    : user = UserInfo.fromJson(_m(raw['user'])),
      zone = raw['zone'] is Map ? ZoneInfo.fromJson(_m(raw['zone'])) : null,
      zones = _l(raw['zones']).map((e) => ZoneInfo.fromJson(_m(e))).toList(),
      settings = SiteSettings(_m(raw['settings'])),
      cashAllowed = raw['cash_allowed'] != false,
      tariffs = _l(raw['tariffs']).map((e) => Tariff.fromJson(_m(e))).toList(),
      passTypes = _l(raw['pass_types']).map((e) => PassTypeInfo.fromJson(_m(e))).toList(),
      gates = {for (final g in _l(raw['gates'])) _s(_m(g)['id']) ?? '': _s(_m(g)['name']) ?? ''},
      serverTime = parseTime(raw['server_time']),
      siteTimezone = _s(raw['site_timezone']),
      publicReceiptBase = _stripSlash(_s(raw['public_receipt_base']));

  final Map<String, dynamic> raw;
  final UserInfo user;
  final ZoneInfo? zone;
  final List<ZoneInfo> zones;
  final SiteSettings settings;
  final bool cashAllowed;
  final List<Tariff> tariffs;
  final List<PassTypeInfo> passTypes;
  final Map<String, String> gates;
  final DateTime? serverTime;

  /// IANA zone of the site (e.g. "Asia/Kolkata"); null on older servers.
  final String? siteTimezone;

  /// Base of public receipt links, e.g. "https://pay.example-parking.in/r".
  final String? publicReceiptBase;

  static String? _stripSlash(String? s) {
    if (s == null || s.isEmpty) return null;
    var out = s;
    while (out.endsWith('/')) {
      out = out.substring(0, out.length - 1);
    }
    return out;
  }

  /// Public receipt link for a receipt code (null if the base is unknown).
  String? receiptLink(String code) => publicReceiptBase == null ? null : '$publicReceiptBase/$code';

  String zoneName(int? id) {
    if (id == null) return '—';
    for (final z in zones) {
      if (z.id == id) return z.name;
    }
    return 'Zone $id';
  }

  List<PassTypeInfo> passTypesFor(String vehicleClass) =>
      passTypes.where((p) => p.vehicleClass == vehicleClass).toList()
        ..sort((a, b) => (b.isDefault ? 1 : 0) - (a.isDefault ? 1 : 0));
}

/// A row of `GET /api/collect/list`.
class CollectItem {
  CollectItem(this.raw);
  final Map<String, dynamic> raw;
  int get sessionId => _i(raw['session_id']);
  int get vehicleId => _i(raw['vehicle_id']);
  String get plate => _s(raw['plate']) ?? '';
  String get displayPlate => _s(raw['display_plate']) ?? plate;
  String get vehicleClass => _s(raw['vehicle_class']) ?? 'BIKE';
  DateTime? get entryAt => parseTime(raw['entry_at']);
  String? get gateId => _s(raw['gate_id']);
  int? get zoneId => _in(raw['zone_id']);
  int get previousDuePaise => _i(raw['previous_due_paise']);
  int get creditPaise => _i(raw['credit_paise']);
  String? get plateImage => _s(raw['plate_image']);
  String? get entryMatch => _s(raw['entry_match']);
  bool get unpaidFlagged => _b(raw['unpaid_flagged']);
  bool get phoneKnown => _b(raw['phone_known']);
  bool get passCandidate => _b(raw['pass_candidate']);
}

class SessionInfo {
  SessionInfo(this.raw);
  final Map<String, dynamic> raw;
  int get id => _i(raw['id']);
  int get vehicleId => _i(raw['vehicle_id']);
  String get plate => _s(raw['plate']) ?? '';
  String get displayPlate => _s(raw['display_plate']) ?? plate;
  String get vehicleClass => _s(raw['vehicle_class']) ?? 'BIKE';
  String get status => _s(raw['status']) ?? '';
  DateTime? get entryAt => parseTime(raw['entry_at']);
  DateTime? get exitAt => parseTime(raw['exit_at']);
  String? get entryGate => _s(raw['entry_gate']);
  String? get exitGate => _s(raw['exit_gate']);
  int? get estDurationMinutes => _in(raw['est_duration_minutes']);
  int? get chargePaise => _in(raw['charge_paise']);
  int? get zoneId => _in(raw['zone_id']);
  int? get passId => _in(raw['pass_id']);
  Map<String, dynamic> get entryImages => _m(raw['entry_images']);
  String? get plateImage => _s(entryImages['plate_crop']);
  bool get isOpen => status == 'OPEN';
}

class PassBrief {
  PassBrief(this.raw);
  final Map<String, dynamic> raw;
  int get id => _i(raw['id']);
  DateTime? get endsAt => parseTime(raw['ends_at']);
  DateTime? get startsAt => parseTime(raw['starts_at']);
  String get passType => _s(raw['pass_type']) ?? 'Pass';
  String get status => _s(raw['status']) ?? 'ACTIVE';
  int get amountPaise => _i(raw['amount_paise']);
  String? get endsOn => _s(raw['ends_on']);
  String? get startsOn => _s(raw['starts_on']);
}

class LedgerLine {
  LedgerLine(this.raw);
  final Map<String, dynamic> raw;
  String get kind => _s(raw['kind']) ?? '';
  int get amountPaise => _i(raw['amount_paise']);
  String? get reason => _s(raw['reason']);
  DateTime? get createdAt => parseTime(raw['created_at']);
}

class VehicleInfo {
  VehicleInfo(this.raw);
  final Map<String, dynamic> raw;
  int get id => _i(raw['id']);
  String get plate => _s(raw['plate']) ?? '';
  String get displayPlate => _s(raw['display_plate']) ?? plate;
  String get vehicleClass => _s(raw['vehicle_class']) ?? 'BIKE';
  DateTime? get firstSeen => parseTime(raw['first_seen']);
  DateTime? get lastSeen => parseTime(raw['last_seen']);
  String? get phone => _s(raw['phone']);
  String? get name => _s(raw['name']);
  int get balancePaise => _i(raw['balance_paise']);
  int get duePaise => balancePaise > 0 ? balancePaise : 0;
  int get creditPaise => balancePaise < 0 ? -balancePaise : 0;
  int get pendingClaimsPaise => _i(raw['pending_claims_paise']);
  PassBrief? get activePass => raw['pass'] is Map ? PassBrief(_m(raw['pass'])) : null;
  SessionInfo? get openSession => raw['open_session'] is Map ? SessionInfo(_m(raw['open_session'])) : null;
  int get distance => _i(raw['distance']);
  bool get exact => raw['exact'] == true || distance == 0;
  bool get passCandidate => _b(raw['pass_candidate']);
  List<SessionInfo> get sessions => _l(raw['sessions']).map((e) => SessionInfo(_m(e))).toList();
  List<PaymentInfo> get payments => _l(raw['payments']).map((e) => PaymentInfo(_m(e))).toList();
  List<LedgerLine> get ledger => _l(raw['ledger']).map((e) => LedgerLine(_m(e))).toList();
  List<PassBrief> get passes => _l(raw['passes']).map((e) => PassBrief(_m(e))).toList();

  /// Latest plate crop of this vehicle (server `last_plate_image`), else the open session's.
  String? get plateImage => _s(raw['last_plate_image']) ?? openSession?.plateImage;
}

class ReceiptInfo {
  ReceiptInfo(this.raw);
  final Map<String, dynamic> raw;
  String get code => _s(raw['code']) ?? '';
  String? get number => _s(raw['number']);
  String? get link => _s(raw['link']);
  String? get channel => _s(raw['channel']);
  String? get deliveryStatus => _s(raw['delivery_status']);
  bool get toPhone => channel == 'SMS' || channel == 'WHATSAPP';
}

class PayStatus {
  static const initiated = 'INITIATED';
  static const claimedOffline = 'CLAIMED_OFFLINE';
  static const confirmed = 'CONFIRMED';
  static const failed = 'FAILED';
  static const refunded = 'REFUNDED';
  static const reversed = 'REVERSED';
}

class PaymentInfo {
  PaymentInfo(this.raw);
  final Map<String, dynamic> raw;
  int get id => _i(raw['id']);
  int get vehicleId => _i(raw['vehicle_id']);
  String? get plate => _s(raw['plate']);
  int? get sessionId => _in(raw['session_id']);
  int? get passId => _in(raw['pass_id']);
  String get purpose => _s(raw['purpose']) ?? 'SESSION';
  String get mode => _s(raw['mode']) ?? 'UPI';
  int get amountPaise => _i(raw['amount_paise']);
  int get basePaise => _i(raw['base_paise']);
  int get duesPaise => _i(raw['dues_paise']);
  int? get durationMinutes => _in(raw['duration_minutes']);
  String get status => _s(raw['status']) ?? '';
  String? get txnRef => _s(raw['txn_ref']);
  String? get upiUri => _s(raw['upi_uri']);
  bool get offline => _b(raw['offline']);
  DateTime? get createdAt => parseTime(raw['created_at']);
  DateTime? get confirmedAt => parseTime(raw['confirmed_at']);
  String? get utr => _s(raw['utr']);
  ReceiptInfo? get receipt => raw['receipt'] is Map ? ReceiptInfo(_m(raw['receipt'])) : null;
  bool get limitBreach => _b(raw['limit_breach']);
  String? get statusNote => _s(raw['status_note']);
  Map<String, dynamic>? get cash => raw['cash'] is Map ? _m(raw['cash']) : null;
  bool get isDone => status == PayStatus.confirmed || status == PayStatus.claimedOffline;
}

class QuoteInfo {
  QuoteInfo({
    required this.basePaise,
    required this.duesPaise,
    required this.creditPaise,
    required this.amountPaise,
    this.durationMinutes,
    this.local = false,
  });
  final int basePaise;
  final int duesPaise;
  final int creditPaise;
  final int amountPaise;
  final int? durationMinutes;

  /// Computed on the phone from the cached tariff (server unreachable).
  final bool local;

  factory QuoteInfo.fromJson(Map<String, dynamic> j) => QuoteInfo(
    basePaise: _i(j['base_paise']),
    duesPaise: _i(j['dues_paise']),
    creditPaise: _i(j['credit_paise']),
    amountPaise: _i(j['amount_paise']),
    durationMinutes: _in(j['duration_minutes']),
  );
}

class CashHolding {
  CashHolding(this.raw);
  final Map<String, dynamic> raw;
  int get userId => _i(raw['user_id']);
  String? get name => _s(raw['name']);
  int? get shiftId => _in(raw['shift_id']);
  int? get zoneId => _in(raw['zone_id']);
  int get cashInHandPaise => _i(raw['cash_in_hand_paise']);
  int get limitPaise => _i(raw['limit_paise'], 200000);
  bool get warn => _b(raw['warn']);
  bool get blocked => _b(raw['blocked']);
}

class ShiftInfo {
  ShiftInfo(this.raw);
  final Map<String, dynamic> raw;
  int get id => _i(raw['id']);
  int? get zoneId => _in(raw['zone_id']);
  DateTime? get openedAt => parseTime(raw['opened_at']);
  int get upiPaise => _i(raw['upi_paise']);
  int get cashPaise => _i(raw['cash_paise']);
  int get upiCount => _i(raw['upi_count']);
  int get cashCount => _i(raw['cash_count']);
  int get handedOverPaise => _i(raw['handed_over_paise']);
  int get pendingHandoverPaise => _i(raw['pending_handover_paise']);
  CashHolding? get cash => raw['cash'] is Map ? CashHolding(_m(raw['cash'])) : null;
}

class HandoverInfo {
  HandoverInfo(this.raw);
  final Map<String, dynamic> raw;
  int get id => _i(raw['id']);
  int get fromUser => _i(raw['from_user']);
  String get fromName => _s(raw['from_name']) ?? 'Worker ${raw['from_user']}';
  int? get shiftId => _in(raw['shift_id']);
  int get expectedPaise => _i(raw['expected_paise']);
  int get declaredPaise => _i(raw['declared_paise']);
  Map<int, int> get declaredDenoms => _denoms(raw['declared_denoms']);
  int? get countedPaise => _in(raw['counted_paise']);
  Map<int, int> get countedDenoms => _denoms(raw['counted_denoms']);
  int? get variancePaise => _in(raw['variance_paise']);
  String get status => _s(raw['status']) ?? '';
  String? get note => _s(raw['note']);
  DateTime? get declaredAt => parseTime(raw['declared_at']);
  DateTime? get confirmedAt => parseTime(raw['confirmed_at']);

  static Map<int, int> _denoms(dynamic v) {
    final out = <int, int>{};
    _m(v).forEach((k, n) {
      final d = int.tryParse(k);
      if (d != null) out[d] = _i(n);
    });
    return out;
  }
}

class AlertInfo {
  AlertInfo(this.raw);
  final Map<String, dynamic> raw;

  /// REST (`alert_json`) nests details under `data`; WebSocket payloads flatten them.
  Map<String, dynamic> get data => raw['data'] is Map ? _m(raw['data']) : raw;
  int get id => _i(raw['id']);
  String get kind => _s(raw['kind']) ?? '';
  String? get gateId => _s(raw['gate_id']);
  int? get sessionId => _in(raw['session_id']);
  int? get vehicleId => _in(raw['vehicle_id']);
  String get message => _s(raw['message']) ?? '';
  DateTime? get createdAt => parseTime(raw['created_at']) ?? parseTime(raw['_received_at']);
  DateTime? get acknowledgedAt => parseTime(raw['acknowledged_at']);
  String? get note => _s(raw['note']);
  String get plate => _s(data['plate']) ?? '';
  String get displayPlate => _s(data['display_plate']) ?? plate;
  int get amountDuePaise => _i(data['amount_due_paise']);
  String? get plateImage => _s(data['plate_image']);
  bool get isExitUnpaid => kind == 'EXIT_UNPAID';
}

class DisputeInfo {
  DisputeInfo(this.raw);
  final Map<String, dynamic> raw;
  int get id => _i(raw['id']);
  int get vehicleId => _i(raw['vehicle_id']);
  String get plate => _s(raw['plate']) ?? '';
  int? get sessionId => _in(raw['session_id']);
  int? get claimedPaise => _in(raw['claimed_paise']);
  String get claimedMode => _s(raw['claimed_mode']) ?? 'CASH';
  String? get claimedWhen => _s(raw['claimed_when']);
  int? get zoneId => _in(raw['zone_id']);
  String? get workerName => _s(raw['worker_name']);
  String get raisedByRole => _s(raw['raised_by_role']) ?? '';
  String get status => _s(raw['status']) ?? 'OPEN';
  String? get note => _s(raw['note']);
  String? get resolutionNote => _s(raw['resolution_note']);
  DateTime? get createdAt => parseTime(raw['created_at']);
  int? get balancePaise => _in(raw['balance_paise']);
}

class ZoneAssignmentInfo {
  ZoneAssignmentInfo(this.raw);
  final Map<String, dynamic> raw;
  int get id => _i(raw['id']);
  int get zoneId => _i(raw['zone_id']);
  int get userId => _i(raw['user_id']);
  String get userName => _s(raw['user_name']) ?? '';
  DateTime? get startsAt => parseTime(raw['starts_at']);
  DateTime? get endsAt => parseTime(raw['ends_at']);
  String get shiftLabel => _s(raw['shift_label']) ?? '';
}
