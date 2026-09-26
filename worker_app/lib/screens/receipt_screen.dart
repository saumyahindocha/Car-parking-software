import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../api/api_client.dart';
import '../api/models.dart';
import '../domain/pay_request.dart';
import '../state/app_state.dart';
import '../state/payment_service.dart';
import '../util/format.dart';
import '../widgets/common.dart';
import '../widgets/dialogs.dart';

class ReceiptArgs {
  const ReceiptArgs({
    required this.request,
    required this.mode,
    required this.clientUuid,
    this.payment,
    this.phone,
    this.phoneOnFile = false,
    this.mandatoryQr = true,
    this.receiptCode,
  });
  final PayRequest request;
  final String mode;
  final String clientUuid;

  /// Server payment (online) — null when the cash was queued offline.
  final PaymentInfo? payment;
  final String? phone;
  final bool phoneOnFile;

  /// Cash: the QR must be shown and "Customer scanned" tapped before moving on.
  final bool mandatoryQr;

  /// Offline cash: receipt code generated on the phone and sent in the CASH
  /// sync item; the server uses it, so its public link is the real receipt.
  final String? receiptCode;
}

/// Digital receipt after a cash payment (spec 6 / 5A): SMS/WhatsApp when a
/// number is known, otherwise the receipt QR full-screen until the customer
/// has scanned it.
class ReceiptScreen extends StatefulWidget {
  const ReceiptScreen({super.key, required this.args});
  final ReceiptArgs args;

  @override
  State<ReceiptScreen> createState() => _ReceiptScreenState();
}

class _ReceiptScreenState extends State<ReceiptScreen> {
  PaymentInfo? _payment;
  String? _phone;
  bool _busy = false;
  bool _scanned = false;

  ReceiptArgs get a => widget.args;
  AppState get _app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _payment = a.payment;
    _phone = a.phone;
  }

  bool get _offline => _payment == null;

  bool get _toPhone {
    if (_phone != null) return true;
    final rec = _payment?.receipt;
    if (rec != null) return rec.toPhone;
    return a.phoneOnFile;
  }

  bool get _mustShowQr => a.mandatoryQr && !_toPhone && !_scanned;

  /// Online: the receipt link. Offline: a provisional text receipt (the final
  /// receipt link is issued when the phone syncs).
  String get _qrData => _payment?.receipt?.link ?? _payment?.receipt?.text ?? _offlineLink ?? _offlineText;

  /// `${public_receipt_base}/<code>` from the cached bootstrap.
  String? get _offlineLink {
    final code = a.receiptCode;
    if (code == null) return null;
    return _app.bootstrap?.receiptLink(code);
  }

  /// Plain-text receipt details (shown under the QR offline; the QR itself
  /// when no receipt link can be built).
  String get _offlineText {
    final r = a.request;
    final s = _app.settings;
    return [
      s.lotName,
      'PARKING RECEIPT (provisional, offline)',
      'Plate: ${r.target.displayPlate}',
      'Paid: ${rupees(r.payablePaise)} ${a.mode}',
      if (r.durationMinutes != null) 'Duration: ${durationLabel(r.durationMinutes!)}',
      if (r.duesPaise > 0) 'Dues cleared: ${rupees(r.duesPaise)}',
      if (r.passName != null) 'Pass: ${r.passName}',
      'Time: ${dateTimeIst(DateTime.now())}',
      'Ref: ${a.receiptCode ?? a.clientUuid.substring(0, 8).toUpperCase()}',
      s.receiptFooter,
    ].join('\n');
  }

  Future<void> _customerScanned() async {
    setState(() => _busy = true);
    try {
      await PaymentService(_app).receiptShown(receiptCode: _payment?.receipt?.code, paymentClientUuid: a.clientUuid);
      setState(() => _scanned = true);
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _gaveNumber() async {
    final ans = await askPhone(context);
    final phone = ans?.phone;
    if (phone == null || !mounted) return;
    setState(() => _busy = true);
    try {
      final rec = _payment?.receipt;
      if (rec != null) {
        await _app.api.receiptSend(rec.code, phone);
        _payment = await _app.api.payment(_payment!.id);
      } else {
        // Offline: attach the number to the queued cash item if not yet sent,
        // otherwise save it as a contact for the vehicle.
        final patched = await _app.store.patchPending(a.clientUuid, {'phone': phone});
        if (!patched) await PaymentService(_app).saveContact(a.request.vehicleId, phone);
      }
      setState(() => _phone = phone);
    } on NetworkException {
      final patched = await _app.store.patchPending(a.clientUuid, {'phone': phone});
      if (!patched) await PaymentService(_app).saveContact(a.request.vehicleId, phone);
      setState(() => _phone = phone);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = a.request;
    return PopScope(
      canPop: !_mustShowQr && !_busy,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && _mustShowQr) {
          showSnack(context, 'Show the receipt QR and tap "Customer scanned" first.', error: true);
        }
      },
      child: Scaffold(
        appBar: AppBar(
          automaticallyImplyLeading: !_mustShowQr,
          title: Text('${a.mode == 'CASH' ? 'Cash' : 'UPI'} ${rupees(r.payablePaise)} received'),
          backgroundColor: paidGreen,
          foregroundColor: Colors.white,
        ),
        body: SafeArea(child: _toPhone ? _phoneView() : _qrView()),
      ),
    );
  }

  Widget _phoneView() {
    final rec = _payment?.receipt;
    final ch = rec?.channel == 'WHATSAPP' ? 'WhatsApp' : 'SMS';
    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        const Icon(Icons.mark_email_read, size: 88, color: paidGreen),
        const SizedBox(height: 12),
        Text(
          _offline
              ? 'Receipt will be sent by SMS/WhatsApp${_phone != null ? ' to ${_mask(_phone!)}' : ' to the number on file'} when this phone syncs.'
              : 'Receipt sent by $ch${_phone != null ? ' to ${_mask(_phone!)}' : ''}.',
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
        ),
        if (rec?.number != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              'Receipt ${rec!.number}',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.black54),
            ),
          ),
        if (_offline)
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Text(
              'Saved on this phone (offline).',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.deepOrange),
            ),
          ),
        const SizedBox(height: 32),
        BigButton(
          label: 'Done — next vehicle',
          icon: Icons.check,
          color: paidGreen,
          onPressed: () => Navigator.pop(context, true),
        ),
      ],
    );
  }

  Widget _qrView() {
    final size = MediaQuery.of(context).size.width - 32;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          'Customer: scan this QR to keep your receipt',
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800),
        ),
        const SizedBox(height: 12),
        Center(
          child: Container(
            color: Colors.white,
            padding: const EdgeInsets.all(10),
            child: QrImageView(data: _qrData, size: size.clamp(220, 420).toDouble(), backgroundColor: Colors.white),
          ),
        ),
        const SizedBox(height: 8),
        Center(child: PlateText(a.request.target.displayPlate, size: 18)),
        if (_offline) ...[
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              _offlineLink != null
                  ? 'Saved offline. The receipt page opens once this phone syncs.'
                  : 'Offline receipt: the receipt link is issued when this phone syncs.',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.deepOrange, fontSize: 12),
            ),
          ),
          if (_offlineLink != null)
            Container(
              margin: const EdgeInsets.only(top: 8),
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(color: Colors.grey.shade100, borderRadius: BorderRadius.circular(8)),
              child: Text(_offlineText, style: const TextStyle(fontSize: 13)),
            ),
        ],
        const SizedBox(height: 16),
        BigButton(
          label: 'Customer scanned',
          icon: Icons.qr_code_scanner,
          color: paidGreen,
          onPressed: _busy ? null : _customerScanned,
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: _busy ? null : _gaveNumber,
          icon: const Icon(Icons.sms),
          label: const Text('Customer gave a mobile number instead'),
        ),
      ],
    );
  }

  static String _mask(String p) => p.length < 4 ? p : '••••••${p.substring(p.length - 4)}';
}
