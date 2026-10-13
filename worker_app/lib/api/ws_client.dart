import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

/// One message from the backend hub: `{"topic": ..., "data": {...}, "ts": ...}`.
class WsMessage {
  WsMessage(this.topic, this.data);
  final String topic;
  final Map<String, dynamic> data;
}

/// Auto-reconnecting WebSocket to `/ws?token=` (topics: session.opened,
/// session.closed, payment.updated, cash.updated, alert, exit, handover.*, ...).
class WsClient extends ChangeNotifier {
  WsClient(this._uriBuilder);

  final Uri Function() _uriBuilder;
  final StreamController<WsMessage> _out = StreamController<WsMessage>.broadcast();
  WebSocketChannel? _ch;
  StreamSubscription<dynamic>? _sub;
  Timer? _ping;
  Timer? _retry;
  int _attempt = 0;
  bool _closed = false;
  bool connected = false;

  Stream<WsMessage> get messages => _out.stream;

  Stream<WsMessage> topic(String t) => messages.where((m) => m.topic == t);

  void connect() {
    _closed = false;
    _open();
  }

  Future<void> _open() async {
    if (_closed) return;
    _teardown();
    try {
      final ch = WebSocketChannel.connect(_uriBuilder());
      _ch = ch;
      await ch.ready.timeout(const Duration(seconds: 8));
      _attempt = 0;
      _setConnected(true);
      _sub = ch.stream.listen(_onData, onDone: _onClosed, onError: (_) => _onClosed(), cancelOnError: true);
      _ping = Timer.periodic(const Duration(seconds: 20), (_) {
        try {
          _ch?.sink.add('ping');
        } catch (_) {}
      });
    } catch (_) {
      _onClosed();
    }
  }

  void _onData(dynamic raw) {
    try {
      final j = jsonDecode(raw as String);
      if (j is! Map) return;
      final topic = j['topic']?.toString() ?? '';
      if (topic == 'pong' || topic.isEmpty) return;
      final data = j['data'] is Map ? Map<String, dynamic>.from(j['data'] as Map) : <String, dynamic>{};
      _out.add(WsMessage(topic, data));
    } catch (_) {
      // ignore malformed frames
    }
  }

  void _onClosed() {
    _setConnected(false);
    _teardown();
    if (_closed) return;
    _attempt++;
    final secs = min(30, 1 << min(_attempt, 5));
    _retry?.cancel();
    _retry = Timer(Duration(seconds: secs), _open);
  }

  void _setConnected(bool v) {
    if (connected != v) {
      connected = v;
      notifyListeners();
    }
  }

  void _teardown() {
    _ping?.cancel();
    _ping = null;
    _sub?.cancel();
    _sub = null;
    try {
      _ch?.sink.close();
    } catch (_) {}
    _ch = null;
  }

  /// Reconnect immediately (e.g. connectivity came back).
  void kick() {
    if (!connected && !_closed) {
      _retry?.cancel();
      _open();
    }
  }

  void close() {
    _closed = true;
    _retry?.cancel();
    _teardown();
    _setConnected(false);
  }

  @override
  void dispose() {
    close();
    _out.close();
    super.dispose();
  }
}
