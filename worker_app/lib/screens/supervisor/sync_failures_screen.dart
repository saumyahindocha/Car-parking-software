import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../api/models.dart';
import '../../state/app_state.dart';
import '../../util/format.dart';
import '../../widgets/common.dart';
import '../../widgets/dialogs.dart';

/// Offline actions from any phone that the server could not apply (SYNC_FAILED
/// alerts). The worker still sees them on their phone; the supervisor follows
/// up (e.g. cash recorded offline with a wrong amount) and marks them handled.
class SyncFailuresScreen extends StatefulWidget {
  const SyncFailuresScreen({super.key});

  @override
  State<SyncFailuresScreen> createState() => _SyncFailuresScreenState();
}

class _SyncFailuresScreenState extends State<SyncFailuresScreen> {
  List<AlertInfo>? _rows;
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
      final r = await _app.api.alerts(kind: 'SYNC_FAILED', openOnly: _openOnly, hours: 24 * 7);
      final acked = await _app.locallyAckedAlerts();
      if (mounted) {
        setState(() {
          _rows = _openOnly ? r.where((a) => !acked.contains(a.id)).toList() : r;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = errorText(e));
    }
  }

  Future<void> _handled(AlertInfo a) async {
    final note = await textPrompt(
      context,
      'Mark as handled',
      label: 'What was done (e.g. amount corrected, cash counted)',
    );
    if (note == null) return;
    try {
      await _app.ackAlert(a.id, note: note);
      await _load();
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Failed phone syncs'),
        actions: [
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
                  ? ListView(children: const [EmptyState('No failed syncs', icon: Icons.cloud_done)])
                  : ListView(
                      children: [
                        for (final a in _rows!)
                          Card(
                            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
                            child: ListTile(
                              leading: const Icon(Icons.sync_problem, color: Colors.deepOrange),
                              title: Text(a.message),
                              subtitle: Text(
                                [
                                  dateTimeIst(a.createdAt),
                                  if (a.data['type'] != null) '${a.data['type']}',
                                  if (a.data['data'] is Map) _brief(Map<String, dynamic>.from(a.data['data'] as Map)),
                                  if (a.acknowledgedAt != null) 'handled: ${a.note ?? ''}',
                                ].join(' · '),
                              ),
                              isThreeLine: true,
                              trailing: a.acknowledgedAt == null
                                  ? TextButton(onPressed: () => _handled(a), child: const Text('Handled'))
                                  : null,
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

  static String _brief(Map<String, dynamic> d) => [
    if (d['amount_paise'] is num) rupees((d['amount_paise'] as num).toInt()),
    if (d['session_id'] != null) 'session ${d['session_id']}',
    if (d['vehicle_id'] != null) 'vehicle ${d['vehicle_id']}',
    if (d['plate'] != null) '${d['plate']}',
    if (d['txn_ref'] != null) '${d['txn_ref']}',
  ].join(', ');
}
