import 'dart:async';

import 'package:flutter/foundation.dart';

import '../api/api_client.dart';
import 'local_store.dart';

/// Sends a batch to `POST /api/sync`. [ApiClient] is the real transport;
/// tests pass a fake.
abstract class SyncTransport {
  Future<Map<String, dynamic>> postSync(List<Map<String, dynamic>> items);
}

class ApiSyncTransport implements SyncTransport {
  ApiSyncTransport(this.api);
  final ApiClient api;
  @override
  Future<Map<String, dynamic>> postSync(List<Map<String, dynamic>> items) => api.sync(items);
}

class SyncOutcome {
  SyncOutcome({this.sent = 0, this.ok = 0, this.failed = 0, this.unreachable = false, this.error, this.cash});
  final int sent;
  final int ok;
  final int failed;
  final bool unreachable;
  final String? error;

  /// Latest cash holding returned by the server (null for guards).
  final Map<String, dynamic>? cash;
}

/// Flushes the offline queue to the server in order whenever it is reachable.
///
/// * Idempotent: every item carries its `client_uuid`; the server returns the
///   existing record for a repeated uuid, so re-sending after a lost response
///   is safe.
/// * Per-item errors mark only that item FAILED (kept visible with the error for
///   the supervisor); the rest of the batch still applies.
/// * Network errors leave everything PENDING for the next attempt.
class SyncService extends ChangeNotifier {
  SyncService(
    this.store,
    this.transport, {
    required this.userId,
    this.batchSize = 50,
    this.interval = const Duration(seconds: 20),
  });

  final LocalStore store;
  final SyncTransport transport;
  final int userId;
  final int batchSize;
  final Duration interval;

  /// Called after each successful round-trip with the server's cash holding.
  void Function(SyncOutcome outcome)? onSynced;

  Timer? _timer;
  bool _running = false;
  bool get running => _running;
  DateTime? lastAttempt;
  DateTime? lastSuccess;
  String? lastError;
  bool? reachable;
  int pendingCount = 0;
  int failedCount = 0;

  void start() {
    _timer?.cancel();
    _timer = Timer.periodic(interval, (_) => flush());
    refreshCounts();
    flush();
  }

  void stop() {
    _timer?.cancel();
    _timer = null;
  }

  Future<void> refreshCounts() async {
    pendingCount = await store.count(QueueStatus.pending, userId: userId);
    failedCount = await store.count(QueueStatus.failed, userId: userId);
    notifyListeners();
  }

  /// Sends all pending items (in batches, oldest first). With [retryFailed],
  /// failed items are put back in the queue first ("Retry now").
  Future<SyncOutcome> flush({bool retryFailed = false}) async {
    if (_running) return SyncOutcome();
    _running = true;
    notifyListeners();
    var sent = 0, ok = 0, failed = 0;
    Map<String, dynamic>? cash;
    try {
      if (retryFailed) await store.requeueFailed(userId: userId);
      while (true) {
        final batch = await store.pending(userId: userId, limit: batchSize);
        if (batch.isEmpty) break;
        lastAttempt = DateTime.now();
        final res = await _sendBatch(batch);
        sent += batch.length;
        ok += res.ok;
        failed += res.failed;
        cash = res.cash ?? cash;
        if (res.stalled) break; // nothing progressed; avoid a hot loop
      }
      if (sent > 0) reachable = true;
      lastError = failed > 0 ? '$failed item(s) rejected by the server' : null;
      if (sent > 0) lastSuccess = DateTime.now();
      await store.pruneDone();
      final outcome = SyncOutcome(sent: sent, ok: ok, failed: failed, cash: cash);
      if (sent > 0) onSynced?.call(outcome);
      return outcome;
    } on NetworkException catch (e) {
      reachable = false;
      lastError = e.toString();
      return SyncOutcome(sent: sent, ok: ok, failed: failed, unreachable: true, error: lastError);
    } on ApiException catch (e) {
      lastError = e.detail;
      return SyncOutcome(sent: sent, ok: ok, failed: failed, error: e.detail);
    } finally {
      _running = false;
      await refreshCounts();
    }
  }

  Future<_BatchResult> _sendBatch(List<QueueItem> batch) async {
    Map<String, dynamic> resp;
    try {
      resp = await transport.postSync(batch.map((i) => i.toSyncJson()).toList());
    } on ApiException catch (e) {
      if (e.statusCode == 422 && batch.length > 1) {
        // A malformed item fails validation for the whole request: isolate it.
        var ok = 0, failed = 0;
        Map<String, dynamic>? cash;
        for (final item in batch) {
          final r = await _sendBatch([item]);
          ok += r.ok;
          failed += r.failed;
          cash = r.cash ?? cash;
        }
        return _BatchResult(ok, failed, cash, stalled: false);
      }
      if (e.statusCode == 422) {
        await store.markFailed(batch.first.clientUuid, 'Rejected: ${e.detail}');
        return _BatchResult(0, 1, null, stalled: false);
      }
      rethrow;
    }
    final results = (resp['results'] as List?) ?? const [];
    final byUuid = <String, Map<String, dynamic>>{
      for (final r in results)
        if (r is Map && r['client_uuid'] != null) r['client_uuid'].toString(): Map<String, dynamic>.from(r),
    };
    var ok = 0, failed = 0;
    for (final item in batch) {
      final r = byUuid[item.clientUuid];
      if (r == null) {
        await store.markAttempt(item.clientUuid);
        continue; // server did not answer for it: stays pending
      }
      if (r['ok'] == true) {
        await store.markDone(item.clientUuid, result: r);
        ok++;
      } else {
        await store.markFailed(item.clientUuid, (r['error'] ?? 'rejected').toString());
        failed++;
      }
    }
    final cash = resp['cash'] is Map ? Map<String, dynamic>.from(resp['cash'] as Map) : null;
    return _BatchResult(ok, failed, cash, stalled: ok + failed == 0);
  }

  @override
  void dispose() {
    stop();
    super.dispose();
  }
}

class _BatchResult {
  _BatchResult(this.ok, this.failed, this.cash, {required this.stalled});
  final int ok;
  final int failed;
  final Map<String, dynamic>? cash;
  final bool stalled;
}
