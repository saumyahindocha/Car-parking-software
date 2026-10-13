import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../api/models.dart';
import '../state/app_state.dart';
import '../util/format.dart';
import '../widgets/common.dart';
import '../widgets/dialogs.dart';
import 'collect_flow_screen.dart';
import 'pass_sell_screen.dart';
import 'search_screen.dart' show targetFromVehicle;

/// Full vehicle history: sessions, payments, passes, ledger and balance. Supervisors
/// can reverse a cash payment or refund a UPI payment here (reason required).
class VehicleScreen extends StatefulWidget {
  const VehicleScreen({super.key, required this.vehicleId});
  final int vehicleId;

  @override
  State<VehicleScreen> createState() => _VehicleScreenState();
}

class _VehicleScreenState extends State<VehicleScreen> {
  VehicleInfo? _v;
  String? _error;
  bool _loading = true;

  AppState get _app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final v = await _app.api.vehicle(widget.vehicleId);
      if (mounted) {
        setState(() {
          _v = v;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = errorText(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _reverse(PaymentInfo p) async {
    final reason = await textPrompt(
      context,
      'Reverse cash ${rupees(p.amountPaise)}?',
      label: 'Reason (mandatory, goes to the override report)',
      maxLines: 2,
    );
    if (reason == null || !mounted) return;
    try {
      await _app.api.reverseCash(p.id, reason);
      if (mounted) showSnack(context, 'Cash payment reversed');
      await _load();
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _refund(PaymentInfo p) async {
    final reason = TextEditingController();
    final amt = TextEditingController(text: (p.amountPaise ~/ 100).toString());
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, set) => AlertDialog(
          title: Text('Refund UPI ${rupees(p.amountPaise)}'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: amt,
                keyboardType: TextInputType.number,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                decoration: const InputDecoration(labelText: 'Refund amount (₹)', prefixText: '₹ '),
              ),
              TextField(
                controller: reason,
                decoration: const InputDecoration(labelText: 'Reason (mandatory)'),
                onChanged: (_) => set(() {}),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
            FilledButton(
              onPressed: reason.text.trim().isEmpty ? null : () => Navigator.pop(c, true),
              child: const Text('Refund'),
            ),
          ],
        ),
      ),
    );
    if (ok != true || !mounted) return;
    final paise = (int.tryParse(amt.text) ?? 0) * 100;
    try {
      await _app.api.refundUpi(
        p.id,
        reason.text.trim(),
        amountPaise: paise > 0 && paise < p.amountPaise ? paise : null,
      );
      if (mounted) showSnack(context, 'Refund initiated through the gateway');
      await _load();
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final isSup = app.user?.isSupervisor ?? false;
    final v = _v;
    return Scaffold(
      appBar: AppBar(
        title: Text(v?.displayPlate ?? 'Vehicle'),
        actions: [IconButton(onPressed: _load, icon: const Icon(Icons.refresh))],
      ),
      body: Column(
        children: [
          const ConnectivityBar(),
          if (_loading && v == null) const Expanded(child: Center(child: CircularProgressIndicator())),
          if (_error != null && v == null) Expanded(child: EmptyState(_error!, icon: Icons.cloud_off)),
          if (v != null)
            Expanded(
              child: RefreshIndicator(
                onRefresh: _load,
                child: ListView(
                  padding: const EdgeInsets.all(12),
                  children: [
                    _header(v, app),
                    const SectionTitle('Passes'),
                    if (v.passes.isEmpty) const Text('No passes', style: TextStyle(color: Colors.black54)),
                    for (final p in v.passes)
                      ListTile(
                        dense: true,
                        leading: const Icon(Icons.card_membership),
                        title: Text('${p.passType} · ${p.status}'),
                        subtitle: Text('${p.startsOn ?? dateIst(p.startsAt)} → ${p.endsOn ?? dateIst(p.endsAt)}'),
                        trailing: Text(rupees(p.amountPaise)),
                      ),
                    const SectionTitle('Visits'),
                    for (final s in v.sessions)
                      ListTile(
                        dense: true,
                        leading: Icon(s.isOpen ? Icons.local_parking : Icons.history),
                        title: Text('${dateTimeIst(s.entryAt)} → ${s.exitAt == null ? '—' : timeIst(s.exitAt)}'),
                        subtitle: Text(
                          '${s.status}${s.estDurationMinutes != null ? ' · paid for ${durationLabel(s.estDurationMinutes!)}' : ''}'
                          '${s.entryGate != null ? ' · ${s.entryGate}' : ''}',
                        ),
                        trailing: s.chargePaise == null ? null : Text(rupees(s.chargePaise)),
                      ),
                    const SectionTitle('Payments'),
                    if (v.payments.isEmpty) const Text('No payments', style: TextStyle(color: Colors.black54)),
                    for (final p in v.payments)
                      ListTile(
                        dense: true,
                        leading: Icon(
                          p.mode == 'CASH' ? Icons.payments : Icons.qr_code_2,
                          color: p.status == PayStatus.confirmed ? paidGreen : Colors.grey,
                        ),
                        title: Text('${rupees(p.amountPaise)} ${p.mode} · ${p.status}'),
                        subtitle: Text(
                          '${dateTimeIst(p.createdAt)} · ${p.purpose}${p.receipt?.number != null ? ' · ${p.receipt!.number}' : ''}'
                          '${p.statusNote != null ? '\n${p.statusNote}' : ''}',
                        ),
                        isThreeLine: p.statusNote != null,
                        trailing: !isSup || p.status != PayStatus.confirmed
                            ? null
                            : TextButton(
                                onPressed: () => p.mode == 'CASH' ? _reverse(p) : _refund(p),
                                child: Text(
                                  p.mode == 'CASH' ? 'Reverse' : 'Refund',
                                  style: const TextStyle(color: dueRed),
                                ),
                              ),
                      ),
                    const SectionTitle('Ledger'),
                    for (final l in v.ledger)
                      ListTile(
                        dense: true,
                        title: Text(l.kind),
                        subtitle: Text('${dateTimeIst(l.createdAt)}${l.reason != null ? ' · ${l.reason}' : ''}'),
                        trailing: Text(
                          rupees(l.amountPaise),
                          style: TextStyle(color: l.amountPaise > 0 ? dueRed : paidGreen),
                        ),
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

  Widget _header(VehicleInfo v, AppState app) {
    final s = v.openSession;
    final canCollect = s != null && s.isOpen && app.user!.isCollector;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (v.plateImage != null)
              PlateImage(v.plateImage, width: double.infinity, height: 110, fit: BoxFit.contain),
            const SizedBox(height: 8),
            Center(child: PlateText(v.displayPlate, size: 26)),
            const SizedBox(height: 8),
            Wrap(
              alignment: WrapAlignment.center,
              spacing: 6,
              runSpacing: 4,
              children: [
                Badge2(v.vehicleClass, color: Colors.teal),
                if (v.activePass != null) Badge2('Pass till ${dateIst(v.activePass!.endsAt)}', color: Colors.purple),
                if (v.passCandidate) const Badge2('Pass candidate', color: Colors.purple),
                if (v.phone != null) const Badge2('Mobile on file', color: Colors.blueGrey, icon: Icons.phone),
              ],
            ),
            const SizedBox(height: 8),
            if (v.duePaise > 0) MoneyRow('Balance due', v.duePaise, color: dueRed, bold: true, size: 20),
            if (v.creditPaise > 0) MoneyRow('Credit', v.creditPaise, color: paidGreen, bold: true),
            if (v.duePaise == 0 && v.creditPaise == 0) const MoneyRow('Balance', 0),
            if (v.pendingClaimsPaise > 0)
              MoneyRow('UPI claims awaiting bank confirmation', v.pendingClaimsPaise, color: Colors.orange),
            Text(
              'First seen ${dateIst(v.firstSeen)} · last seen ${dateTimeIst(v.lastSeen)}',
              style: const TextStyle(color: Colors.black54, fontSize: 12),
            ),
            const SizedBox(height: 10),
            if (canCollect)
              BigButton(
                label: 'Collect for current visit',
                icon: Icons.point_of_sale,
                onPressed: () =>
                    Navigator.of(context)
                        .push(MaterialPageRoute(builder: (_) => CollectFlowScreen(target: targetFromVehicle(v))))
                        .then((_) => _load()),
              )
            else if (v.duePaise > 0 && app.user!.isCollector)
              BigButton(
                label: 'Collect dues ${rupees(v.duePaise)}',
                icon: Icons.point_of_sale,
                color: dueRed,
                onPressed: () =>
                    Navigator.of(context)
                        .push(MaterialPageRoute(builder: (_) => CollectFlowScreen(target: targetFromVehicle(v))))
                        .then((_) => _load()),
              ),
            if (app.user!.isCollector) ...[
              const SizedBox(height: 8),
              OutlinedButton.icon(
                icon: const Icon(Icons.card_membership),
                label: Text(v.activePass != null ? 'Renew pass' : 'Sell pass'),
                onPressed: () =>
                    Navigator.of(context)
                        .push(MaterialPageRoute(builder: (_) => PassSellScreen(target: targetFromVehicle(v))))
                        .then((_) => _load()),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
