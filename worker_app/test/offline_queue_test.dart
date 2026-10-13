// Offline queue (sqflite via sqflite_common_ffi) and the SyncService that
// flushes it to POST /api/sync.
import 'package:flutter_test/flutter_test.dart';
import 'package:parking_worker/api/api_client.dart';
import 'package:parking_worker/domain/cash_limit.dart';
import 'package:parking_worker/offline/local_store.dart';
import 'package:parking_worker/offline/sync_service.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Fake /api/sync: records requests and answers per a programmable rule.
class FakeTransport implements SyncTransport {
  FakeTransport(this.rule);
  Map<String, dynamic> Function(Map<String, dynamic> item) rule;
  final List<List<Map<String, dynamic>>> calls = [];
  Object? throwOnce;
  Map<String, dynamic>? cash = {'cash_in_hand_paise': 4000, 'limit_paise': 200000};

  @override
  Future<Map<String, dynamic>> postSync(List<Map<String, dynamic>> items) async {
    calls.add(items);
    final t = throwOnce;
    if (t != null) {
      throwOnce = null;
      throw t;
    }
    return {'results': items.map(rule).toList(), 'cash': cash};
  }
}

Map<String, dynamic> okRule(Map<String, dynamic> i) => {'client_uuid': i['client_uuid'], 'ok': true};

void main() {
  sqfliteFfiInit();
  late LocalStore store;

  setUp(() async {
    store = await LocalStore.open(factory: databaseFactoryFfi, path: inMemoryDatabasePath);
  });

  tearDown(() async => store.close());

  Future<QueueItem> cash(int paise, {int session = 1, int user = 5}) => store.enqueue(
    type: SyncType.cash,
    data: {
      'session_id': session,
      'vehicle_id': 10 + session,
      'duration_minutes': 240,
      'dues_paise': 0,
      'amount_paise': paise,
    },
    userId: user,
    amountPaise: paise,
  );

  group('LocalStore', () {
    test('enqueue keeps order, is idempotent on client_uuid', () async {
      final a = await cash(2000, session: 1);
      final b = await cash(3000, session: 2);
      final again = await store.enqueue(
        type: SyncType.cash,
        data: {'x': 1},
        userId: 5,
        clientUuid: a.clientUuid,
        amountPaise: 999,
      );
      expect(again.id, a.id);
      expect(again.amountPaise, 2000);
      final pending = await store.pending(userId: 5);
      expect(pending.map((e) => e.id), [a.id, b.id]);
      expect(await store.count(QueueStatus.pending, userId: 5), 2);
    });

    test('sync JSON has type, client_uuid, ISO created_at and data', () async {
      final a = await cash(2000);
      final j = a.toSyncJson();
      expect(j['type'], 'CASH');
      expect(j['client_uuid'], a.clientUuid);
      expect(DateTime.parse(j['created_at'] as String).isUtc, isTrue);
      expect((j['data'] as Map)['amount_paise'], 2000);
    });

    test('unsynced cash counts pending and failed CASH only, per user', () async {
      await cash(2000, session: 1);
      final b = await cash(3000, session: 2);
      await cash(700, session: 3, user: 6);
      await store.enqueue(type: SyncType.upiClaim, data: {'session_id': 4, 'amount_paise': 5000}, userId: 5);
      expect(await store.unsyncedCashPaise(userId: 5), 5000);
      await store.markFailed(b.clientUuid, 'amount does not match tariff');
      expect(await store.unsyncedCashPaise(userId: 5), 5000); // worker still holds failed cash
      await store.markDone(b.clientUuid);
      expect(await store.unsyncedCashPaise(userId: 5), 2000);
      expect(await store.sessionsPaidLocally(userId: 5), {1, 4});
    });

    test('patchPending only touches unsent items', () async {
      final a = await cash(2000);
      expect(await store.patchPending(a.clientUuid, {'phone': '9876543210'}), isTrue);
      expect((await store.byUuid(a.clientUuid))!.data['phone'], '9876543210');
      await store.markDone(a.clientUuid);
      expect(await store.patchPending(a.clientUuid, {'phone': '1'}), isFalse);
    });

    test('cache round trip', () async {
      await store.putCache('bootstrap', {
        'a': 1,
        'list': [1, 2],
      });
      expect(await store.getCache<Map<String, dynamic>>('bootstrap'), {
        'a': 1,
        'list': [1, 2],
      });
      await store.putCache('collect', [
        {'session_id': 1},
      ]);
      expect((await store.getCache<List<dynamic>>('collect'))!.length, 1);
      expect(await store.getCache<Map<String, dynamic>>('missing'), isNull);
    });

    test('requeue failed and prune done', () async {
      final a = await cash(100);
      await store.markFailed(a.clientUuid, 'x');
      expect(await store.requeueFailed(userId: 5), 1);
      expect((await store.byUuid(a.clientUuid))!.status, QueueStatus.pending);
      await store.markDone(a.clientUuid);
      expect(await store.pruneDone(keep: Duration.zero), 1);
    });
  });

  group('SyncService', () {
    test('flushes in order and marks items done; returns server cash', () async {
      final a = await cash(2000, session: 1);
      final b = await cash(3000, session: 2);
      final t = FakeTransport(okRule);
      final s = SyncService(store, t, userId: 5);
      final out = await s.flush();
      expect(out.ok, 2);
      expect(t.calls.single.map((e) => e['client_uuid']), [a.clientUuid, b.clientUuid]);
      expect(out.cash?['cash_in_hand_paise'], 4000);
      expect(await store.count(QueueStatus.pending, userId: 5), 0);
      expect(await store.unsyncedCashPaise(userId: 5), 0);
      expect(s.pendingCount, 0);
      expect(s.reachable, isTrue);
    });

    test('per-item error marks only that item failed', () async {
      final a = await cash(2000, session: 1);
      final b = await cash(9999, session: 2);
      final t = FakeTransport(
        (i) => i['client_uuid'] == b.clientUuid
            ? {'client_uuid': i['client_uuid'], 'ok': false, 'error': 'amount does not match tariff'}
            : okRule(i),
      );
      final s = SyncService(store, t, userId: 5);
      final out = await s.flush();
      expect(out.ok, 1);
      expect(out.failed, 1);
      expect((await store.byUuid(a.clientUuid))!.status, QueueStatus.done);
      final fb = (await store.byUuid(b.clientUuid))!;
      expect(fb.status, QueueStatus.failed);
      expect(fb.error, 'amount does not match tariff');
      expect(s.failedCount, 1);
      // failed items are not auto-retried...
      await s.flush();
      expect(t.calls.length, 1);
      // ...but "Retry now" resends them with the same client_uuid (idempotent server-side)
      t.rule = okRule;
      await s.flush(retryFailed: true);
      expect(t.calls.last.single['client_uuid'], b.clientUuid);
      expect(s.failedCount, 0);
    });

    test('network error keeps everything pending', () async {
      await cash(2000);
      final t = FakeTransport(okRule)..throwOnce = NetworkException('no route');
      final s = SyncService(store, t, userId: 5);
      final out = await s.flush();
      expect(out.unreachable, isTrue);
      expect(s.reachable, isFalse);
      expect(await store.count(QueueStatus.pending, userId: 5), 1);
      final out2 = await s.flush();
      expect(out2.ok, 1);
    });

    test('a 422 batch is split so one malformed item cannot block the queue', () async {
      final a = await cash(2000, session: 1);
      final bad = await store.enqueue(type: 'CASH', data: {'broken': true}, userId: 5);
      final c = await cash(1000, session: 3);
      final s = SyncService(store, _Validating(FakeTransport(okRule).postSync, bad.clientUuid), userId: 5);
      final out = await s.flush();
      expect(out.ok, 2);
      expect(out.failed, 1);
      expect((await store.byUuid(a.clientUuid))!.status, QueueStatus.done);
      expect((await store.byUuid(c.clientUuid))!.status, QueueStatus.done);
      expect((await store.byUuid(bad.clientUuid))!.status, QueueStatus.failed);
    });

    test('items with no result stay pending (no hot loop)', () async {
      await cash(2000);
      final t = FakeTransport(okRule);
      final s = SyncService(store, _NoResults(t), userId: 5);
      final out = await s.flush();
      expect(out.ok, 0);
      expect(await store.count(QueueStatus.pending, userId: 5), 1);
      expect((await store.pending(userId: 5)).single.attempts, 1);
    });

    test('only the logged-in user\'s items are sent', () async {
      await cash(2000, user: 5);
      await cash(3000, user: 6);
      final t = FakeTransport(okRule);
      await SyncService(store, t, userId: 6).flush();
      expect(t.calls.single.length, 1);
      expect(await store.count(QueueStatus.pending, userId: 5), 1);
    });
  });

  group('device cash-in-hand limit', () {
    test('server + unsynced, 80% warning, block at limit', () {
      const p = CashPosition(serverHeldPaise: 150000, unsyncedPaise: 10000, limitPaise: 200000, warnRatio: 0.8);
      expect(p.heldPaise, 160000);
      expect(p.warn, isTrue);
      expect(p.blocked, isFalse);
      expect(p.canCollect(40000), isTrue); // exactly at the limit is allowed (backend: > limit refused)
      expect(p.canCollect(40001), isFalse);
      const full = CashPosition(serverHeldPaise: 200000, unsyncedPaise: 0, limitPaise: 200000);
      expect(full.blocked, isTrue);
      expect(full.canCollect(1), isFalse);
      const low = CashPosition(serverHeldPaise: 0, unsyncedPaise: 159999, limitPaise: 200000);
      expect(low.warn, isFalse);
    });
  });
}

/// Simulates FastAPI request validation: a batch containing [badUuid] gets 422.
class _Validating implements SyncTransport {
  _Validating(this.inner, this.badUuid);
  final Future<Map<String, dynamic>> Function(List<Map<String, dynamic>>) inner;
  final String badUuid;
  @override
  Future<Map<String, dynamic>> postSync(List<Map<String, dynamic>> items) async {
    if (items.any((i) => i['client_uuid'] == badUuid)) {
      throw ApiException(422, 'items.1.data: field required');
    }
    return inner(items);
  }
}

class _NoResults implements SyncTransport {
  _NoResults(this.inner);
  final SyncTransport inner;
  @override
  Future<Map<String, dynamic>> postSync(List<Map<String, dynamic>> items) async => {'results': [], 'cash': null};
}
