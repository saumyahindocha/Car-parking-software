import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../api/models.dart';
import '../../api/ws_client.dart';
import '../../state/app_state.dart';
import '../../util/format.dart';
import '../../widgets/common.dart';

/// Live cash-in-hand for every worker with an open shift.
class HoldingsScreen extends StatefulWidget {
  const HoldingsScreen({super.key});

  @override
  State<HoldingsScreen> createState() => _HoldingsScreenState();
}

class _HoldingsScreenState extends State<HoldingsScreen> {
  List<CashHolding>? _rows;
  String? _error;
  StreamSubscription<WsMessage>? _sub;
  Timer? _poll;

  AppState get _app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
    _sub = context.read<AppState>().ws?.topic('cash.updated').listen((m) {
      final h = CashHolding(m.data);
      setState(() {
        final rows = [...?_rows];
        final i = rows.indexWhere((r) => r.userId == h.userId);
        if (i >= 0) {
          rows[i] = h;
        } else {
          rows.add(h);
        }
        _rows = rows;
      });
    });
    _poll = Timer.periodic(const Duration(seconds: 30), (_) => _load());
  }

  @override
  void dispose() {
    _sub?.cancel();
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final r = await _app.api.holdings();
      if (mounted) {
        setState(() {
          _rows = r;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = errorText(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = [...?_rows]..sort((a, b) => b.cashInHandPaise.compareTo(a.cashInHandPaise));
    final boot = context.watch<AppState>().bootstrap;
    return Scaffold(
      appBar: AppBar(title: const Text('Cash in hand — all workers')),
      body: Column(
        children: [
          const ConnectivityBar(),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(8),
              child: Text(_error!, style: const TextStyle(color: dueRed)),
            ),
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
                  : rows.isEmpty
                  ? ListView(children: const [EmptyState('No open shifts')])
                  : ListView.builder(
                      itemCount: rows.length,
                      itemBuilder: (c, i) {
                        final h = rows[i];
                        final frac = h.limitPaise <= 0 ? 1.0 : (h.cashInHandPaise / h.limitPaise).clamp(0.0, 1.0);
                        final color = h.blocked ? dueRed : (h.warn ? Colors.amber.shade800 : paidGreen);
                        return Card(
                          margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
                          child: Padding(
                            padding: const EdgeInsets.all(12),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  children: [
                                    Expanded(
                                      child: Text(
                                        h.name ?? 'User ${h.userId}',
                                        style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
                                      ),
                                    ),
                                    Text(
                                      rupees(h.cashInHandPaise),
                                      style: TextStyle(fontSize: 20, fontWeight: FontWeight.w900, color: color),
                                    ),
                                  ],
                                ),
                                const SizedBox(height: 6),
                                LinearProgressIndicator(value: frac.toDouble(), color: color, minHeight: 8),
                                const SizedBox(height: 4),
                                Text(
                                  'Limit ${rupees(h.limitPaise)}'
                                  '${h.zoneId != null ? ' · ${boot?.zoneName(h.zoneId) ?? 'zone ${h.zoneId}'}' : ''}'
                                  '${h.blocked ? ' · BLOCKED' : (h.warn ? ' · over 80%' : '')}',
                                ),
                              ],
                            ),
                          ),
                        );
                      },
                    ),
            ),
          ),
        ],
      ),
    );
  }
}
