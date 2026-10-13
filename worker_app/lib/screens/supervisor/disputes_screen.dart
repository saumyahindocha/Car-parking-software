import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../api/models.dart';
import '../../state/app_state.dart';
import '../../util/format.dart';
import '../../widgets/common.dart';
import '../vehicle_screen.dart';

/// Open payment disputes (raised by guards, workers or customers). Each is
/// resolved UPHELD / REJECTED / UNRESOLVED with a note; an upheld claim can
/// credit the vehicle's balance.
class DisputesScreen extends StatefulWidget {
  const DisputesScreen({super.key});

  @override
  State<DisputesScreen> createState() => _DisputesScreenState();
}

class _DisputesScreenState extends State<DisputesScreen> {
  List<DisputeInfo>? _rows;
  String? _error;
  bool _openOnly = true;

  AppState get _app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await _app.api.disputes(status: _openOnly ? 'OPEN' : null);
      if (mounted) {
        setState(() {
          _rows = r;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = errorText(e));
    }
  }

  Future<void> _resolve(DisputeInfo d) async {
    final res = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _ResolveSheet(dispute: d),
    );
    if (res == true) await _load();
  }

  @override
  Widget build(BuildContext context) {
    final boot = context.watch<AppState>().bootstrap;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Disputes'),
        actions: [
          Row(
            children: [
              const Text('Open only'),
              Switch(
                value: _openOnly,
                onChanged: (v) {
                  setState(() => _openOnly = v);
                  _load();
                },
              ),
            ],
          ),
        ],
      ),
      body: Column(
        children: [
          const ConnectivityBar(),
          if (_error != null) Text(_error!, style: const TextStyle(color: dueRed)),
          Expanded(
            child: RefreshIndicator(
              onRefresh: _load,
              child: _rows == null
                  ? ListView(
                      children: const [
                        Padding(
                          padding: EdgeInsets.all(32),
                          child: Center(child: CircularProgressIndicator()),
                        ),
                      ],
                    )
                  : _rows!.isEmpty
                  ? ListView(children: const [EmptyState('No disputes')])
                  : ListView(
                      children: [
                        for (final d in _rows!)
                          Card(
                            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
                            child: Padding(
                              padding: const EdgeInsets.all(12),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    children: [
                                      PlateText(d.plate, size: 16),
                                      const Spacer(),
                                      Badge2(d.status, color: d.status == 'OPEN' ? Colors.orange : Colors.blueGrey),
                                    ],
                                  ),
                                  const SizedBox(height: 6),
                                  Text(
                                    'Says paid by ${d.claimedMode}${d.claimedPaise != null ? ' ${rupees(d.claimedPaise)}' : ''}'
                                    ' · raised by ${d.raisedByRole} · ${dateTimeIst(d.createdAt)}',
                                  ),
                                  Text(
                                    'Worker on duty: ${d.workerName ?? '—'}${d.zoneId != null ? ' · ${boot?.zoneName(d.zoneId) ?? d.zoneId}' : ''}',
                                  ),
                                  if (d.balancePaise != null)
                                    Text(
                                      'Current balance ${rupees(d.balancePaise)}',
                                      style: TextStyle(color: (d.balancePaise ?? 0) > 0 ? dueRed : null),
                                    ),
                                  if (d.note != null)
                                    Text('Note: ${d.note}', style: const TextStyle(color: Colors.black54)),
                                  if (d.resolutionNote != null) Text('Resolution: ${d.resolutionNote}'),
                                  Row(
                                    children: [
                                      TextButton(
                                        onPressed: () => Navigator.of(context).push(
                                          MaterialPageRoute(builder: (_) => VehicleScreen(vehicleId: d.vehicleId)),
                                        ),
                                        child: const Text('History'),
                                      ),
                                      const Spacer(),
                                      if (d.status == 'OPEN')
                                        FilledButton(onPressed: () => _resolve(d), child: const Text('Resolve')),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                          ),
                      ],
                    ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ResolveSheet extends StatefulWidget {
  const _ResolveSheet({required this.dispute});
  final DisputeInfo dispute;

  @override
  State<_ResolveSheet> createState() => _ResolveSheetState();
}

class _ResolveSheetState extends State<_ResolveSheet> {
  String _outcome = 'UPHELD';
  final _note = TextEditingController();
  late final TextEditingController _credit;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    final c = widget.dispute.claimedPaise;
    _credit = TextEditingController(text: c == null ? '' : '${c ~/ 100}');
  }

  Future<void> _submit() async {
    setState(() => _busy = true);
    final rs = int.tryParse(_credit.text.trim());
    try {
      await context.read<AppState>().api.resolveDispute(
        widget.dispute.id,
        _outcome,
        _note.text.trim(),
        adjustPaise: _outcome == 'UPHELD' && rs != null && rs > 0 ? -rs * 100 : null,
      );
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 20, 20, MediaQuery.of(context).viewInsets.bottom + 20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            'Resolve dispute #${widget.dispute.id} (${widget.dispute.plate})',
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 12),
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'UPHELD', label: Text('Upheld')),
              ButtonSegment(value: 'REJECTED', label: Text('Rejected')),
              ButtonSegment(value: 'UNRESOLVED', label: Text('Unresolved')),
            ],
            selected: {_outcome},
            onSelectionChanged: (s) => setState(() => _outcome = s.first),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: _note,
            maxLines: 2,
            decoration: const InputDecoration(labelText: 'Resolution note (required)'),
            onChanged: (_) => setState(() {}),
          ),
          if (_outcome == 'UPHELD')
            TextField(
              controller: _credit,
              keyboardType: TextInputType.number,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              decoration: const InputDecoration(
                labelText: 'Credit to the vehicle balance (₹, optional)',
                prefixText: '₹ ',
              ),
            ),
          const SizedBox(height: 16),
          SizedBox(
            height: 52,
            child: FilledButton(
              onPressed: _busy || _note.text.trim().isEmpty ? null : _submit,
              child: const Text('Save resolution'),
            ),
          ),
        ],
      ),
    );
  }
}
