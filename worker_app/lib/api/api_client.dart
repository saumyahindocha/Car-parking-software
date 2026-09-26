import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'models.dart';

/// The server answered with an error status; [detail] is the backend's message
/// (FastAPI `{"detail": ...}`), suitable to show to the worker.
class ApiException implements Exception {
  ApiException(this.statusCode, this.detail);
  final int statusCode;
  final String detail;

  bool get isAuth => statusCode == 401;
  bool get isCashLimit => statusCode == 423;
  bool get isConflict => statusCode == 409;

  @override
  String toString() => detail;
}

/// The server could not be reached (LAN down, server off, timeout). Callers
/// fall back to the offline path.
class NetworkException implements Exception {
  NetworkException(this.message);
  final String message;
  @override
  String toString() => 'Server unreachable: $message';
}

/// Thin JSON client for the edge server. Every call reports reachability via
/// [onReachability] so the UI can show online/offline without extra pings.
class ApiClient {
  ApiClient({required String baseUrl, this.token, http.Client? client, this.timeout = const Duration(seconds: 8)})
    : baseUrl = _clean(baseUrl),
      _http = client ?? http.Client();

  String baseUrl;
  String? token;
  final http.Client _http;
  final Duration timeout;
  void Function(bool reachable)? onReachability;
  void Function()? onUnauthorized;

  static String _clean(String u) {
    var s = u.trim();
    if (s.isEmpty) return s;
    if (!s.startsWith('http://') && !s.startsWith('https://')) s = 'http://$s';
    while (s.endsWith('/')) {
      s = s.substring(0, s.length - 1);
    }
    return s;
  }

  set server(String url) => baseUrl = _clean(url);

  Uri uri(String path, [Map<String, dynamic>? query]) {
    final q = <String, String>{};
    query?.forEach((k, v) {
      if (v != null) q[k] = v.toString();
    });
    final u = Uri.parse('$baseUrl$path');
    return q.isEmpty ? u : u.replace(queryParameters: {...u.queryParameters, ...q});
  }

  /// Absolute URL for a backend image path (`/api/images/...`) with the token
  /// as a query parameter (Image.network cannot always send headers reliably
  /// through caches).
  String? imageUrl(String? path) {
    if (path == null || path.isEmpty) return null;
    final base = path.startsWith('http') ? path : '$baseUrl$path';
    final sep = base.contains('?') ? '&' : '?';
    return token == null ? base : '$base${sep}token=${Uri.encodeQueryComponent(token!)}';
  }

  /// `ws://host:port/ws?token=...`
  Uri wsUri({List<String> topics = const []}) {
    final u = Uri.parse(baseUrl);
    return u.replace(
      scheme: u.scheme == 'https' ? 'wss' : 'ws',
      path: '/ws',
      queryParameters: {'token': token ?? '', if (topics.isNotEmpty) 'topics': topics.join(',')},
    );
  }

  Map<String, String> get _headers => {
    'Accept': 'application/json',
    if (token != null) 'Authorization': 'Bearer $token',
  };

  Future<dynamic> get(String path, {Map<String, dynamic>? query, Duration? timeout}) =>
      _send(() => _http.get(uri(path, query), headers: _headers), timeout);

  Future<dynamic> post(String path, [Object? body, Duration? timeout]) => _send(
    () =>
        _http.post(uri(path), headers: {..._headers, 'Content-Type': 'application/json'}, body: jsonEncode(body ?? {})),
    timeout,
  );

  /// Multipart form POST (handover confirm with photo, bank deposit with slip).
  Future<dynamic> multipart(String path, {required Map<String, String> fields, required Map<String, File> files}) {
    return _send(() async {
      final req = http.MultipartRequest('POST', uri(path));
      req.headers.addAll(_headers);
      req.fields.addAll(fields);
      for (final e in files.entries) {
        req.files.add(await http.MultipartFile.fromPath(e.key, e.value.path, filename: e.value.uri.pathSegments.last));
      }
      return http.Response.fromStream(await _http.send(req));
    }, const Duration(seconds: 60));
  }

  Future<dynamic> _send(Future<http.Response> Function() fn, Duration? t) async {
    http.Response res;
    try {
      res = await fn().timeout(t ?? timeout);
    } on TimeoutException {
      onReachability?.call(false);
      throw NetworkException('timed out');
    } on SocketException catch (e) {
      onReachability?.call(false);
      throw NetworkException(e.message);
    } on http.ClientException catch (e) {
      onReachability?.call(false);
      throw NetworkException(e.message);
    } on HandshakeException catch (e) {
      onReachability?.call(false);
      throw NetworkException(e.message);
    }
    onReachability?.call(true);
    final text = utf8.decode(res.bodyBytes);
    dynamic body;
    if (text.isNotEmpty) {
      try {
        body = jsonDecode(text);
      } catch (_) {
        body = text;
      }
    }
    if (res.statusCode >= 200 && res.statusCode < 300) return body;
    if (res.statusCode == 401) onUnauthorized?.call();
    throw ApiException(res.statusCode, _detail(body, res.statusCode));
  }

  static String _detail(dynamic body, int status) {
    if (body is Map && body['detail'] != null) {
      final d = body['detail'];
      if (d is String) return d;
      if (d is List) {
        return d.map((e) => e is Map ? '${(e['loc'] as List?)?.join('.') ?? ''}: ${e['msg']}' : '$e').join('; ');
      }
      return d.toString();
    }
    if (body is String && body.isNotEmpty && body.length < 200) return body;
    return 'Server error ($status)';
  }

  // ------------------------------------------------------------------ typed calls
  Future<Map<String, dynamic>> login(String username, String pin, String deviceId) async => Map<String, dynamic>.from(
    await post('/api/auth/login', {'username': username, 'pin': pin, 'device_id': deviceId}),
  );

  Future<bool> health() async {
    try {
      await get('/api/health', timeout: const Duration(seconds: 4));
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<Map<String, dynamic>> bootstrap() async => Map<String, dynamic>.from(await get('/api/bootstrap'));

  Future<List<dynamic>> collectList({int? zoneId}) async =>
      List<dynamic>.from(await get('/api/collect/list', query: {'zone_id': zoneId}));

  Future<List<VehicleInfo>> searchVehicles(String q) async =>
      (await get('/api/vehicles/search', query: {'q': q}) as List)
          .map((e) => VehicleInfo(Map<String, dynamic>.from(e)))
          .toList();

  Future<VehicleInfo> vehicle(int id) async => VehicleInfo(Map<String, dynamic>.from(await get('/api/vehicles/$id')));

  Future<SessionInfo> session(int id) async => SessionInfo(Map<String, dynamic>.from(await get('/api/sessions/$id')));

  Future<SessionInfo> correctPlate(int sessionId, String plate) async =>
      SessionInfo(Map<String, dynamic>.from(await post('/api/sessions/$sessionId/correct-plate', {'plate': plate})));

  Future<QuoteInfo> quote(int sessionId, int durationMinutes) async => QuoteInfo.fromJson(
    Map<String, dynamic>.from(
      await get('/api/sessions/$sessionId/quote', query: {'duration_minutes': durationMinutes}),
    ),
  );

  Future<PaymentInfo> payUpi(Map<String, dynamic> body) async =>
      PaymentInfo(Map<String, dynamic>.from(await post('/api/payments/upi', body)));

  Future<PaymentInfo> payCash(Map<String, dynamic> body) async =>
      PaymentInfo(Map<String, dynamic>.from(await post('/api/payments/cash', body)));

  Future<PaymentInfo> payment(int id) async => PaymentInfo(Map<String, dynamic>.from(await get('/api/payments/$id')));

  Future<PaymentInfo> claimOffline(int id) async =>
      PaymentInfo(Map<String, dynamic>.from(await post('/api/payments/$id/claim-offline')));

  Future<Map<String, dynamic>> receiptShown(String code) async =>
      Map<String, dynamic>.from(await post('/api/receipts/$code/shown'));

  Future<Map<String, dynamic>> receiptSend(String code, String phone) async =>
      Map<String, dynamic>.from(await post('/api/receipts/$code/send', {'phone': phone}));

  Future<void> setContact(int vehicleId, String phone) async =>
      post('/api/vehicles/$vehicleId/contact', {'phone': phone});

  Future<PaymentInfo> sellPass(Map<String, dynamic> body) async =>
      PaymentInfo(Map<String, dynamic>.from(await post('/api/passes/sell', body)));

  Future<CashHolding> myCash() async => CashHolding(Map<String, dynamic>.from(await get('/api/me/cash')));

  Future<ShiftInfo?> currentShift() async {
    final r = await get('/api/shifts/current');
    return r is Map ? ShiftInfo(Map<String, dynamic>.from(r)) : null;
  }

  Future<ShiftInfo> openShift({int? zoneId}) async =>
      ShiftInfo(Map<String, dynamic>.from(await post('/api/shifts/open', {'zone_id': zoneId})));

  Future<Map<String, dynamic>> closeShift({String? note}) async =>
      Map<String, dynamic>.from(await post('/api/shifts/close', {'note': note}));

  Future<HandoverInfo> declareHandover(int amountPaise, Map<int, int> denoms, String clientUuid) async => HandoverInfo(
    Map<String, dynamic>.from(
      await post('/api/cash/handovers', {
        'amount_paise': amountPaise,
        'denominations': {
          for (final e in denoms.entries)
            if (e.value > 0) '${e.key}': e.value,
        },
        'client_uuid': clientUuid,
      }),
    ),
  );

  Future<List<HandoverInfo>> myHandovers() async =>
      (await get('/api/cash/handovers/mine') as List).map((e) => HandoverInfo(Map<String, dynamic>.from(e))).toList();

  Future<List<AlertInfo>> alerts({String? kind, bool openOnly = true, int hours = 24}) async =>
      (await get('/api/alerts', query: {'kind': kind, 'open_only': openOnly, 'hours': hours}) as List)
          .map((e) => AlertInfo(Map<String, dynamic>.from(e)))
          .toList();

  Future<AlertInfo> ackAlert(int id, {String? note}) async =>
      AlertInfo(Map<String, dynamic>.from(await post('/api/alerts/$id/ack', {'note': note})));

  Future<DisputeInfo> raiseDispute(Map<String, dynamic> body) async =>
      DisputeInfo(Map<String, dynamic>.from(await post('/api/disputes', body)));

  Future<Map<String, dynamic>> sync(List<Map<String, dynamic>> items) async =>
      Map<String, dynamic>.from(await post('/api/sync', {'items': items}, const Duration(seconds: 30)));

  // ------------------------------------------------------------------ supervisor
  Future<List<CashHolding>> holdings() async =>
      (await get('/api/cash/holdings') as List).map((e) => CashHolding(Map<String, dynamic>.from(e))).toList();

  Future<List<HandoverInfo>> handovers({String? status = 'PENDING'}) async =>
      (await get('/api/cash/handovers', query: {'status': status ?? ''}) as List)
          .map((e) => HandoverInfo(Map<String, dynamic>.from(e)))
          .toList();

  Future<HandoverInfo> confirmHandover(int id, Map<int, int> counted, File photo, {String? note}) async => HandoverInfo(
    Map<String, dynamic>.from(
      await multipart(
        '/api/cash/handovers/$id/confirm',
        fields: {
          'counted_denominations': jsonEncode({
            for (final e in counted.entries)
              if (e.value > 0) '${e.key}': e.value,
          }),
          if (note != null && note.trim().isNotEmpty) 'note': note.trim(),
        },
        files: {'photo': photo},
      ),
    ),
  );

  Future<HandoverInfo> rejectHandover(int id, String note) async =>
      HandoverInfo(Map<String, dynamic>.from(await post('/api/cash/handovers/$id/reject', {'note': note})));

  Future<Map<String, dynamic>> recordDeposit({
    required String businessDate,
    required int amountPaise,
    required String slipRef,
    required File slipPhoto,
    String? note,
  }) async => Map<String, dynamic>.from(
    await multipart(
      '/api/cash/deposits',
      fields: {
        'business_date': businessDate,
        'amount_paise': '$amountPaise',
        'slip_ref': slipRef,
        if (note != null && note.trim().isNotEmpty) 'note': note.trim(),
      },
      files: {'slip_photo': slipPhoto},
    ),
  );

  Future<List<dynamic>> deposits() async => List<dynamic>.from(await get('/api/cash/deposits'));

  Future<List<DisputeInfo>> disputes({String? status = 'OPEN'}) async =>
      (await get('/api/disputes', query: {'status': status}) as List)
          .map((e) => DisputeInfo(Map<String, dynamic>.from(e)))
          .toList();

  Future<DisputeInfo> resolveDispute(int id, String outcome, String note, {int? adjustPaise}) async => DisputeInfo(
    Map<String, dynamic>.from(
      await post('/api/disputes/$id/resolve', {'outcome': outcome, 'note': note, 'adjust_paise': adjustPaise}),
    ),
  );

  Future<List<ZoneInfo>> zones() async =>
      (await get('/api/zones') as List).map((e) => ZoneInfo.fromJson(Map<String, dynamic>.from(e))).toList();

  Future<List<ZoneAssignmentInfo>> zoneAssignments({String? date}) async =>
      (await get('/api/zones/assignments', query: {'date': date}) as List)
          .map((e) => ZoneAssignmentInfo(Map<String, dynamic>.from(e)))
          .toList();

  Future<void> assignZone({
    required int zoneId,
    required int userId,
    required DateTime startsAt,
    required DateTime endsAt,
    String shiftLabel = '',
  }) => post('/api/zones/assignments', {
    'zone_id': zoneId,
    'user_id': userId,
    'starts_at': startsAt.toUtc().toIso8601String(),
    'ends_at': endsAt.toUtc().toIso8601String(),
    'shift_label': shiftLabel,
  });

  Future<List<UserInfo>> users() async =>
      (await get('/api/users') as List).map((e) => UserInfo.fromJson(Map<String, dynamic>.from(e))).toList();

  Future<PaymentInfo> reverseCash(int paymentId, String reason) async =>
      PaymentInfo(Map<String, dynamic>.from(await post('/api/payments/$paymentId/reverse', {'reason': reason})));

  Future<PaymentInfo> refundUpi(int paymentId, String reason, {int? amountPaise}) async => PaymentInfo(
    Map<String, dynamic>.from(
      await post('/api/payments/$paymentId/refund', {'reason': reason, 'amount_paise': amountPaise}),
    ),
  );
}
