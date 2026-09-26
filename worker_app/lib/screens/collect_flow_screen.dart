import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/models.dart';
import '../domain/pay_request.dart';
import '../domain/plates.dart' as plates;
import '../state/app_state.dart';
import '../state/payment_service.dart';
import '../tariff/tariff.dart';
import '../util/format.dart';
import '../widgets/common.dart';
import '../widgets/dialogs.dart';
import '../widgets/plate_scanner.dart';
import 'pass_sell_screen.dart';
import 'receipt_screen.dart';
import 'upi_pay_screen.dart';

/// The < 20 s collection flow: confirm/correct plate → duration → system amount
/// (dues as a separate line) → UPI (default) or Cash → digital receipt.
/// Without a session (a vehicle with dues only) it collects the dues.
class CollectFlowScreen extends StatefulWidget {
  const CollectFlowScreen({super.key, required this.target});
  final CollectTarget target;

  @override
  State<CollectFlowScreen> createState() => _CollectFlowScreenState();
}

class _CollectFlowScreenState extends State<CollectFlowScreen> {
  late CollectTarget _t;
  int? _duration;
  QuoteInfo? _quote;
  bool _quoting = false;
  String? _quoteError;
  OverrideRequest? _override;
  bool _busy = false;
  final _parked = TextEditingController();

  bool get _duesOnly => _t.sessionId == null;

  @override
  void initState() {
    super.initState();
    _t = widget.target;
    if (_duesOnly) {
      _quote = QuoteInfo(basePaise: 0, duesPaise: _t.duesPaise, creditPaise: 0, amountPaise: _t.duesPaise);
    }
  }

  @override
  void dispose() {
    _parked.dispose();
    super.dispose();
  }

  AppState get _app => context.read<AppState>();

  Future<void> _selectDuration(int minutes) async {
    setState(() {
      _duration = minutes;
      _quoting = true;
      _quoteError = null;
      _override = null;
    });
    QuoteInfo? q;
    String? err;
    try {
      if (!_app.online) throw NetworkException('offline');
      q = await _app.api.quote(_t.sessionId!, minutes);
    } on NetworkException {
      try {
        q = _localQuote(minutes);
      } catch (e) {
        err = 'Offline and no cached tariff: $e';
      }
    } on ApiException catch (e) {
      err = e.detail;
    }
    if (!mounted || _duration != minutes) return;
    setState(() {
      _quote = q;
      _quoteError = err;
      _quoting = false;
    });
  }

  QuoteInfo _localQuote(int minutes) {
    final boot = _app.bootstrap;
    if (boot == null || boot.tariffs.isEmpty) throw StateError('tariff not cached');
    if (_t.entryAt == null) throw StateError('entry time unknown');
    final lq = localSessionQuote(
      tariffs: boot.tariffs,
      vehicleClass: _t.vehicleClass,
      entryAt: _t.entryAt!,
      durationMinutes: minutes,
      duesPaise: _t.duesPaise,
      creditPaise: _t.creditPaise,
    );
    return QuoteInfo(
      basePaise: lq.basePaise,
      duesPaise: lq.duesPaise,
      creditPaise: lq.creditPaise,
      amountPaise: lq.amountPaise,
      durationMinutes: minutes,
      local: true,
    );
  }

  PayRequest? get _request {
    final q = _quote;
    if (q == null) return null;
    final r = PayRequest(
      purpose: _duesOnly ? PayPurpose.dues : PayPurpose.session,
      target: _t,
      basePaise: q.basePaise,
      duesPaise: q.duesPaise,
      creditPaise: q.creditPaise,
      amountPaise: q.amountPaise,
      durationMinutes: _duesOnly ? null : _duration,
      localQuote: q.local,
    );
    final o = _override;
    return o == null ? r : r.withOverride(o.amountPaise, o.reason, o.supervisorPin);
  }

  // ------------------------------------------------------------------ plate correction
  Future<void> _correctPlate() async {
    if (_t.sessionId == null) return;
    final ctl = TextEditingController(text: _t.plate);
    final codes = _app.settings.stateCodes;
    final newPlate = await showDialog<String>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, set) {
          final norm = plates.normalise(ctl.text);
          final corr = plates.correct(norm, codes.isEmpty ? null : codes);
          return AlertDialog(
            title: const Text('Correct the plate'),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Type the plate exactly as on the vehicle. The correction is logged and sent for review.'),
                const SizedBox(height: 12),
                TextField(
                  controller: ctl,
                  autofocus: true,
                  textCapitalization: TextCapitalization.characters,
                  style: const TextStyle(fontSize: 22, fontFamily: 'monospace', fontWeight: FontWeight.w700),
                  decoration: InputDecoration(
                    border: const OutlineInputBorder(),
                    suffixIcon: IconButton(
                      icon: const Icon(Icons.camera_alt),
                      tooltip: 'Scan plate',
                      onPressed: () async {
                        final p = await scanPlate(c, stateCodes: codes);
                        if (p != null) set(() => ctl.text = p);
                      },
                    ),
                  ),
                  onChanged: (_) => set(() {}),
                ),
                const SizedBox(height: 8),
                Text(
                  corr.valid ? 'Reads as ${plates.display(corr.plate)}' : 'Not a valid Indian plate yet',
                  style: TextStyle(color: corr.valid ? paidGreen : Colors.deepOrange),
                ),
              ],
            ),
            actions: [
              TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
              FilledButton(
                onPressed: norm.length >= 6 ? () => Navigator.pop(c, corr.valid ? corr.plate : norm) : null,
                child: const Text('Save correction'),
              ),
            ],
          );
        },
      ),
    );
    if (newPlate == null || newPlate == _t.plate || !mounted) return;
    setState(() => _busy = true);
    try {
      final s = await _app.api.correctPlate(_t.sessionId!, newPlate);
      var t = _t.copyWith(vehicleId: s.vehicleId, plate: s.plate, displayPlate: s.displayPlate);
      try {
        final v = await _app.api.vehicle(s.vehicleId);
        t = t.copyWith(
          duesPaise: v.duePaise,
          creditPaise: v.creditPaise,
          phoneKnown: v.phone != null,
          passCandidate: v.passCandidate,
        );
      } catch (_) {}
      if (!mounted) return;
      setState(() => _t = t);
      if (s.status == 'PASS') {
        showSnack(context, '${s.displayPlate} holds a valid pass — nothing to collect.');
        Navigator.pop(context);
        return;
      }
      showSnack(context, 'Plate corrected to ${s.displayPlate}');
      if (_duration != null) await _selectDuration(_duration!);
    } on NetworkException {
      if (mounted) {
        showSnack(
          context,
          'Plate correction needs the server. Collect with the current plate or retry later.',
          error: true,
        );
      }
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _askOverride() async {
    final q = _quote;
    if (q == null) return;
    if (q.local) {
      showSnack(context, 'Overrides need the server (supervisor PIN is checked there).', error: true);
      return;
    }
    final o = await overrideDialog(context, q.amountPaise);
    if (o != null) setState(() => _override = o);
  }

  // ------------------------------------------------------------------ pay
  Future<void> _payUpi() async {
    final r = _request;
    if (r == null) return;
    final done = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => UpiPayScreen(request: r, parkedLocation: _parked.text.trim()),
      ),
    );
    if (done == true && mounted) Navigator.pop(context, true);
    if (done == false && mounted && _duration != null) await _selectDuration(_duration!); // amount may have changed
  }

  Future<void> _payCash() async {
    final r = _request;
    if (r == null) return;
    final svc = PaymentService(_app);
    try {
      svc.checkCash(r.payablePaise);
    } on CashBlocked catch (e) {
      showSnack(context, e.message, error: true);
      return;
    }
    final ok = await confirmDialog(
      context,
      'Cash received ${rupees(r.payablePaise)}?',
      'Confirm only after you have the cash in hand for ${_t.displayPlate}.',
      ok: 'Cash received ${rupees(r.payablePaise)}',
      okColor: paidGreen,
    );
    if (!ok || !mounted) return;
    final phone = await askPhone(context, phoneOnFile: _t.phoneKnown);
    if (phone == null || !mounted) return;
    setState(() => _busy = true);
    try {
      final res = await svc.recordCash(r, phone: phone.phone, parkedLocation: _parked.text.trim());
      if (!mounted) return;
      await Navigator.of(context).pushReplacement(
        MaterialPageRoute(
          builder: (_) => ReceiptScreen(
            args: ReceiptArgs(
              request: r,
              mode: 'CASH',
              payment: res.payment,
              clientUuid: res.clientUuid,
              phone: phone.phone,
              phoneOnFile: phone.useOnFile,
              mandatoryQr: true,
            ),
          ),
        ),
      );
    } on CashBlocked catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    } on ApiException catch (e) {
      if (!mounted) return;
      showSnack(context, e.detail, error: true);
      if (e.isConflict && _duration != null) await _selectDuration(_duration!);
      if (e.isCashLimit) await _app.refreshCash();
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ------------------------------------------------------------------ UI
  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final buttons = app.settings.durationButtons;
    final r = _request;
    final cashPos = app.cashPosition;
    final cashOk = app.cashAllowed && r != null && cashPos.canCollect(r.payablePaise);
    return Scaffold(
      appBar: AppBar(title: Text(_duesOnly ? 'Collect dues' : 'Collect')),
      body: Column(
        children: [
          const ConnectivityBar(),
          const CashLimitBanner(),
          Expanded(
            child: AbsorbPointer(
              absorbing: _busy,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  // 1. confirm the plate
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Column(
                        children: [
                          PlateImage(_t.plateImage, width: double.infinity, height: 120, fit: BoxFit.contain),
                          const SizedBox(height: 10),
                          PlateText(_t.displayPlate, size: 28),
                          const SizedBox(height: 6),
                          if (_t.entryAt != null)
                            Text(
                              'Entered ${dateTimeIst(_t.entryAt)} (${ago(_t.entryAt)}) · ${app.bootstrap?.gates[_t.gateId] ?? _t.gateId ?? ''}',
                              style: const TextStyle(color: Colors.black54),
                            ),
                          if (!_duesOnly) ...[
                            const SizedBox(height: 8),
                            Row(
                              children: [
                                const Expanded(
                                  child: Text(
                                    'Does the plate match the vehicle?',
                                    style: TextStyle(fontWeight: FontWeight.w600),
                                  ),
                                ),
                                TextButton.icon(
                                  onPressed: _correctPlate,
                                  icon: const Icon(Icons.edit),
                                  label: const Text('Correct it'),
                                ),
                              ],
                            ),
                          ],
                          if (_t.passCandidate)
                            Container(
                              margin: const EdgeInsets.only(top: 6),
                              padding: const EdgeInsets.all(8),
                              decoration: BoxDecoration(
                                color: Colors.purple.shade50,
                                borderRadius: BorderRadius.circular(8),
                              ),
                              child: Row(
                                children: [
                                  const Icon(Icons.card_membership, color: Colors.purple),
                                  const SizedBox(width: 8),
                                  const Expanded(
                                    child: Text(
                                      'Parks here often — offer a monthly pass.',
                                      style: TextStyle(color: Colors.purple),
                                    ),
                                  ),
                                  TextButton(onPressed: _openPassSale, child: const Text('Sell pass')),
                                ],
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                  // 2. duration
                  if (!_duesOnly) ...[
                    const SectionTitle('How long will they park?'),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final m in buttons)
                          SizedBox(
                            width: (MediaQuery.of(context).size.width - 32 - 16) / 3,
                            height: 56,
                            child: ChoiceChip(
                              label: SizedBox(
                                width: double.infinity,
                                child: Text(
                                  durationLabel(m),
                                  textAlign: TextAlign.center,
                                  style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
                                ),
                              ),
                              selected: _duration == m,
                              onSelected: (_) => _selectDuration(m),
                            ),
                          ),
                      ],
                    ),
                  ],
                  // 3. amount
                  const SectionTitle('Amount'),
                  if (_quoting)
                    const Padding(
                      padding: EdgeInsets.all(16),
                      child: Center(child: CircularProgressIndicator()),
                    ),
                  if (_quoteError != null) Text(_quoteError!, style: const TextStyle(color: dueRed, fontSize: 15)),
                  if (!_quoting && _quote == null && _quoteError == null)
                    const Text('Tap a duration to see the amount.', style: TextStyle(color: Colors.black54)),
                  if (!_quoting && _quote != null) _amountCard(_quote!, r!),
                  // 4. pay
                  if (!_quoting && r != null && r.payablePaise > 0) ...[
                    const SizedBox(height: 16),
                    BigButton(
                      label: 'UPI  ${rupees(r.payablePaise)}',
                      icon: Icons.qr_code_2,
                      color: upiBlue,
                      height: 68,
                      onPressed: _payUpi,
                    ),
                    const SizedBox(height: 10),
                    BigButton(
                      label: !app.cashAllowed
                          ? 'Cash is off — use UPI'
                          : (cashOk ? 'Cash  ${rupees(r.payablePaise)}' : 'Cash blocked — hand over cash first'),
                      icon: Icons.payments,
                      outlined: true,
                      color: cashOk ? paidGreen : Colors.grey,
                      onPressed: cashOk ? _payCash : null,
                    ),
                    const SizedBox(height: 8),
                    ExpansionTile(
                      tilePadding: EdgeInsets.zero,
                      title: const Text('Parked location (optional)', style: TextStyle(fontSize: 14)),
                      children: [
                        TextField(
                          controller: _parked,
                          decoration: const InputDecoration(hintText: 'e.g. Row C near pillar 12'),
                        ),
                      ],
                    ),
                  ],
                  if (!_quoting && r != null && r.payablePaise == 0)
                    const Padding(
                      padding: EdgeInsets.all(12),
                      child: Text(
                        'Nothing to collect — the customer\'s credit covers this.',
                        style: TextStyle(color: paidGreen, fontSize: 16),
                      ),
                    ),
                  // 5. passes
                  const SectionTitle('Monthly pass'),
                  OutlinedButton.icon(
                    onPressed: _openPassSale,
                    icon: const Icon(Icons.card_membership),
                    label: const Text('Sell / renew a pass for this vehicle'),
                  ),
                  const SizedBox(height: 24),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _amountCard(QuoteInfo q, PayRequest r) {
    return Card(
      color: Colors.grey.shade50,
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          children: [
            if (!_duesOnly) MoneyRow('Parking ${_duration == null ? '' : durationLabel(_duration!)}', q.basePaise),
            if (q.duesPaise > 0) MoneyRow('Previous dues', q.duesPaise, color: dueRed, bold: true),
            if (q.creditPaise > 0) MoneyRow('Credit from earlier', q.creditPaise, color: paidGreen, negative: true),
            const Divider(),
            MoneyRow('Total', q.amountPaise, bold: true, size: 24),
            if (r.hasOverride) ...[
              MoneyRow('Override (supervisor)', r.payablePaise, bold: true, size: 20, color: Colors.deepPurple),
              Align(
                alignment: Alignment.centerLeft,
                child: Text('Reason: ${r.overrideReason}', style: const TextStyle(fontSize: 12, color: Colors.black54)),
              ),
            ],
            if (q.local)
              const Padding(
                padding: EdgeInsets.only(top: 6),
                child: Row(
                  children: [
                    Icon(Icons.cloud_off, size: 14, color: Colors.deepOrange),
                    SizedBox(width: 4),
                    Expanded(
                      child: Text(
                        'Calculated on this phone from the saved tariff (server offline).',
                        style: TextStyle(fontSize: 12, color: Colors.deepOrange),
                      ),
                    ),
                  ],
                ),
              ),
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: r.hasOverride ? () => setState(() => _override = null) : _askOverride,
                child: Text(
                  r.hasOverride ? 'Remove override' : 'Different amount? (needs supervisor PIN)',
                  style: const TextStyle(fontSize: 12),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _openPassSale() {
    Navigator.of(context).push(MaterialPageRoute(builder: (_) => PassSellScreen(target: _t)));
  }
}
