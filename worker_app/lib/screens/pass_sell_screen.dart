import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/models.dart';
import '../domain/pay_request.dart';
import '../offline/local_store.dart';
import '../state/app_state.dart';
import '../state/payment_service.dart';
import '../util/format.dart';
import '../widgets/common.dart';
import '../widgets/dialogs.dart';
import 'receipt_screen.dart';
import 'upi_pay_screen.dart';

/// Sell or renew a monthly (or other) pass for a vehicle, by UPI or cash.
/// The price comes from the pass type; previous dues are added as a separate line.
class PassSellScreen extends StatefulWidget {
  const PassSellScreen({super.key, required this.target});

  /// `vehicleId == 0` means a new vehicle known only by plate (online only).
  final CollectTarget target;

  @override
  State<PassSellScreen> createState() => _PassSellScreenState();
}

class _PassSellScreenState extends State<PassSellScreen> {
  PassTypeInfo? _type;
  List<PassBrief> _passes = [];
  final _phone = TextEditingController();
  bool _busy = false;

  AppState get _app => context.read<AppState>();
  CollectTarget get _t => widget.target;

  @override
  void initState() {
    super.initState();
    final types = context.read<AppState>().bootstrap?.passTypesFor(_t.vehicleClass) ?? [];
    if (types.isNotEmpty) _type = types.first;
    _loadPasses();
  }

  @override
  void dispose() {
    _phone.dispose();
    super.dispose();
  }

  Future<void> _loadPasses() async {
    if (_t.vehicleId <= 0) return;
    try {
      final list = await _app.api.get('/api/passes/vehicle/${_t.vehicleId}') as List;
      if (mounted) setState(() => _passes = list.map((e) => PassBrief(Map<String, dynamic>.from(e as Map))).toList());
    } catch (_) {}
  }

  PayRequest? get _request {
    final t = _type;
    if (t == null) return null;
    return PayRequest(
      purpose: PayPurpose.pass,
      target: _t,
      basePaise: t.pricePaise,
      duesPaise: _t.duesPaise,
      creditPaise: 0,
      amountPaise: t.pricePaise + _t.duesPaise,
      passTypeId: t.id,
      passName: t.name,
    );
  }

  String? get _phoneValue => normalisePhone(_phone.text);

  Future<void> _upi() async {
    final r = _request;
    if (r == null) return;
    setState(() => _busy = true);
    PaymentInfo? existing;
    try {
      existing = await _app.api.sellPass(
        r.passSellBody(mode: 'UPI', phone: _phoneValue, clientUuid: LocalStore.newUuid()),
      );
    } on NetworkException {
      if (_t.vehicleId <= 0) {
        if (mounted) showSnack(context, 'Selling a pass to a new plate needs the server.', error: true);
        setState(() => _busy = false);
        return;
      }
    } catch (e) {
      if (mounted) showError(context, e);
      setState(() => _busy = false);
      return;
    }
    if (!mounted) return;
    setState(() => _busy = false);
    final done = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => UpiPayScreen(request: r, existing: existing, phone: _phoneValue),
      ),
    );
    if (done == true && mounted) Navigator.pop(context, true);
  }

  Future<void> _cash() async {
    final r = _request;
    if (r == null) return;
    final svc = PaymentService(_app);
    try {
      svc.checkCash(r.payablePaise);
    } on CashBlocked catch (e) {
      showSnack(context, e.message, error: true);
      return;
    }
    if (_t.vehicleId <= 0 && !_app.online) {
      showSnack(context, 'Selling a pass to a new plate needs the server.', error: true);
      return;
    }
    final ok = await confirmDialog(
      context,
      'Cash received ${rupees(r.payablePaise)}?',
      '${r.passName} for ${_t.displayPlate}.',
      ok: 'Cash received ${rupees(r.payablePaise)}',
      okColor: paidGreen,
    );
    if (!ok || !mounted) return;
    String? phone = _phoneValue;
    var onFile = false;
    if (phone == null) {
      final ans = await askPhone(context, phoneOnFile: _t.phoneKnown, purpose: 'pass receipt and renewal reminders');
      if (ans == null || !mounted) return;
      phone = ans.phone;
      onFile = ans.useOnFile;
    }
    setState(() => _busy = true);
    try {
      final res = await svc.recordCash(r, phone: phone);
      if (!mounted) return;
      await Navigator.of(context).pushReplacement(
        MaterialPageRoute(
          builder: (_) => ReceiptScreen(
            args: ReceiptArgs(
              request: r,
              mode: 'CASH',
              payment: res.payment,
              clientUuid: res.clientUuid,
              phone: phone,
              phoneOnFile: onFile,
            ),
          ),
        ),
      );
    } on CashBlocked catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final types = app.bootstrap?.passTypesFor(_t.vehicleClass) ?? [];
    final r = _request;
    final cashOk = app.cashAllowed && r != null && app.cashPosition.canCollect(r.payablePaise);
    final active = _passes.where((p) => p.status == 'ACTIVE').toList();
    return Scaffold(
      appBar: AppBar(title: const Text('Sell / renew pass')),
      body: Column(
        children: [
          const ConnectivityBar(),
          Expanded(
            child: AbsorbPointer(
              absorbing: _busy,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  Center(child: PlateText(_t.displayPlate, size: 26)),
                  const SizedBox(height: 8),
                  if (active.isNotEmpty)
                    Card(
                      color: Colors.purple.shade50,
                      child: ListTile(
                        leading: const Icon(Icons.card_membership, color: Colors.purple),
                        title: Text(
                          '${active.first.passType} active until ${active.first.endsOn ?? dateIst(active.first.endsAt)}',
                        ),
                        subtitle: const Text('A renewal starts when the current pass ends.'),
                      ),
                    )
                  else
                    const Text(
                      'Pass holders park with zero interaction: entry and exit are recognised automatically.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: Colors.black54),
                    ),
                  const SectionTitle('Pass type'),
                  if (types.isEmpty) const Text('No pass types for this vehicle class.'),
                  for (final t in types)
                    Card(
                      shape: RoundedRectangleBorder(
                        side: BorderSide(color: _type?.id == t.id ? upiBlue : Colors.transparent, width: 2),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: ListTile(
                        onTap: () => setState(() => _type = t),
                        leading: Icon(
                          _type?.id == t.id ? Icons.radio_button_checked : Icons.radio_button_off,
                          color: upiBlue,
                        ),
                        title: Text(t.name, style: const TextStyle(fontWeight: FontWeight.w700)),
                        subtitle: Text('${t.periodValue} ${t.periodUnit.toLowerCase()}${t.periodValue > 1 ? 's' : ''}'),
                        trailing: Text(
                          rupees(t.pricePaise),
                          style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800),
                        ),
                      ),
                    ),
                  if (r != null) ...[
                    const SectionTitle('Amount'),
                    MoneyRow(r.passName ?? 'Pass', r.basePaise),
                    if (r.duesPaise > 0) MoneyRow('Previous dues', r.duesPaise, color: dueRed, bold: true),
                    const Divider(),
                    MoneyRow('Total', r.amountPaise, bold: true, size: 24),
                  ],
                  const SectionTitle('Customer mobile (for receipt & renewal reminders)'),
                  TextField(
                    controller: _phone,
                    keyboardType: TextInputType.phone,
                    decoration: InputDecoration(
                      prefixText: '+91 ',
                      border: const OutlineInputBorder(),
                      helperText: _t.phoneKnown ? 'A number is already on file for this vehicle' : null,
                    ),
                    onChanged: (_) => setState(() {}),
                  ),
                  const SizedBox(height: 20),
                  if (r != null) ...[
                    BigButton(
                      label: 'UPI  ${rupees(r.payablePaise)}',
                      icon: Icons.qr_code_2,
                      color: upiBlue,
                      height: 64,
                      onPressed: _upi,
                    ),
                    const SizedBox(height: 10),
                    BigButton(
                      label: cashOk
                          ? 'Cash  ${rupees(r.payablePaise)}'
                          : (app.cashAllowed ? 'Cash blocked — hand over first' : 'Cash is off'),
                      icon: Icons.payments,
                      outlined: true,
                      color: cashOk ? paidGreen : Colors.grey,
                      onPressed: cashOk ? _cash : null,
                    ),
                  ],
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
