import 'dart:convert';

import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';
import 'package:uuid/uuid.dart';

/// Offline action types accepted by `POST /api/sync` (backend api/worker.py).
class SyncType {
  static const cash = 'CASH';
  static const upiClaim = 'UPI_CLAIM';
  static const dispute = 'DISPUTE';
  static const handover = 'HANDOVER';
  static const receiptShown = 'RECEIPT_SHOWN';
  static const contact = 'CONTACT';
}

class QueueStatus {
  /// Waiting to be sent.
  static const pending = 'PENDING';

  /// The server rejected it; kept visible (with the error) for the supervisor.
  static const failed = 'FAILED';

  /// Accepted by the server (kept a few days as local history).
  static const done = 'DONE';
}

class QueueItem {
  QueueItem({
    required this.id,
    required this.clientUuid,
    required this.type,
    required this.data,
    required this.createdAt,
    required this.status,
    required this.userId,
    this.error,
    this.attempts = 0,
    this.amountPaise = 0,
    this.label,
    this.syncedAt,
    this.result,
  });

  final int id;
  final String clientUuid;
  final String type;
  final Map<String, dynamic> data;
  final DateTime createdAt;
  final String status;
  final int userId;
  final String? error;
  final int attempts;
  final int amountPaise;

  /// Short human description ("Cash ₹40 · MH43AB1234").
  final String? label;
  final DateTime? syncedAt;
  final Map<String, dynamic>? result;

  /// Item as sent to `/api/sync`.
  Map<String, dynamic> toSyncJson() => {
        'type': type,
        'client_uuid': clientUuid,
        'created_at': createdAt.toUtc().toIso8601String(),
        'data': data,
      };

  factory QueueItem.fromRow(Map<String, Object?> r) => QueueItem(
        id: r['id'] as int,
        clientUuid: r['client_uuid'] as String,
        type: r['type'] as String,
        data: Map<String, dynamic>.from(jsonDecode(r['data'] as String) as Map),
        createdAt: DateTime.parse(r['created_at'] as String),
        status: r['status'] as String,
        userId: (r['user_id'] as int?) ?? 0,
        error: r['error'] as String?,
        attempts: (r['attempts'] as int?) ?? 0,
        amountPaise: (r['amount_paise'] as int?) ?? 0,
        label: r['label'] as String?,
        syncedAt: r['synced_at'] == null ? null : DateTime.tryParse(r['synced_at'] as String),
        result: r['result'] == null ? null : Map<String, dynamic>.from(jsonDecode(r['result'] as String) as Map),
      );
}

/// SQLite storage on the phone: the offline action queue ("outbox") and a
/// key/value cache (bootstrap, to-collect list, cash holding) for offline use.
class LocalStore {
  LocalStore._(this.db);
  final Database db;
  static const _uuid = Uuid();

  static String newUuid() => _uuid.v4();

  /// Opens (or creates) the database. Tests pass `sqflite_common_ffi`'s factory
  /// and `inMemoryDatabasePath`.
  static Future<LocalStore> open({DatabaseFactory? factory, String? path}) async {
    final f = factory ?? databaseFactory;
    final dbPath = path ?? p.join(await f.getDatabasesPath(), 'parking_worker.db');
    final db = await f.openDatabase(dbPath,
        options: OpenDatabaseOptions(
          version: 1,
          onCreate: (db, _) async {
            await db.execute('''
              CREATE TABLE outbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_uuid TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                user_id INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                amount_paise INTEGER NOT NULL DEFAULT 0,
                label TEXT,
                synced_at TEXT,
                result TEXT
              )''');
            await db.execute('CREATE INDEX outbox_status ON outbox(status, id)');
            await db.execute('CREATE TABLE cache(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)');
          },
        ));
    return LocalStore._(db);
  }

  Future<void> close() => db.close();

  // ------------------------------------------------------------------ outbox
  /// Adds an action to the queue. Returns the stored item. If [clientUuid] was
  /// already queued (e.g. an online attempt timed out and is re-queued), the
  /// existing row is returned unchanged — the server is idempotent on it too.
  Future<QueueItem> enqueue({
    required String type,
    required Map<String, dynamic> data,
    required int userId,
    String? clientUuid,
    DateTime? createdAt,
    int amountPaise = 0,
    String? label,
  }) async {
    final uuid = clientUuid ?? newUuid();
    final existing = await byUuid(uuid);
    if (existing != null) return existing;
    final id = await db.insert('outbox', {
      'client_uuid': uuid,
      'type': type,
      'data': jsonEncode(data),
      'created_at': (createdAt ?? DateTime.now()).toUtc().toIso8601String(),
      'status': QueueStatus.pending,
      'user_id': userId,
      'amount_paise': amountPaise,
      'label': label,
    });
    return (await byId(id))!;
  }

  Future<QueueItem?> byId(int id) async {
    final r = await db.query('outbox', where: 'id = ?', whereArgs: [id]);
    return r.isEmpty ? null : QueueItem.fromRow(r.first);
  }

  Future<QueueItem?> byUuid(String uuid) async {
    final r = await db.query('outbox', where: 'client_uuid = ?', whereArgs: [uuid]);
    return r.isEmpty ? null : QueueItem.fromRow(r.first);
  }

  /// Items in creation order (the server applies them in the order sent).
  Future<List<QueueItem>> items({List<String>? statuses, int? userId, int? limit}) async {
    final where = <String>[];
    final args = <Object?>[];
    if (statuses != null && statuses.isNotEmpty) {
      where.add('status IN (${List.filled(statuses.length, '?').join(',')})');
      args.addAll(statuses);
    }
    if (userId != null) {
      where.add('user_id = ?');
      args.add(userId);
    }
    final rows = await db.query('outbox',
        where: where.isEmpty ? null : where.join(' AND '), whereArgs: args, orderBy: 'id ASC', limit: limit);
    return rows.map(QueueItem.fromRow).toList();
  }

  Future<List<QueueItem>> pending({int? userId, int? limit}) =>
      items(statuses: [QueueStatus.pending], userId: userId, limit: limit);

  Future<List<QueueItem>> failed({int? userId}) => items(statuses: [QueueStatus.failed], userId: userId);

  Future<List<QueueItem>> recentDone({int? userId, int limit = 50}) async {
    final rows = await db.query('outbox',
        where: userId == null ? 'status = ?' : 'status = ? AND user_id = ?',
        whereArgs: userId == null ? [QueueStatus.done] : [QueueStatus.done, userId],
        orderBy: 'id DESC',
        limit: limit);
    return rows.map(QueueItem.fromRow).toList();
  }

  Future<int> count(String status, {int? userId}) async {
    final r = await db.rawQuery(
        'SELECT COUNT(*) AS n FROM outbox WHERE status = ?${userId == null ? '' : ' AND user_id = ?'}',
        [status, ?userId]);
    return (r.first['n'] as int?) ?? 0;
  }

  /// Cash recorded on this phone that the server has not accepted yet
  /// (pending or failed CASH items). Counted towards the device cash-in-hand.
  Future<int> unsyncedCashPaise({int? userId}) async {
    final r = await db.rawQuery(
        "SELECT COALESCE(SUM(amount_paise), 0) AS s FROM outbox WHERE type = 'CASH' AND status IN ('PENDING','FAILED')"
        "${userId == null ? '' : ' AND user_id = ?'}",
        [?userId]);
    return (r.first['s'] as int?) ?? 0;
  }

  /// Session ids with a queued (not yet synced) payment: hidden from "To collect".
  Future<Set<int>> sessionsPaidLocally({int? userId}) async {
    final list = await items(statuses: [QueueStatus.pending, QueueStatus.failed], userId: userId);
    return {
      for (final i in list)
        if ((i.type == SyncType.cash || i.type == SyncType.upiClaim) && i.data['session_id'] is int)
          i.data['session_id'] as int,
    };
  }

  Future<void> markDone(String uuid, {Map<String, dynamic>? result}) => db.update(
      'outbox',
      {
        'status': QueueStatus.done,
        'error': null,
        'synced_at': DateTime.now().toUtc().toIso8601String(),
        'result': result == null ? null : jsonEncode(result),
      },
      where: 'client_uuid = ?',
      whereArgs: [uuid]);

  Future<void> markFailed(String uuid, String error) => db.rawUpdate(
      'UPDATE outbox SET status = ?, error = ?, attempts = attempts + 1 WHERE client_uuid = ?',
      [QueueStatus.failed, error, uuid]);

  Future<void> markAttempt(String uuid) =>
      db.rawUpdate('UPDATE outbox SET attempts = attempts + 1 WHERE client_uuid = ?', [uuid]);

  /// Put failed items back in the queue (the "Retry now" button).
  Future<int> requeueFailed({int? userId}) => db.update('outbox', {'status': QueueStatus.pending},
      where: userId == null ? 'status = ?' : 'status = ? AND user_id = ?',
      whereArgs: userId == null ? [QueueStatus.failed] : [QueueStatus.failed, userId]);

  /// Merge fields into a still-pending item's data (e.g. the customer gave a
  /// mobile number after an offline cash payment was queued). Returns false if
  /// the item was already sent.
  Future<bool> patchPending(String uuid, Map<String, dynamic> patch) async {
    final item = await byUuid(uuid);
    if (item == null || item.status != QueueStatus.pending) return false;
    final n = await db.update('outbox', {'data': jsonEncode({...item.data, ...patch})},
        where: 'client_uuid = ? AND status = ?', whereArgs: [uuid, QueueStatus.pending]);
    return n == 1;
  }

  Future<int> requeue(String uuid) =>
      db.update('outbox', {'status': QueueStatus.pending}, where: 'client_uuid = ?', whereArgs: [uuid]);

  /// Drops synced history older than [keep]. Pending and failed items are never pruned.
  Future<int> pruneDone({Duration keep = const Duration(days: 7)}) => db.delete('outbox',
      where: 'status = ? AND synced_at < ?',
      whereArgs: [QueueStatus.done, DateTime.now().toUtc().subtract(keep).toIso8601String()]);

  // ------------------------------------------------------------------ cache
  Future<void> putCache(String key, Object? value) => db.insert(
      'cache', {'key': key, 'value': jsonEncode(value), 'updated_at': DateTime.now().toUtc().toIso8601String()},
      conflictAlgorithm: ConflictAlgorithm.replace);

  Future<T?> getCache<T>(String key) async {
    final r = await db.query('cache', where: 'key = ?', whereArgs: [key]);
    if (r.isEmpty) return null;
    final v = jsonDecode(r.first['value'] as String);
    return v is T ? v : null;
  }

  Future<DateTime?> cacheTime(String key) async {
    final r = await db.query('cache', columns: ['updated_at'], where: 'key = ?', whereArgs: [key]);
    return r.isEmpty ? null : DateTime.tryParse(r.first['updated_at'] as String);
  }

  Future<void> clearCache() => db.delete('cache');
}
