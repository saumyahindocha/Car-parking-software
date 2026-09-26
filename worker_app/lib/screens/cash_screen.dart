import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/models.dart';
import '../api/ws_client.dart';
import '../state/app_state.dart';
import '../util/format.dart';
import '../widgets/common.dart';
import 'handover_screen.dart';

/// Live cash-in-hand against the limit (server + unsynced cash on this phone),
/// and the worker's handovers.
class CashScreen extends StatefulWidget {
  const CashScreen({super.key});

  @override
  State<CashScreen> createState() => _CashScreenState();
}

class _CashScreenState extends State<CashScreen> {
  List<HandoverInfo>? _handovers;
  String? _error;
  StreamSubscription<WsMessage>? _sub;

  AppState get _app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
    _sub = context.read<AppState>().ws?.messages.where((m) => m.topic == 'handover.confirmed').listen((_) => _load());
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    await _app.refreshCash();
    try {
      final h = await _app.api.myHandovers();
      if (mounted) {
        setState(() {
          _handovers = h;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = errorText(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final pos = app.cashPosition;
    final color = pos.blocked ? dueRed : (pos.warn ? Colors.amber.shade800 : paidGreen);
    final pending = _handovers?.where((h) => h.status == 'PENDING').toList() ?? [];
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.all(16), children: [
        if (!app.cashAllowed)
          const Card(
            child: ListTile(leading: Icon(Icons.block), title: Text('Cash collection is switched off for you. UPI only.')),
          ),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              const Text('Cash in hand', style: TextStyle(fontSize: 16, color: Colors.black54)),
              Text(rupees(pos.heldPaise), style: TextStyle(fontSize: 44, fontWeight: FontWeight.w900, color: color)),
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(6),
                child: LinearProgressIndicator(value: pos.fraction, minHeight: 14, color: color, backgroundColor: Colors.grey.shade200),
              ),
              const SizedBox(height: 6),
              Row(children: [
                Text('Limit ${rupees(pos.limitPaise)}'),
                const Spacer(),
                Text('Warning at ${rupees(pos.warnAtPaise)}', style: const TextStyle(color: Colors.black54)),
              ]),
              const Divider(height: 24),
              MoneyRow('Recorded on server', pos.serverHeldPaise),
              MoneyRow('Recorded offline, not yet synced', pos.unsyncedPaise, color: pos.unsyncedPaise > 0 ? Colors.deepOrange : null),
              if (pos.blocked)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: Text('Cash collection blocked until you hand over. UPI still works.',
                      style: TextStyle(color: dueRed, fontWeight: FontWeight.w700)),
                )
              else
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text('You can take ${rupees(pos.headroomPaise)} more in cash.'),
                ),
              if (app.serverCash?.zoneId != null)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text('Shift zone: ${app.bootstrap?.zoneName(app.serverCash!.zoneId) ?? app.serverCash!.zoneId}'),
                ),
            ]),
          ),
        ),
        const SizedBox(height: 12),
        BigButton(
          label: pending.isNotEmpty ? 'Handover waiting for supervisor' : 'Hand over cash',
          icon: Icons.handshake,
          onPressed: pending.isNotEmpty || pos.heldPaise <= 0
              ? null
              : () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const HandoverScreen())).then((_) => _load()),
        ),
        const SizedBox(height: 4),
        const Text('Hand over at the handover desk (under CCTV). The supervisor counts and confirms on their phone.',
            style: TextStyle(color: Colors.black54, fontSize: 12)),
        const SectionTitle('My handovers'),
        if (_error != null && _handovers == null) Text(_error!, style: const TextStyle(color: Colors.deepOrange)),
        if (_handovers != null && _handovers!.isEmpty) const Text('None yet', style: TextStyle(color: Colors.black54)),
        for (final h in _handovers ?? const <HandoverInfo>[])
          Card(
            child: ListTile(
              leading: Icon(
                h.status == 'CONFIRMED' ? Icons.verified : (h.status == 'PENDING' ? Icons.hourglass_top : Icons.cancel),
                color: h.status == 'CONFIRMED' ? paidGreen : (h.status == 'PENDING' ? Colors.orange : dueRed),
              ),
              title: Text('Declared ${rupees(h.declaredPaise)} · ${h.status}'),
              subtitle: Text([
                dateTimeIst(h.declaredAt),
                if (h.countedPaise != null) 'counted ${rupees(h.countedPaise)}',
                if ((h.variancePaise ?? 0) != 0) 'variance ${rupees(h.variancePaise)}',
                if (h.note != null) h.note!,
              ].join(' · ')),
            ),
          ),
      ]),
    );
  }
}
