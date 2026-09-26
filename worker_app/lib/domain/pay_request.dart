/// What the worker is collecting for, with the system-calculated amount.
/// Builds both the online API bodies and the offline `/api/sync` payloads, so
/// the two paths can never drift apart.
library;

import 'upi.dart';

enum PayPurpose { session, dues, pass }

/// The vehicle/session being collected for (from the To-collect list, a search
/// result, or a vehicle's history).
class CollectTarget {
  const CollectTarget({
    required this.vehicleId,
    required this.plate,
    required this.displayPlate,
    required this.vehicleClass,
    this.sessionId,
    this.entryAt,
    this.gateId,
    this.zoneId,
    this.plateImage,
    this.duesPaise = 0,
    this.creditPaise = 0,
    this.passCandidate = false,
    this.phoneKnown = false,
  });

  final int vehicleId;
  final String plate;
  final String displayPlate;
  final String vehicleClass;
  final int? sessionId;
  final DateTime? entryAt;
  final String? gateId;
  final int? zoneId;
  final String? plateImage;

  /// Previous balance due (shown as a separate red line).
  final int duesPaise;
  final int creditPaise;
  final bool passCandidate;
  final bool phoneKnown;

  CollectTarget copyWith({int? vehicleId, String? plate, String? displayPlate, int? duesPaise, int? creditPaise, bool? phoneKnown, bool? passCandidate}) =>
      CollectTarget(
        vehicleId: vehicleId ?? this.vehicleId,
        plate: plate ?? this.plate,
        displayPlate: displayPlate ?? this.displayPlate,
        vehicleClass: vehicleClass,
        sessionId: sessionId,
        entryAt: entryAt,
        gateId: gateId,
        zoneId: zoneId,
        plateImage: plateImage,
        duesPaise: duesPaise ?? this.duesPaise,
        creditPaise: creditPaise ?? this.creditPaise,
        passCandidate: passCandidate ?? this.passCandidate,
        phoneKnown: phoneKnown ?? this.phoneKnown,
      );
}

class PayRequest {
  const PayRequest({
    required this.purpose,
    required this.target,
    required this.basePaise,
    required this.duesPaise,
    required this.creditPaise,
    required this.amountPaise,
    this.durationMinutes,
    this.passTypeId,
    this.passName,
    this.localQuote = false,
    this.overridePaise,
    this.overrideReason,
    this.supervisorPin,
  });

  final PayPurpose purpose;
  final CollectTarget target;
  final int basePaise;
  final int duesPaise;
  final int creditPaise;

  /// System amount (tariff + dues − credit, or pass price + dues).
  final int amountPaise;
  final int? durationMinutes;
  final int? passTypeId;
  final String? passName;

  /// Amount computed on the phone from the cached tariff (server unreachable).
  final bool localQuote;
  final int? overridePaise;
  final String? overrideReason;
  final String? supervisorPin;

  bool get hasOverride => overridePaise != null && overridePaise != amountPaise;

  /// What the customer actually pays.
  int get payablePaise => hasOverride ? overridePaise! : amountPaise;

  int get vehicleId => target.vehicleId;
  int? get sessionId => purpose == PayPurpose.session ? target.sessionId : null;

  String get purposeCode => switch (purpose) {
        PayPurpose.session => 'SESSION',
        PayPurpose.dues => 'DUES',
        PayPurpose.pass => 'PASS',
      };

  PayRequest withOverride(int? paise, String? reason, String? pin) => PayRequest(
        purpose: purpose,
        target: target,
        basePaise: basePaise,
        duesPaise: duesPaise,
        creditPaise: creditPaise,
        amountPaise: amountPaise,
        durationMinutes: durationMinutes,
        passTypeId: passTypeId,
        passName: passName,
        localQuote: localQuote,
        overridePaise: paise,
        overrideReason: reason,
        supervisorPin: pin,
      );

  /// Body for `POST /api/payments/upi|cash` (SESSION / DUES).
  Map<String, dynamic> paymentBody({String? phone, required String clientUuid, String? parkedLocation}) => {
        'purpose': purposeCode,
        if (sessionId != null) 'session_id': sessionId,
        'vehicle_id': vehicleId,
        if (durationMinutes != null && purpose == PayPurpose.session) 'duration_minutes': durationMinutes,
        if (passTypeId != null) 'pass_type_id': passTypeId,
        'phone': ?phone,
        'client_uuid': clientUuid,
        'expected_amount_paise': amountPaise,
        if (hasOverride) 'override_paise': overridePaise,
        if (hasOverride) 'override_reason': overrideReason,
        if (hasOverride) 'supervisor_pin': supervisorPin,
        if (parkedLocation != null && parkedLocation.isNotEmpty) 'parked_location': parkedLocation,
      };

  /// Body for `POST /api/passes/sell`.
  /// Without a known vehicle (id 0) the plate is sent and the server creates it.
  Map<String, dynamic> passSellBody({required String mode, String? phone, required String clientUuid}) => {
        if (vehicleId > 0) 'vehicle_id': vehicleId else 'plate': target.plate,
        'pass_type_id': passTypeId,
        'mode': mode,
        'phone': ?phone,
        'client_uuid': clientUuid,
      };

  /// `data` of a CASH / UPI_CLAIM item for `POST /api/sync` (see
  /// `payments._offline_quote`: base is re-checked against the tariff there).
  Map<String, dynamic> syncData({String? phone, String? txnRef}) => {
        if (sessionId != null) 'session_id': sessionId,
        'vehicle_id': vehicleId,
        if (durationMinutes != null && purpose == PayPurpose.session) 'duration_minutes': durationMinutes,
        if (passTypeId != null && purpose == PayPurpose.pass) 'pass_type_id': passTypeId,
        'dues_paise': duesPaise,
        'amount_paise': payablePaise,
        'phone': ?phone,
        'txn_ref': ?txnRef,
      };

  /// UPI reference encoding what is paid (session id, else vehicle id).
  String newTxnRef() =>
      sessionId != null ? makeTxnRef(TxnKind.session, sessionId!) : makeTxnRef(TxnKind.vehicle, vehicleId);

  String describe() => switch (purpose) {
        PayPurpose.session => 'Parking${durationMinutes == null ? '' : ' ${_dur(durationMinutes!)}'}',
        PayPurpose.dues => 'Previous dues',
        PayPurpose.pass => passName ?? 'Pass',
      };

  static String _dur(int m) => m % 1440 == 0 ? (m == 1440 ? 'full day' : '${m ~/ 1440} days') : '${m ~/ 60} h';
}
