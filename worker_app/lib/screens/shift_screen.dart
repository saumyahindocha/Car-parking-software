import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/models.dart';
import '../offline/local_store.dart';
import '../state/app_state.dart';
import '../util/format.dart';
import '../widgets/common.dart';
import '../widgets/dialogs.dart';

/// Shift open/close with UPI and cash totals, and the worker's assigned zone.
class ShiftScreen extends StatefulWidget {
  const ShiftScreen({super.key, this.standalone = false});

  /// Pushed as its own page (supervisor menu) rather than shown in a tab.
  final bool standalone;

  @override
  State<ShiftScreen> createState() => _ShiftScreenState();
}

class _ShiftScreenState extends State<ShiftScreen> {
  ShiftInfo? _shift;
  bool _loading = true;
  bool _busy = false;
  String? _error;
  bool _fromCache = false;
  Map<String, dynamic>? _closed;
  String? _queued;

  AppState get _app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final s = await _app.api.currentShift();
      await _app.store.putCache(CacheKeys.shift, s?.raw);
      await _app.refreshBootstrap();
      if (mounted) {
        setState(() {
          _shift = s;
          _error = null;
          _fromCache = false;
        });
      }
    } on NetworkException {
      final c = await _app.store.getCache<Map<String, dynamic>>(CacheKeys.shift);
      if (mounted) {
        setState(() {
          _shift = c == null ? null : ShiftInfo(c);
          _fromCache = true;
          _error = 'Offline: showing the last known shift. Cash still works offline (a shift opens automatically).';
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = errorText(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _open() async {
    final boot = _app.bootstrap;
    int? zoneId = boot?.zone?.id;
    if (zoneId == null && (boot?.zones.isNotEmpty ?? false)) {
      zoneId = await showDialog<int>(
        context: context,
        builder: (c) => SimpleDialog(
          title: const Text('Which zone are you covering?'),
          children: [
            for (final z in boot!.zones)
              SimpleDialogOption(onPressed: () => Navigator.pop(c, z.id), child: Text(z.name)),
            SimpleDialogOption(onPressed: () => Navigator.pop(c, -1), child: const Text('No zone')),
          ],
        ),
      );
      if (zoneId == null) return;
      if (zoneId == -1) zoneId = null;
    }
    setState(() => _busy = true);
    try {
      if (!_app.online) throw NetworkException('offline');
      await _app.api.openShift(zoneId: zoneId);
      await _load();
    } on NetworkException {
      await _app.enqueue(SyncType.shiftOpen, {'zone_id': ?zoneId}, label: 'Open shift');
      if (mounted) {
        setState(
          () => _queued = 'Shift opening saved offline — it opens on the server at this time when the phone syncs.',
        );
      }
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _close() async {
    if ((_app.sync?.pendingCount ?? 0) > 0 && _app.online) {
      // Send queued cash etc. first so the server's totals are complete.
      await _app.sync?.flush();
      if (!mounted) return;
      final left = _app.sync?.pendingCount ?? 0;
      if (left > 0 && _app.online) {
        showSnack(context, '$left offline item(s) could not be synced yet. Try again in a moment.', error: true);
        return;
      }
    }
    final held = _app.cashPosition.heldPaise;
    String? note;
    if (held > 0) {
      note = await textPrompt(
        context,
        '${rupees(held)} still in hand',
        label: 'Hand it over first, or explain why (logged as a variance)',
        maxLines: 3,
      );
      if (note == null) return;
    } else {
      final ok = await confirmDialog(
        context,
        'Close shift?',
        'Totals are locked and sent to the supervisor.',
        ok: 'Close shift',
      );
      if (!ok) return;
    }
    setState(() => _busy = true);
    try {
      if (!_app.online) throw NetworkException('offline');
      final r = await _app.api.closeShift(note: note);
      await _app.refreshCash();
      if (mounted) setState(() => _closed = r);
      await _load();
    } on NetworkException {
      // Queued after every earlier offline item, so they are applied first.
      await _app.enqueue(SyncType.shiftClose, {'note': ?note}, label: 'Close shift');
      if (mounted) {
        setState(
          () => _queued =
              'Shift close saved offline — it is applied when the phone syncs. '
              'If the server refuses it (e.g. a handover still pending), your supervisor is alerted.',
        );
      }
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final zone = app.bootstrap?.zone;
    final s = _shift;
    final content = RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: ListTile(
              leading: const Icon(Icons.map, size: 36),
              title: Text(
                zone == null ? 'No zone assigned right now' : 'Your zone: ${zone.name}',
                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
              ),
              subtitle: Text(
                zone?.until == null ? 'Ask your supervisor for your zone.' : 'Until ${dateTimeIst(zone!.until)}',
              ),
            ),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(8),
              child: Text(_error!, style: const TextStyle(color: Colors.deepOrange)),
            ),
          if (_loading && s == null)
            const Padding(
              padding: EdgeInsets.all(24),
              child: Center(child: CircularProgressIndicator()),
            ),
          if (_queued != null)
            Card(
              color: Colors.orange.shade50,
              child: ListTile(
                leading: const Icon(Icons.cloud_off, color: Colors.deepOrange),
                title: Text(_queued!),
              ),
            ),
          if (_closed != null)
            Card(
              color: Colors.green.shade50,
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Shift closed', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
                    MoneyRow('UPI', (_closed!['upi_total_paise'] as num?)?.toInt() ?? 0),
                    MoneyRow('Cash', (_closed!['cash_total_paise'] as num?)?.toInt() ?? 0),
                    MoneyRow('Handed over', (_closed!['handed_over_paise'] as num?)?.toInt() ?? 0),
                    MoneyRow('Variance', (_closed!['variance_paise'] as num?)?.toInt() ?? 0, color: dueRed),
                  ],
                ),
              ),
            ),
          if (!_loading && s == null && _closed == null)
            Column(
              children: [
                const EmptyState('No shift open.', icon: Icons.schedule),
                BigButton(label: 'Open shift', icon: Icons.play_arrow, onPressed: _busy ? null : _open),
              ],
            ),
          if (s != null) ...[
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Shift open since ${dateTimeIst(s.openedAt)}',
                      style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
                    ),
                    if (s.zoneId != null) Text('Zone: ${app.bootstrap?.zoneName(s.zoneId) ?? s.zoneId}'),
                    const Divider(height: 24),
                    MoneyRow('UPI (${s.upiCount})', s.upiPaise, color: upiBlue, bold: true, size: 20),
                    MoneyRow('Cash (${s.cashCount})', s.cashPaise, color: paidGreen, bold: true, size: 20),
                    const Divider(),
                    MoneyRow('Handed over', s.handedOverPaise),
                    if (s.pendingHandoverPaise > 0)
                      MoneyRow('Handover awaiting count', s.pendingHandoverPaise, color: Colors.orange),
                    MoneyRow('Cash in hand (incl. unsynced)', app.cashPosition.heldPaise, bold: true),
                    if (_fromCache)
                      const Text('(last known — offline)', style: TextStyle(color: Colors.deepOrange, fontSize: 12)),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),
            BigButton(
              label: 'Close shift',
              icon: Icons.stop,
              color: dueRed,
              outlined: true,
              onPressed: _busy ? null : _close,
            ),
          ],
        ],
      ),
    );
    if (widget.standalone) {
      return Scaffold(
        appBar: AppBar(title: const Text('My shift')),
        body: Column(
          children: [
            const ConnectivityBar(),
            Expanded(child: content),
          ],
        ),
      );
    }
    return content;
  }
}
