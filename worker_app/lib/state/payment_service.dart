import '../api/api_client.dart';
import '../api/models.dart';
import '../domain/pay_request.dart';
import '../offline/local_store.dart';
import '../util/format.dart';
import 'app_state.dart';

class CashBlocked implements Exception {
  CashBlocked(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Outcome of recording a cash payment: either the server's payment (online)
/// or the queued offline item (to be synced).
class CashResult {
  CashResult({required this.clientUuid, this.payment, this.queued});
  final String clientUuid;
  final PaymentInfo? payment;
  final QueueItem? queued;
  bool get offline => payment == null;
}

/// Cash and offline-UPI recording shared by the collect flow and pass sales.
class PaymentService {
  PaymentService(this.app);
  final AppState app;

  /// Throws [CashBlocked] if cash is off for this user or the limit would be exceeded.
  void checkCash(int amountPaise) {
    if (!app.cashAllowed) throw CashBlocked('Cash is switched off for you. Use UPI.');
    final pos = app.cashPosition;
    if (!pos.canCollect(amountPaise)) {
      throw CashBlocked(
        'Cash-in-hand limit ${rupees(pos.limitPaise)} would be exceeded '
        '(holding ${rupees(pos.heldPaise)}). Hand over cash first — UPI still works.',
      );
    }
  }

  /// Worker confirmed "Cash received ₹X". Online → `/api/payments/cash` (or
  /// `/api/passes/sell`); unreachable → CASH item in the offline queue with the
  /// same client_uuid (idempotent if the first attempt actually reached the server).
  Future<CashResult> recordCash(PayRequest r, {String? phone, String? parkedLocation}) async {
    checkCash(r.payablePaise);
    final uuid = LocalStore.newUuid();
    try {
      // Known offline: don't make the worker wait for a network timeout.
      if (!app.online) throw NetworkException('offline');
      final p = r.purpose == PayPurpose.pass
          ? await app.api.sellPass(r.passSellBody(mode: 'CASH', phone: phone, clientUuid: uuid))
          : await app.api.payCash(r.paymentBody(phone: phone, clientUuid: uuid, parkedLocation: parkedLocation));
      if (p.cash != null) {
        await app.store.putCache(CacheKeys.cash, p.cash);
      }
      await app.refreshCash();
      if (r.sessionId != null) app.collect?.markCollected(r.sessionId!);
      return CashResult(clientUuid: uuid, payment: p);
    } on NetworkException {
      if (r.vehicleId <= 0) {
        throw CashBlocked('Selling a pass to a new plate needs the server. Try again when online.');
      }
      if (r.hasOverride) {
        throw CashBlocked(
          'An amount override needs the server (supervisor PIN check). '
          'Collect the system amount, or wait until the server is reachable.',
        );
      }
      final item = await app.enqueue(
        SyncType.cash,
        r.syncData(phone: phone),
        clientUuid: uuid,
        amountPaise: r.payablePaise,
        label: 'Cash ${rupees(r.payablePaise)} · ${r.target.displayPlate} · ${r.describe()}',
      );
      if (r.sessionId != null) app.collect?.markCollected(r.sessionId!);
      return CashResult(clientUuid: uuid, queued: item);
    }
  }

  /// Offline UPI: the customer showed a success screen for the locally generated
  /// intent QR. Recorded as CLAIMED_OFFLINE via the sync queue; reconciliation
  /// later confirms it by txn_ref.
  Future<QueueItem> queueUpiClaim(PayRequest r, {required String txnRef, String? phone}) async {
    final item = await app.enqueue(
      SyncType.upiClaim,
      r.syncData(phone: phone, txnRef: txnRef),
      amountPaise: 0,
      label: 'UPI claim ${rupees(r.payablePaise)} · ${r.target.displayPlate} · $txnRef',
    );
    if (r.sessionId != null) app.collect?.markCollected(r.sessionId!);
    return item;
  }

  /// Marks a receipt QR as shown (online) or queues RECEIPT_SHOWN (offline).
  Future<void> receiptShown({String? receiptCode, required String paymentClientUuid}) async {
    if (receiptCode != null) {
      try {
        await app.api.receiptShown(receiptCode);
        return;
      } on NetworkException {
        // fall through to the queue
      }
    }
    await app.enqueue(SyncType.receiptShown, {
      'payment_client_uuid': paymentClientUuid,
    }, label: 'Receipt QR shown ${receiptCode ?? ''}'.trim());
  }

  /// Save a customer's number for the vehicle (online or CONTACT sync item).
  Future<void> saveContact(int vehicleId, String phone) async {
    try {
      await app.api.setContact(vehicleId, phone);
    } on NetworkException {
      await app.enqueue(SyncType.contact, {
        'vehicle_id': vehicleId,
        'phone': phone,
      }, label: 'Mobile for vehicle $vehicleId');
    }
  }
}
