import 'dart:async';

import 'package:flutter/foundation.dart';

import '../api/api_client.dart';
import '../api/models.dart';
import '../api/ws_client.dart';
import 'app_state.dart';

/// The live "To collect" list: WebSocket-driven, polled every 30 s, pull to
/// refresh, and cached in SQLite so it still shows when the server is down.
class CollectModel extends ChangeNotifier {
  CollectModel(this.app);

  final AppState app;
  List<CollectItem> _all = [];
  Set<int> _paidLocally = {};
  bool loading = false;
  String? error;
  DateTime? updatedAt;
  bool fromCache = false;
  bool onlyMyZone = false;
  Timer? _poll;
  Timer? _debounce;
  StreamSubscription<WsMessage>? _sub;
  bool _disposed = false;

  static const _topics = {'session.opened', 'session.closed', 'payment.updated', 'pass.activated'};

  List<CollectItem> get items {
    final zone = app.bootstrap?.zone?.id;
    final list = _all.where((i) => !_paidLocally.contains(i.sessionId)).where((i) => !onlyMyZone || zone == null || i.zoneId == zone).toList();
    list.sort((a, b) => (b.entryAt ?? DateTime(0)).compareTo(a.entryAt ?? DateTime(0)));
    return list;
  }

  CollectItem? bySession(int id) {
    for (final i in _all) {
      if (i.sessionId == id) return i;
    }
    return null;
  }

  Future<void> start() async {
    final cached = await app.store.getCache<List<dynamic>>(CacheKeys.collect);
    if (cached != null && _all.isEmpty) {
      _all = cached.map((e) => CollectItem(Map<String, dynamic>.from(e as Map))).toList();
      updatedAt = await app.store.cacheTime(CacheKeys.collect);
      fromCache = true;
      _notify();
    }
    _sub = app.ws?.messages.where((m) => _topics.contains(m.topic)).listen(_onEvent);
    _poll = Timer.periodic(const Duration(seconds: 30), (_) => refresh(silent: true));
    await refresh();
  }

  void _onEvent(WsMessage m) {
    // Paid sessions disappear immediately; everything else triggers a debounced refresh.
    if (m.topic == 'payment.updated') {
      final sid = m.data['session_id'];
      final st = m.data['status'];
      if (sid is int && (st == PayStatus.confirmed || st == PayStatus.claimedOffline)) {
        _all.removeWhere((i) => i.sessionId == sid);
        _notify();
      }
    }
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 800), () => refresh(silent: true));
  }

  Future<void> refresh({bool silent = false}) async {
    if (_disposed) return;
    if (!silent) {
      loading = true;
      _notify();
    }
    try {
      final list = await app.api.collectList();
      _all = list.map((e) => CollectItem(Map<String, dynamic>.from(e as Map))).toList();
      await app.store.putCache(CacheKeys.collect, list);
      updatedAt = DateTime.now();
      fromCache = false;
      error = null;
    } on NetworkException {
      fromCache = true;
      error = 'Offline: showing the list from ${updatedAt == null ? 'cache' : 'the last update'}';
    } on ApiException catch (e) {
      error = e.detail;
    }
    if (app.user != null) _paidLocally = await app.store.sessionsPaidLocally(userId: app.user!.id);
    loading = false;
    _notify();
  }

  /// Hide a session as soon as the worker has collected for it.
  void markCollected(int sessionId) {
    _all.removeWhere((i) => i.sessionId == sessionId);
    _notify();
  }

  void setOnlyMyZone(bool v) {
    onlyMyZone = v;
    _notify();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _poll?.cancel();
    _debounce?.cancel();
    _sub?.cancel();
    super.dispose();
  }
}
