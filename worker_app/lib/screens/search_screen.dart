import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/models.dart';
import '../domain/pay_request.dart';
import '../domain/plates.dart' as plates;
import '../state/app_state.dart';
import '../util/format.dart';
import '../widgets/common.dart';
import '../widgets/plate_scanner.dart';
import 'collect_flow_screen.dart';
import 'collect_screen.dart' show targetFromItem;
import 'pass_sell_screen.dart';
import 'vehicle_screen.dart';

CollectTarget targetFromVehicle(VehicleInfo v) {
  final s = v.openSession;
  return CollectTarget(
    vehicleId: v.id,
    plate: v.plate,
    displayPlate: v.displayPlate,
    vehicleClass: s?.vehicleClass ?? v.vehicleClass,
    sessionId: (s != null && s.isOpen) ? s.id : null,
    entryAt: s?.entryAt,
    gateId: s?.entryGate,
    zoneId: s?.zoneId,
    plateImage: s?.plateImage,
    duesPaise: v.duePaise,
    creditPaise: v.creditPaise,
    passCandidate: v.passCandidate,
    phoneKnown: v.phone != null,
  );
}

/// Plate search with approximate matching (server ranks exact, then confusion +
/// Levenshtein), camera scan with on-device OCR, and an offline fallback over
/// the cached To-collect list.
class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key, this.supervisorMode = false});

  /// Supervisor "reverse / refund" entry point: results open the vehicle history.
  final bool supervisorMode;

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final _q = TextEditingController();
  Timer? _debounce;
  bool _loading = false;
  String? _error;
  List<VehicleInfo> _results = [];
  List<(CollectItem, int)> _offline = [];
  bool _searchedOffline = false;
  String _lastQuery = '';

  AppState get _app => context.read<AppState>();

  @override
  void dispose() {
    _debounce?.cancel();
    _q.dispose();
    super.dispose();
  }

  void _onChanged(String v) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), () => _search(v));
  }

  Future<void> _search(String raw) async {
    final q = plates.normalise(raw);
    if (q.length < 3) {
      setState(() {
        _results = [];
        _offline = [];
        _error = null;
      });
      return;
    }
    _lastQuery = q;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final r = await _app.api.searchVehicles(q);
      if (!mounted || _lastQuery != q) return;
      setState(() {
        _results = r;
        _offline = [];
        _searchedOffline = false;
      });
    } on NetworkException {
      if (!mounted) return;
      setState(() {
        _results = [];
        _offline = _localSearch(q);
        _searchedOffline = true;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.detail);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  /// Offline: approximate match (confusion map + edit distance) against the cached To-collect list.
  List<(CollectItem, int)> _localSearch(String q) {
    final items = _app.collect?.items ?? const <CollectItem>[];
    final tol = (_app.bootstrap?.raw['settings']?['approx_tolerance'] as num?)?.toInt() ?? 1;
    final out = <(CollectItem, int)>[];
    for (final i in items) {
      if (q.length < 6) {
        if (i.plate.contains(q)) out.add((i, 0));
      } else {
        final d = plates.fuzzyDistance(q, i.plate, tol);
        if (d <= tol) out.add((i, i.plate == q ? 0 : d));
      }
    }
    out.sort((a, b) => a.$2.compareTo(b.$2));
    return out;
  }

  Future<void> _scan() async {
    final p = await scanPlate(context, stateCodes: _app.settings.stateCodes);
    if (p == null || !mounted) return;
    _q.text = p;
    await _search(p);
  }

  @override
  Widget build(BuildContext context) {
    final body = Column(
      children: [
        Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _q,
                  autofocus: !widget.supervisorMode,
                  textCapitalization: TextCapitalization.characters,
                  style: const TextStyle(fontSize: 20, fontFamily: 'monospace', fontWeight: FontWeight.w700),
                  decoration: InputDecoration(
                    hintText: 'Plate e.g. MH43AB1234',
                    prefixIcon: const Icon(Icons.search),
                    border: const OutlineInputBorder(),
                    suffixIcon: _q.text.isEmpty
                        ? null
                        : IconButton(
                            icon: const Icon(Icons.clear),
                            onPressed: () {
                              _q.clear();
                              _search('');
                            },
                          ),
                  ),
                  onChanged: (v) {
                    setState(() {});
                    _onChanged(v);
                  },
                  onSubmitted: _search,
                ),
              ),
              const SizedBox(width: 8),
              SizedBox(
                height: 58,
                child: FilledButton.tonalIcon(
                  onPressed: _scan,
                  icon: const Icon(Icons.camera_alt),
                  label: const Text('Scan'),
                ),
              ),
            ],
          ),
        ),
        if (_loading) const LinearProgressIndicator(),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.all(8),
            child: Text(_error!, style: const TextStyle(color: dueRed)),
          ),
        if (_searchedOffline)
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 12),
            child: Text(
              'Offline: searching the saved To-collect list only.',
              style: TextStyle(color: Colors.deepOrange),
            ),
          ),
        Expanded(child: _list()),
      ],
    );
    if (widget.supervisorMode) {
      return Scaffold(
        appBar: AppBar(title: const Text('Find payment to reverse / refund')),
        body: body,
      );
    }
    return body;
  }

  Widget _list() {
    if (_searchedOffline) {
      if (_offline.isEmpty) return const EmptyState('No matching open session in the saved list.');
      return ListView.separated(
        itemCount: _offline.length,
        separatorBuilder: (_, _) => const Divider(height: 1),
        itemBuilder: (context, i) {
          final (item, d) = _offline[i];
          return ListTile(
            leading: PlateImage(item.plateImage, width: 90, height: 46),
            title: PlateText(item.displayPlate, size: 16),
            subtitle: Text(
              'In ${timeIst(item.entryAt)} · ${d == 0 ? 'exact' : 'close match'}'
              '${item.previousDuePaise > 0 ? ' · dues ${rupees(item.previousDuePaise)}' : ''}',
            ),
            trailing: const Icon(Icons.chevron_right),
            onTap: () =>
                Navigator.of(context)
                    .push(MaterialPageRoute(builder: (_) => CollectFlowScreen(target: targetFromItem(item)))),
          );
        },
      );
    }
    if (_results.isEmpty) {
      final q = plates.normalise(_q.text);
      if (q.length < 3) return const EmptyState('Type 3+ characters of the plate, or scan it.', icon: Icons.search);
      if (_loading) return const SizedBox.shrink();
      final corr = plates.correct(q, _app.settings.stateCodes.isEmpty ? null : _app.settings.stateCodes);
      return ListView(
        children: [
          const EmptyState('No vehicle found.'),
          if (corr.valid && !widget.supervisorMode)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24),
              child: OutlinedButton.icon(
                icon: const Icon(Icons.card_membership),
                label: Text('Sell a pass to ${plates.display(corr.plate)}'),
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) => PassSellScreen(
                      target: CollectTarget(
                        vehicleId: 0,
                        plate: corr.plate,
                        displayPlate: plates.display(corr.plate),
                        vehicleClass: 'BIKE',
                      ),
                    ),
                  ),
                ),
              ),
            ),
        ],
      );
    }
    return ListView.separated(
      itemCount: _results.length,
      separatorBuilder: (_, _) => const Divider(height: 1),
      itemBuilder: (context, i) => _ResultTile(v: _results[i], supervisorMode: widget.supervisorMode),
    );
  }
}

class _ResultTile extends StatelessWidget {
  const _ResultTile({required this.v, required this.supervisorMode});
  final VehicleInfo v;
  final bool supervisorMode;

  @override
  Widget build(BuildContext context) {
    final s = v.openSession;
    final canCollect = s != null && s.isOpen && !supervisorMode;
    return InkWell(
      onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => VehicleScreen(vehicleId: v.id))),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        child: Row(
          children: [
            PlateImage(v.plateImage, width: 100, height: 50),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Flexible(child: PlateText(v.displayPlate, size: 16)),
                      const SizedBox(width: 6),
                      Badge2(
                        v.exact ? 'Exact' : 'Close match (${v.distance})',
                        color: v.exact ? paidGreen : Colors.orange,
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Wrap(
                    spacing: 6,
                    runSpacing: 3,
                    children: [
                      if (s != null) Badge2('${s.status} since ${timeIst(s.entryAt)}', color: Colors.blueGrey),
                      if (v.activePass != null)
                        Badge2('Pass till ${dateIst(v.activePass!.endsAt)}', color: Colors.purple),
                      if (v.duePaise > 0) Badge2('Dues ${rupees(v.duePaise)}', color: dueRed),
                      if (v.creditPaise > 0) Badge2('Credit ${rupees(v.creditPaise)}', color: paidGreen),
                    ],
                  ),
                ],
              ),
            ),
            if (canCollect)
              FilledButton(
                onPressed: () =>
                    Navigator.of(context)
                        .push(MaterialPageRoute(builder: (_) => CollectFlowScreen(target: targetFromVehicle(v)))),
                child: const Text('Collect'),
              )
            else
              const Icon(Icons.chevron_right),
          ],
        ),
      ),
    );
  }
}
