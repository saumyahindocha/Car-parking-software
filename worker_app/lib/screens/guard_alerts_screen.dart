import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/models.dart';
import '../api/ws_client.dart';
import '../offline/local_store.dart';
import '../state/app_state.dart';
import '../util/format.dart';
import '../widgets/common.dart';

/// Guard mode: live unpaid-exit alerts with the plate image and amount due.
/// Guards never stop traffic — alerts are for identification and record; dues
/// are recovered at the vehicle's next entry.
class GuardAlertsScreen extends StatefulWidget {
  const GuardAlertsScreen({super.key});

  @override
  State<GuardAlertsScreen> createState() => _GuardAlertsScreenState();
}

class _GuardAlertsScreenState extends State<GuardAlertsScreen> {
  final Map<int, AlertInfo> _alerts = {};
  final Set<int> _disputed = {};
  bool _loading = false;
  String? _error;
  StreamSubscription<WsMessage>? _sub;
  Timer? _poll;

  AppState get _app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
    _sub = context.read<AppState>().ws?.topic('alert').listen(_onAlert);
    _poll = Timer.periodic(const Duration(seconds: 30), (_) => _load(silent: true));
  }

  @override
  void dispose() {
    _sub?.cancel();
    _poll?.cancel();
    super.dispose();
  }

  void _onAlert(WsMessage m) {
    final a = AlertInfo({...m.data, '_received_at': DateTime.now().toUtc().toIso8601String()});
    if (!a.isExitUnpaid) return;
    HapticFeedback.heavyImpact();
    SystemSound.play(SystemSoundType.alert);
    setState(() => _alerts[a.id] = a);
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) setState(() => _loading = true);
    try {
      final list = await _app.api.alerts(kind: 'EXIT_UNPAID', openOnly: true, hours: 12);
      if (!mounted) return;
      setState(() {
        _alerts
          ..clear()
          ..addEntries(list.map((a) => MapEntry(a.id, a)));
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = errorText(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _ack(AlertInfo a, {String? note}) async {
    try {
      await _app.api.ackAlert(a.id, note: note);
      setState(() => _alerts.remove(a.id));
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _customerSaysPaid(AlertInfo a) async {
    final res = await showModalBottomSheet<(String, int?, String?)>(
      context: context,
      isScrollControlled: true,
      builder: (c) => _DisputeSheet(alert: a),
    );
    if (res == null || !mounted) return;
    final (mode, paise, note) = res;
    final uuid = LocalStore.newUuid();
    try {
      await _app.api.raiseDispute({
        'alert_id': a.id,
        'vehicle_id': a.vehicleId,
        'session_id': a.sessionId,
        'claimed_mode': mode,
        'claimed_paise': ?paise,
        'note': ?note,
        'client_uuid': uuid,
      });
      if (mounted) showSnack(context, 'Dispute recorded for ${a.displayPlate}. The supervisor will check it.');
    } on NetworkException {
      if (a.vehicleId == null) {
        if (mounted) showSnack(context, 'Offline and the alert has no vehicle id; try again when online.', error: true);
        return;
      }
      await _app.enqueue(
        SyncType.dispute,
        {
          'vehicle_id': a.vehicleId,
          'session_id': a.sessionId,
          'alert_id': a.id,
          'claimed_mode': mode,
          'claimed_paise': ?paise,
          'note': ?note,
        },
        clientUuid: uuid,
        label: 'Dispute ${a.displayPlate} ($mode)',
      );
      if (mounted) showSnack(context, 'Offline: dispute saved and will be sent automatically.');
    } catch (e) {
      if (mounted) showError(context, e);
      return;
    }
    setState(() => _disputed.add(a.id));
    await _ack(a, note: 'Customer says paid ($mode)');
  }

  @override
  Widget build(BuildContext context) {
    final list = _alerts.values.toList()..sort((a, b) => b.id.compareTo(a.id));
    final gates = context.watch<AppState>().bootstrap?.gates ?? const {};
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.all(12), children: [
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(color: Colors.blue.shade50, borderRadius: BorderRadius.circular(10)),
          child: const Row(children: [
            Icon(Icons.info, color: upiBlue),
            SizedBox(width: 10),
            Expanded(
              child: Text(
                'Do NOT stop vehicles. These alerts are only to identify and note vehicles leaving with dues. '
                'Dues are recovered automatically at the next entry.',
                style: TextStyle(fontSize: 14),
              ),
            ),
          ]),
        ),
        if (_loading) const LinearProgressIndicator(),
        if (_error != null) Padding(padding: const EdgeInsets.all(8), child: Text(_error!, style: const TextStyle(color: Colors.deepOrange))),
        if (list.isEmpty && !_loading) const EmptyState('No unpaid exits right now.', icon: Icons.verified_user),
        for (final a in list)
          Card(
            elevation: 3,
            margin: const EdgeInsets.symmetric(vertical: 6),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                Row(children: [
                  PlateImage(a.plateImage, width: 140, height: 70),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      PlateText(a.displayPlate, size: 20),
                      const SizedBox(height: 4),
                      Text('Due ${rupees(a.amountDuePaise)}',
                          style: const TextStyle(color: dueRed, fontSize: 22, fontWeight: FontWeight.w900)),
                      Text('${gates[a.gateId] ?? a.gateId ?? ''} · ${timeIst(a.createdAt)}',
                          style: const TextStyle(color: Colors.black54)),
                    ]),
                  ),
                ]),
                const SizedBox(height: 10),
                Row(children: [
                  Expanded(
                    child: SizedBox(
                      height: 52,
                      child: FilledButton.icon(
                        style: FilledButton.styleFrom(backgroundColor: Colors.deepOrange),
                        onPressed: _disputed.contains(a.id) ? null : () => _customerSaysPaid(a),
                        icon: const Icon(Icons.record_voice_over),
                        label: const Text('Customer says paid'),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: SizedBox(
                      height: 52,
                      child: OutlinedButton.icon(
                        onPressed: () => _ack(a, note: 'Noted by guard'),
                        icon: const Icon(Icons.check),
                        label: const Text('Noted'),
                      ),
                    ),
                  ),
                ]),
              ]),
            ),
          ),
      ]),
    );
  }
}

class _DisputeSheet extends StatefulWidget {
  const _DisputeSheet({required this.alert});
  final AlertInfo alert;

  @override
  State<_DisputeSheet> createState() => _DisputeSheetState();
}

class _DisputeSheetState extends State<_DisputeSheet> {
  String _mode = 'CASH';
  final _amt = TextEditingController();
  final _note = TextEditingController();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 20, 20, MediaQuery.of(context).viewInsets.bottom + 20),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text('${widget.alert.displayPlate}: customer says they paid', style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w800)),
        const SizedBox(height: 12),
        SegmentedButton<String>(
          segments: const [
            ButtonSegment(value: 'CASH', label: Text('Cash'), icon: Icon(Icons.payments)),
            ButtonSegment(value: 'UPI', label: Text('UPI'), icon: Icon(Icons.qr_code_2)),
          ],
          selected: {_mode},
          onSelectionChanged: (s) => setState(() => _mode = s.first),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _amt,
          keyboardType: TextInputType.number,
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
          decoration: const InputDecoration(labelText: 'Amount they say they paid (optional)', prefixText: '₹ '),
        ),
        TextField(
          controller: _note,
          decoration: const InputDecoration(labelText: 'Note (optional) e.g. paid the worker near gate 2'),
        ),
        const SizedBox(height: 16),
        SizedBox(
          height: 54,
          child: FilledButton(
            onPressed: () {
              final rs = int.tryParse(_amt.text.trim());
              final note = _note.text.trim();
              Navigator.pop(context, (_mode, rs == null ? null : rs * 100, note.isEmpty ? null : note));
            },
            child: const Text('Raise dispute', style: TextStyle(fontSize: 17)),
          ),
        ),
      ]),
    );
  }
}
