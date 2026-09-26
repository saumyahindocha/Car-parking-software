import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../api/api_client.dart';
import '../api/models.dart';
import '../api/ws_client.dart';
import '../domain/pay_request.dart';
import '../domain/upi.dart';
import '../offline/local_store.dart';
import '../state/app_state.dart';
import '../state/payment_service.dart';
import '../util/format.dart';
import '../widgets/common.dart';
import '../widgets/dialogs.dart';

enum _Stage { creating, waiting, offlineServer, offlineLocal, confirmed, claimed, failed, error }

/// Shows the UPI QR for the exact amount.
///
/// * Online: dynamic gateway QR from `/api/payments/upi`; polls
///   `/api/payments/{id}` and listens to `payment.updated`; turns green on CONFIRMED.
/// * Gateway down (`offline: true`) or server unreachable: standard UPI intent QR
///   (payee VPA, exact amount, txn ref encoding the session) and the worker taps
///   "Customer shows success" → CLAIMED_OFFLINE (claim-offline call, or a
///   UPI_CLAIM item in the sync queue).
///
/// Pops `true` when done, `false` to go back and choose again.
class UpiPayScreen extends StatefulWidget {
  const UpiPayScreen({super.key, required this.request, this.existing, this.phone, this.parkedLocation});
  final PayRequest request;

  /// A payment already created by the server (e.g. `/api/passes/sell`).
  final PaymentInfo? existing;
  final String? phone;
  final String? parkedLocation;

  @override
  State<UpiPayScreen> createState() => _UpiPayScreenState();
}

class _UpiPayScreenState extends State<UpiPayScreen> {
  _Stage _stage = _Stage.creating;
  PaymentInfo? _payment;
  String? _qrData;
  String? _txnRef;
  String? _error;
  String? _phone;
  int _pollFailures = 0;
  bool _busy = false;
  Timer? _poll;
  StreamSubscription<WsMessage>? _sub;
  final String _clientUuid = LocalStore.newUuid();

  AppState get _app => context.read<AppState>();
  PayRequest get _r => widget.request;

  @override
  void initState() {
    super.initState();
    _phone = widget.phone;
    WidgetsBinding.instance.addPostFrameCallback((_) => _start());
  }

  @override
  void dispose() {
    _poll?.cancel();
    _sub?.cancel();
    super.dispose();
  }

  Future<void> _start() async {
    if (widget.existing != null) {
      _adopt(widget.existing!);
      return;
    }
    try {
      if (!_app.online) throw NetworkException('offline');
      final p = await _app.api.payUpi(
        _r.paymentBody(phone: _phone, clientUuid: _clientUuid, parkedLocation: widget.parkedLocation),
      );
      _adopt(p);
    } on NetworkException {
      _goLocal();
    } on ApiException catch (e) {
      if (!mounted) return;
      if (e.isConflict) {
        showSnack(context, e.detail, error: true);
        Navigator.pop(context, false);
        return;
      }
      setState(() {
        _stage = _Stage.error;
        _error = e.detail;
      });
    }
  }

  void _adopt(PaymentInfo p) {
    if (!mounted) return;
    setState(() {
      _payment = p;
      _txnRef = p.txnRef;
      _qrData = p.upiUri;
      if (p.status == PayStatus.confirmed) {
        _stage = _Stage.confirmed;
      } else if (p.status == PayStatus.claimedOffline) {
        _stage = _Stage.claimed;
      } else if (p.status == PayStatus.failed) {
        _stage = _Stage.failed;
      } else {
        _stage = p.offline ? _Stage.offlineServer : _Stage.waiting;
      }
    });
    if (_stage == _Stage.waiting) {
      _sub = _app.ws?.topic('payment.updated').listen((m) {
        if (m.data['id'] == p.id) _onUpdate(PaymentInfo(m.data));
      });
      _poll = Timer.periodic(const Duration(seconds: 3), (_) => _check());
    }
    if (_stage == _Stage.confirmed) _afterConfirmed();
  }

  void _goLocal() {
    if (!mounted) return;
    if (_r.hasOverride) {
      setState(() {
        _stage = _Stage.error;
        _error = 'The server is unreachable, and an amount override needs it. Go back and collect the system amount.';
      });
      return;
    }
    final s = _app.settings;
    if (s.upiVpa.isEmpty) {
      setState(() {
        _stage = _Stage.error;
        _error = 'Payee UPI ID not cached on this phone. Log in once while online.';
      });
      return;
    }
    final ref = _r.newTxnRef();
    setState(() {
      _txnRef = ref;
      _qrData = upiIntentUri(
        vpa: s.upiVpa,
        payee: s.upiPayeeName,
        amountPaise: _r.payablePaise,
        txnRef: ref,
        note: 'Parking ${_r.target.plate}',
      );
      _stage = _Stage.offlineLocal;
    });
  }

  Future<void> _check() async {
    final p = _payment;
    if (p == null || _stage != _Stage.waiting) return;
    try {
      final np = await _app.api.payment(p.id);
      _pollFailures = 0;
      _onUpdate(np);
    } on NetworkException {
      if (mounted) setState(() => _pollFailures++);
    } catch (_) {}
  }

  void _onUpdate(PaymentInfo np) {
    if (!mounted || _stage != _Stage.waiting) return;
    if (np.status == PayStatus.confirmed) {
      _poll?.cancel();
      setState(() {
        _payment = np;
        _stage = _Stage.confirmed;
      });
      _afterConfirmed();
    } else if (np.status == PayStatus.failed) {
      _poll?.cancel();
      setState(() {
        _payment = np;
        _stage = _Stage.failed;
      });
    } else if (np.status == PayStatus.claimedOffline) {
      _poll?.cancel();
      setState(() {
        _payment = np;
        _stage = _Stage.claimed;
      });
    }
  }

  Future<void> _afterConfirmed() async {
    if (_r.sessionId != null) _app.collect?.markCollected(_r.sessionId!);
    // Customer gave a number after the QR was created: switch the receipt to SMS.
    final rec = _payment?.receipt;
    if (_phone != null && rec != null && !rec.toPhone) {
      try {
        await _app.api.receiptSend(rec.code, _phone!);
        final np = await _app.api.payment(_payment!.id);
        if (mounted) setState(() => _payment = np);
      } catch (_) {}
    }
  }

  Future<void> _customerShowsSuccess() async {
    final ok = await confirmDialog(
      context,
      'Customer shows success?',
      'Check the customer\'s UPI app shows ${rupees(_r.payablePaise)} paid to ${_app.settings.upiPayeeName}'
          '${_txnRef == null ? '' : ' (ref $_txnRef)'}. It will be verified against the bank settlement.',
      ok: 'Yes, success shown',
      okColor: paidGreen,
    );
    if (!ok || !mounted) return;
    setState(() => _busy = true);
    try {
      if (_stage == _Stage.offlineServer && _payment != null) {
        final pay = _payment!;
        try {
          if (!_app.online) throw NetworkException('offline');
          final np = await _app.api.claimOffline(pay.id);
          if (_phone != null) await PaymentService(_app).saveContact(_r.vehicleId, _phone!);
          setState(() => _payment = np);
        } on NetworkException {
          // Server created this offline QR, then went away: queue a claim of
          // that same payment (matched by its txn_ref and exact amount).
          await PaymentService(_app)
              .queueUpiClaim(_r, txnRef: pay.txnRef ?? _txnRef!, phone: _phone, existingAmountPaise: pay.amountPaise);
        }
        setState(() => _stage = _Stage.claimed);
      } else {
        await PaymentService(_app).queueUpiClaim(_r, txnRef: _txnRef!, phone: _phone);
        setState(() => _stage = _Stage.claimed);
      }
      if (_r.sessionId != null) _app.collect?.markCollected(_r.sessionId!);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// The customer did not pay (switched to cash, or left). Cancels a server
  /// UPI payment that is still INITIATED; if it was paid meanwhile the server
  /// confirms it instead and the screen turns green.
  Future<void> _abandon() async {
    final pay = _payment;
    final cancellable = pay != null && (_stage == _Stage.waiting || _stage == _Stage.offlineServer);
    if (!cancellable) {
      if (mounted) Navigator.pop(context, false);
      return;
    }
    setState(() => _busy = true);
    try {
      final np = await _app.api.cancelPayment(pay.id, reason: 'customer did not pay by UPI (worker)');
      if (!mounted) return;
      if (np.status == PayStatus.confirmed) {
        _poll?.cancel();
        setState(() {
          _payment = np;
          _stage = _Stage.confirmed;
        });
        showSnack(context, 'The UPI payment just arrived — no cash needed.');
        _afterConfirmed();
        return;
      }
      if (np.status == PayStatus.claimedOffline) {
        setState(() {
          _payment = np;
          _stage = _Stage.claimed;
        });
        return;
      }
    } on NetworkException {
      // Leave it INITIATED: a late payment is still confirmed by webhook / reconciliation.
    } on ApiException catch (e) {
      // e.g. it was confirmed between our last poll and the cancel: re-read it.
      try {
        final np = await _app.api.payment(pay.id);
        if (np.isDone && mounted) {
          _onUpdate(np);
          if (_stage == _Stage.waiting) {
            setState(() {
              _payment = np;
              _stage = np.status == PayStatus.confirmed ? _Stage.confirmed : _Stage.claimed;
            });
          }
          return;
        }
      } catch (_) {}
      if (mounted) showSnack(context, e.detail, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
    if (mounted) Navigator.pop(context, false);
  }

  Future<void> _addPhone() async {
    final p = await textPrompt(
      context,
      'Customer mobile for the receipt',
      label: '10-digit mobile',
      keyboard: TextInputType.phone,
    );
    final n = normalisePhone(p);
    if (p != null && n == null && mounted) {
      showSnack(context, 'Enter a 10-digit mobile number', error: true);
      return;
    }
    if (n != null) setState(() => _phone = n);
  }

  @override
  Widget build(BuildContext context) {
    final green = _stage == _Stage.confirmed || _stage == _Stage.claimed;
    final pending = _stage == _Stage.waiting || _stage == _Stage.offlineServer;
    return PopScope(
      canPop: !_busy && !pending,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && pending && !_busy) _abandon();
      },
      child: Scaffold(
        backgroundColor: green ? paidGreen : null,
        appBar: AppBar(
          title: Text('UPI ${rupees(_r.payablePaise)}'),
          backgroundColor: green ? paidGreen : null,
          foregroundColor: green ? Colors.white : null,
        ),
        body: SafeArea(child: _body()),
      ),
    );
  }

  Widget _body() {
    switch (_stage) {
      case _Stage.creating:
        return const Center(child: CircularProgressIndicator());
      case _Stage.error:
        return Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.error_outline, size: 64, color: dueRed),
              const SizedBox(height: 12),
              Text(_error ?? 'Error', textAlign: TextAlign.center, style: const TextStyle(fontSize: 17)),
              const SizedBox(height: 24),
              BigButton(label: 'Back', onPressed: () => Navigator.pop(context, false)),
            ],
          ),
        );
      case _Stage.failed:
        return Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.cancel, size: 80, color: dueRed),
              const SizedBox(height: 12),
              const Text('Payment failed', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w800)),
              const SizedBox(height: 24),
              BigButton(label: 'Try again', onPressed: () => Navigator.pop(context, false)),
            ],
          ),
        );
      case _Stage.confirmed:
        return _done(
          title: 'Paid ${rupees(_payment?.amountPaise ?? _r.payablePaise)}',
          subtitle: 'UPI payment confirmed',
          receipt: _payment?.receipt,
        );
      case _Stage.claimed:
        return _done(
          title: 'Recorded ${rupees(_r.payablePaise)}',
          subtitle:
              'UPI claimed offline — it will be verified against the bank settlement. '
              '${_phone != null ? 'The receipt goes by SMS once verified.' : 'The receipt is issued once verified.'}',
          receipt: null,
        );
      case _Stage.waiting:
      case _Stage.offlineServer:
      case _Stage.offlineLocal:
        return _qrView();
    }
  }

  Widget _qrView() {
    final offline = _stage != _Stage.waiting;
    final size = MediaQuery.of(context).size.width - 48;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Center(child: PlateText(_r.target.displayPlate, size: 20)),
        const SizedBox(height: 8),
        Center(
          child: Text(
            rupees(_r.payablePaise),
            style: const TextStyle(fontSize: 40, fontWeight: FontWeight.w900, color: upiBlue),
          ),
        ),
        Center(
          child: Text(_r.describe(), style: const TextStyle(color: Colors.black54)),
        ),
        const SizedBox(height: 12),
        if (_qrData != null)
          Center(
            child: Container(
              color: Colors.white,
              padding: const EdgeInsets.all(8),
              child: QrImageView(data: _qrData!, size: size.clamp(200, 380).toDouble(), backgroundColor: Colors.white),
            ),
          ),
        const SizedBox(height: 12),
        if (!offline) ...[
          const Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
              SizedBox(width: 10),
              Text('Waiting for payment…', style: TextStyle(fontSize: 18)),
            ],
          ),
          if (_pollFailures >= 3) ...[
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.all(10),
              color: Colors.orange.shade50,
              child: const Text(
                'Server unreachable. If the customer paid, the payment still confirms automatically from the bank. You can move on.',
                style: TextStyle(color: Colors.deepOrange),
              ),
            ),
            TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Move on')),
          ],
        ] else ...[
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(color: Colors.orange.shade50, borderRadius: BorderRadius.circular(8)),
            child: Text(
              _stage == _Stage.offlineLocal
                  ? 'Server offline: QR made on this phone. Ask the customer to show the success screen.'
                  : 'Payment gateway offline: ask the customer to show the success screen.',
              style: const TextStyle(color: Colors.deepOrange),
            ),
          ),
          const SizedBox(height: 12),
          BigButton(
            label: 'Customer shows success',
            icon: Icons.verified,
            color: paidGreen,
            onPressed: _busy ? null : _customerShowsSuccess,
          ),
        ],
        if (_txnRef != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Center(
              child: Text(
                'Ref $_txnRef',
                style: const TextStyle(fontFamily: 'monospace', color: Colors.black45),
              ),
            ),
          ),
        const SizedBox(height: 12),
        OutlinedButton.icon(
          onPressed: _addPhone,
          icon: const Icon(Icons.sms),
          label: Text(_phone == null ? 'Add customer mobile for SMS receipt (optional)' : 'Receipt to ${_phone!}'),
        ),
        TextButton(onPressed: _busy ? null : _abandon, child: const Text('Customer did not pay / switch to cash')),
      ],
    );
  }

  Widget _done({required String title, required String subtitle, ReceiptInfo? receipt}) {
    final showQr = receipt != null && !receipt.toPhone && receipt.link != null;
    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        const Icon(Icons.check_circle, color: Colors.white, size: 96),
        const SizedBox(height: 8),
        Text(
          title,
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.white, fontSize: 34, fontWeight: FontWeight.w900),
        ),
        const SizedBox(height: 6),
        Text(
          subtitle,
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.white, fontSize: 16),
        ),
        const SizedBox(height: 6),
        Center(child: PlateText(_r.target.displayPlate, size: 20)),
        const SizedBox(height: 16),
        if (receipt != null && receipt.toPhone)
          Text(
            'Receipt sent by ${receipt.channel == 'WHATSAPP' ? 'WhatsApp' : 'SMS'}',
            textAlign: TextAlign.center,
            style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w700),
          ),
        if (showQr) ...[
          const Text(
            'Customer can scan for the receipt',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white),
          ),
          const SizedBox(height: 8),
          Center(
            child: Container(
              color: Colors.white,
              padding: const EdgeInsets.all(10),
              child: QrImageView(data: receipt.link!, size: 240, backgroundColor: Colors.white),
            ),
          ),
        ],
        const SizedBox(height: 24),
        BigButton(
          label: 'Done — next vehicle',
          color: Colors.white.withValues(alpha: 0.25),
          onPressed: () => Navigator.pop(context, true),
        ),
      ],
    );
  }
}
