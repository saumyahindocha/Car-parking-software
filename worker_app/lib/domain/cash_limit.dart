/// Device-side cash-in-hand tracking (spec 5A): the phone enforces the limit
/// even offline, counting cash recorded on the server *plus* cash recorded on
/// the phone that has not been synced yet (or failed to sync — the worker still
/// physically holds that cash).
library;

class CashPosition {
  const CashPosition({
    required this.serverHeldPaise,
    required this.unsyncedPaise,
    required this.limitPaise,
    this.warnRatio = 0.8,
  });

  /// `cash_in_hand_paise` from the server (`/api/me/cash` or the last sync response).
  final int serverHeldPaise;

  /// Sum of CASH items in the offline queue that the server has not accepted yet.
  final int unsyncedPaise;
  final int limitPaise;
  final double warnRatio;

  int get heldPaise => serverHeldPaise + unsyncedPaise;

  /// Same threshold maths as backend `holding_dict`: `int(limit * ratio)`.
  int get warnAtPaise => (limitPaise * warnRatio).floor();

  bool get warn => heldPaise >= warnAtPaise;

  bool get blocked => heldPaise >= limitPaise;

  int get headroomPaise => limitPaise - heldPaise < 0 ? 0 : limitPaise - heldPaise;

  double get fraction => limitPaise <= 0 ? 1 : (heldPaise / limitPaise).clamp(0.0, 1.0).toDouble();

  /// Backend rule (`record_cash`): refused when held + amount > limit.
  bool canCollect(int amountPaise) => heldPaise + amountPaise <= limitPaise;
}
