import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../offline/local_store.dart';
import '../state/app_state.dart';
import '../util/format.dart';
import '../widgets/common.dart';

/// Connectivity and offline-sync status: server reachability, live link, queue
/// length, last sync, failed items (with the server's reason — show these to
/// the supervisor), and "Retry now".
class SyncStatusScreen extends StatefulWidget {
  const SyncStatusScreen({super.key, this.embedded = false});
  final bool embedded;

  @override
  State<SyncStatusScreen> createState() => _SyncStatusScreenState();
}

class _SyncStatusScreenState extends State<SyncStatusScreen> {
  List<QueueItem> _pending = [];
  List<QueueItem> _failed = [];
  List<QueueItem> _done = [];
  bool _checking = false;
  Timer? _t;

  AppState get _app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
    _t = Timer.periodic(const Duration(seconds: 5), (_) => _load());
  }

  @override
  void dispose() {
    _t?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final uid = _app.user?.id;
    if (uid == null) return;
    final p = await _app.store.pending(userId: uid);
    final f = await _app.store.failed(userId: uid);
    final d = await _app.store.recentDone(userId: uid, limit: 30);
    if (mounted) {
      setState(() {
        _pending = p;
        _failed = f;
        _done = d;
      });
    }
  }

  Future<void> _retry() async {
    setState(() => _checking = true);
    final ok = await _app.api.health();
    final out = await _app.sync?.flush(retryFailed: true);
    await _app.refreshUnsynced();
    await _load();
    if (!mounted) return;
    setState(() => _checking = false);
    if (!ok || (out?.unreachable ?? false)) {
      showSnack(context, 'Server still unreachable. Items are safe on this phone.', error: true);
    } else {
      showSnack(context, 'Synced ${out?.ok ?? 0} item(s)${(out?.failed ?? 0) > 0 ? ', ${out!.failed} failed' : ''}');
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final sync = app.sync;
    final list = ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: Column(
            children: [
              ListTile(
                leading: Icon(
                  app.online ? Icons.cloud_done : Icons.cloud_off,
                  color: app.online ? paidGreen : dueRed,
                  size: 32,
                ),
                title: Text(
                  app.online ? 'Server reachable' : 'Server unreachable',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                subtitle: Text('${app.serverUrl}\nLast contact ${ago(app.lastOnline)}'),
                isThreeLine: true,
              ),
              ListTile(
                leading: Icon(
                  app.ws?.connected == true ? Icons.bolt : Icons.bolt_outlined,
                  color: app.ws?.connected == true ? paidGreen : Colors.grey,
                ),
                title: Text(
                  app.ws?.connected == true ? 'Live updates connected' : 'Live updates disconnected (polling)',
                ),
              ),
              ListTile(
                leading: const Icon(Icons.outbox),
                title: Text('${sync?.pendingCount ?? 0} waiting · ${sync?.failedCount ?? 0} failed'),
                subtitle: Text(
                  'Last sync ${ago(sync?.lastSuccess)}${sync?.lastError != null ? '\n${sync!.lastError}' : ''}',
                ),
                isThreeLine: sync?.lastError != null,
              ),
              if (app.unsyncedCashPaise > 0)
                ListTile(
                  leading: const Icon(Icons.payments, color: Colors.deepOrange),
                  title: Text('${rupees(app.unsyncedCashPaise)} cash recorded offline, not yet on the server'),
                ),
              Padding(
                padding: const EdgeInsets.all(12),
                child: BigButton(
                  label: _checking || (sync?.running ?? false) ? 'Syncing…' : 'Retry now',
                  icon: Icons.sync,
                  onPressed: _checking ? null : _retry,
                ),
              ),
            ],
          ),
        ),
        if (_failed.isNotEmpty) ...[
          const SectionTitle('Failed — show your supervisor'),
          for (final i in _failed) _tile(i, Colors.deepOrange),
        ],
        if (_pending.isNotEmpty) ...[
          const SectionTitle('Waiting to sync'),
          for (final i in _pending) _tile(i, Colors.blueGrey),
        ],
        if (_done.isNotEmpty) ...[const SectionTitle('Recently synced'), for (final i in _done) _tile(i, paidGreen)],
        const SizedBox(height: 16),
        Text(
          'Device ${app.deviceId}',
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.black38, fontSize: 11),
        ),
      ],
    );
    if (widget.embedded) return RefreshIndicator(onRefresh: _retry, child: list);
    return Scaffold(
      appBar: AppBar(title: const Text('Connection & sync')),
      body: list,
    );
  }

  Widget _tile(QueueItem i, Color c) {
    final icon = switch (i.type) {
      SyncType.cash => Icons.payments,
      SyncType.upiClaim => Icons.qr_code_2,
      SyncType.dispute => Icons.report,
      SyncType.handover => Icons.handshake,
      SyncType.receiptShown => Icons.receipt_long,
      SyncType.contact => Icons.contact_phone,
      SyncType.alertAck => Icons.notifications_off,
      SyncType.plateCorrection => Icons.edit,
      SyncType.shiftOpen => Icons.play_arrow,
      SyncType.shiftClose => Icons.stop,
      _ => Icons.sync,
    };
    return Card(
      child: ListTile(
        leading: Icon(icon, color: c),
        title: Text(i.label ?? i.type),
        subtitle: Text(
          [
            dateTimeIst(i.createdAt),
            if (i.attempts > 0) '${i.attempts} attempt(s)',
            if (i.error != null) i.error!,
          ].join(' · '),
        ),
        trailing: i.status == QueueStatus.failed
            ? IconButton(
                icon: const Icon(Icons.replay),
                tooltip: 'Retry',
                onPressed: () async {
                  await _app.store.requeue(i.clientUuid);
                  await _app.sync?.flush();
                  await _load();
                },
              )
            : null,
      ),
    );
  }
}
