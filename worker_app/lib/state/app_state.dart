import 'dart:async';
import 'dart:convert';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/api_client.dart';
import '../api/models.dart';
import '../api/ws_client.dart';
import '../domain/cash_limit.dart';
import '../offline/local_store.dart';
import '../offline/sync_service.dart';
import 'collect_model.dart';

const String defaultServerUrl = 'http://192.168.10.2:8000';

class _Keys {
  static const server = 'server_url';
  static const deviceId = 'device_id';
  static const token = 'token';
  static const user = 'user_json';
  static const lastUsername = 'last_username';
}

class CacheKeys {
  static const bootstrap = 'bootstrap';
  static const collect = 'collect_list';
  static const cash = 'my_cash';
  static const shift = 'shift_current';
}

/// App-wide state: server config, device identity, login, cached bootstrap,
/// connectivity, WebSocket, offline queue + sync, and the device-side cash position.
class AppState extends ChangeNotifier {
  AppState._(this.prefs, this.store, this.api, this.deviceId);

  final SharedPreferences prefs;
  final LocalStore store;
  final ApiClient api;

  /// Persisted per-install UUID sent at login; the backend binds each
  /// worker/guard account to one phone.
  final String deviceId;

  UserInfo? user;
  Bootstrap? bootstrap;
  CashHolding? serverCash;
  int unsyncedCashPaise = 0;
  bool online = false;
  DateTime? lastOnline;
  WsClient? ws;
  SyncService? sync;
  CollectModel? collect;
  StreamSubscription<List<ConnectivityResult>>? _connSub;
  StreamSubscription<WsMessage>? _wsSub;
  Timer? _probe;

  static Future<AppState> create() async {
    final prefs = await SharedPreferences.getInstance();
    var deviceId = prefs.getString(_Keys.deviceId);
    if (deviceId == null || deviceId.isEmpty) {
      deviceId = LocalStore.newUuid();
      await prefs.setString(_Keys.deviceId, deviceId);
    }
    final store = await LocalStore.open();
    final api = ApiClient(
      baseUrl: prefs.getString(_Keys.server) ?? defaultServerUrl,
      token: prefs.getString(_Keys.token),
    );
    final s = AppState._(prefs, store, api, deviceId);
    await s._restore();
    return s;
  }

  String get serverUrl => api.baseUrl;
  String get lastUsername => prefs.getString(_Keys.lastUsername) ?? '';
  bool get loggedIn => user != null && api.token != null;
  SiteSettings get settings => bootstrap?.settings ?? SiteSettings(const {});

  Future<void> setServerUrl(String url) async {
    api.server = url.trim().isEmpty ? defaultServerUrl : url;
    await prefs.setString(_Keys.server, api.baseUrl);
    notifyListeners();
  }

  Future<void> _restore() async {
    api.onReachability = _setOnline;
    api.onUnauthorized = () {
      if (loggedIn) logout(reason: 'Session expired: please log in again');
    };
    final uj = prefs.getString(_Keys.user);
    if (api.token != null && uj != null) {
      try {
        user = UserInfo.fromJson(Map<String, dynamic>.from(jsonDecode(uj) as Map));
      } catch (_) {
        user = null;
      }
    }
    final cachedBoot = await store.getCache<Map<String, dynamic>>(CacheKeys.bootstrap);
    if (cachedBoot != null) bootstrap = Bootstrap(cachedBoot);
    final cachedCash = await store.getCache<Map<String, dynamic>>(CacheKeys.cash);
    if (cachedCash != null) serverCash = CashHolding(cachedCash);
    if (loggedIn) await _startSession();
  }

  String? logoutReason;

  // ------------------------------------------------------------------ auth
  Future<void> login(String username, String pin) async {
    final res = await api.login(username.trim(), pin.trim(), deviceId);
    api.token = res['token'] as String;
    user = UserInfo.fromJson(Map<String, dynamic>.from(res['user'] as Map));
    logoutReason = null;
    await prefs.setString(_Keys.token, api.token!);
    await prefs.setString(_Keys.user, jsonEncode(user!.toJson()));
    await prefs.setString(_Keys.lastUsername, username.trim());
    await _startSession();
    notifyListeners();
  }

  Future<void> logout({String? reason}) async {
    _stopSession();
    api.token = null;
    user = null;
    bootstrap = null;
    serverCash = null;
    logoutReason = reason;
    await prefs.remove(_Keys.token);
    await prefs.remove(_Keys.user);
    await store.clearCache();
    notifyListeners();
  }

  Future<void> _startSession() async {
    final u = user!;
    sync = SyncService(store, ApiSyncTransport(api), userId: u.id)
      ..onSynced = (o) {
        if (o.cash != null) _setServerCash(o.cash!);
        refreshUnsynced();
        collect?.refresh(silent: true);
      };
    sync!.addListener(notifyListeners);
    ws = WsClient(() => api.wsUri())..connect();
    _wsSub = ws!.messages.listen(_onWs);
    if (u.isCollector) collect = CollectModel(this);
    await refreshUnsynced();
    unawaited(refreshBootstrap());
    if (u.isCollector) unawaited(refreshCash());
    sync!.start();
    collect?.start();
    _connSub = Connectivity().onConnectivityChanged.listen((r) {
      if (r.any((c) => c != ConnectivityResult.none)) {
        ws?.kick();
        sync?.flush();
        collect?.refresh(silent: true);
      }
    });
    _probe = Timer.periodic(const Duration(seconds: 15), (_) {
      if (!online) api.health();
    });
  }

  void _stopSession() {
    _probe?.cancel();
    _connSub?.cancel();
    _wsSub?.cancel();
    ws?.dispose();
    ws = null;
    sync?.removeListener(notifyListeners);
    sync?.dispose();
    sync = null;
    collect?.dispose();
    collect = null;
  }

  void _onWs(WsMessage m) {
    if (m.topic == 'cash.updated' && user != null && m.data['user_id'] == user!.id) {
      _setServerCash(m.data);
    }
  }

  void _setOnline(bool v) {
    if (v) lastOnline = DateTime.now();
    if (online != v) {
      final cameBack = v && !online;
      online = v;
      notifyListeners();
      if (cameBack) {
        sync?.flush();
        ws?.kick();
      }
    }
  }

  // ------------------------------------------------------------------ data
  Future<void> refreshBootstrap() async {
    try {
      final b = await api.bootstrap();
      await store.putCache(CacheKeys.bootstrap, b);
      bootstrap = Bootstrap(b);
      final u = bootstrap!.user;
      user = u;
      await prefs.setString(_Keys.user, jsonEncode(u.toJson()));
      notifyListeners();
    } on NetworkException {
      // keep cached
    } on ApiException {
      // keep cached
    }
  }

  Future<void> refreshCash() async {
    if (user == null || !user!.isCollector) return;
    try {
      final c = await api.myCash();
      _setServerCash(c.raw);
    } catch (_) {}
    await refreshUnsynced();
  }

  void _setServerCash(Map<String, dynamic> raw) {
    serverCash = CashHolding(raw);
    store.putCache(CacheKeys.cash, raw);
    notifyListeners();
  }

  Future<void> refreshUnsynced() async {
    if (user == null) return;
    unsyncedCashPaise = await store.unsyncedCashPaise(userId: user!.id);
    notifyListeners();
  }

  /// Live cash-in-hand vs limit, enforced on the device even offline.
  CashPosition get cashPosition => CashPosition(
    serverHeldPaise: serverCash?.cashInHandPaise ?? 0,
    unsyncedPaise: unsyncedCashPaise,
    limitPaise: bootstrap?.settings.cashLimitPaise ?? serverCash?.limitPaise ?? 200000,
    warnRatio: bootstrap?.settings.cashWarnRatio ?? 0.8,
  );

  bool get cashAllowed => (bootstrap?.cashAllowed ?? true) && (bootstrap?.settings.cashEnabled ?? true);

  /// Queue an offline action and try to send it right away.
  Future<QueueItem> enqueue(
    String type,
    Map<String, dynamic> data, {
    String? clientUuid,
    int amountPaise = 0,
    String? label,
    DateTime? createdAt,
  }) async {
    final item = await store.enqueue(
      type: type,
      data: data,
      userId: user!.id,
      clientUuid: clientUuid,
      amountPaise: amountPaise,
      label: label,
      createdAt: createdAt,
    );
    await refreshUnsynced();
    await sync?.refreshCounts();
    unawaited(sync?.flush());
    return item;
  }

  @override
  void dispose() {
    _stopSession();
    store.close();
    super.dispose();
  }
}
