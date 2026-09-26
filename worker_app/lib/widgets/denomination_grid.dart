import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../util/format.dart';

/// Indian currency denominations accepted by the backend (`cash.DENOMINATIONS`).
const List<int> denominations = [500, 200, 100, 50, 20, 10, 5, 2, 1];

int denominationTotalPaise(Map<int, int> counts) =>
    counts.entries.fold(0, (s, e) => s + e.key * 100 * (e.value < 0 ? 0 : e.value));

/// Note/coin count grid: the amount is always the auto-summed total, never typed.
class DenominationGrid extends StatefulWidget {
  const DenominationGrid({super.key, required this.counts, required this.onChanged, this.reference});
  final Map<int, int> counts;
  final ValueChanged<Map<int, int>> onChanged;

  /// Optional counts to show alongside (e.g. the worker's declaration).
  final Map<int, int>? reference;

  @override
  State<DenominationGrid> createState() => _DenominationGridState();
}

class _DenominationGridState extends State<DenominationGrid> {
  final Map<int, TextEditingController> _ctl = {};

  @override
  void initState() {
    super.initState();
    for (final d in denominations) {
      final n = widget.counts[d] ?? 0;
      _ctl[d] = TextEditingController(text: n == 0 ? '' : '$n');
    }
  }

  @override
  void dispose() {
    for (final c in _ctl.values) {
      c.dispose();
    }
    super.dispose();
  }

  void _set(int d, int n) {
    final m = Map<int, int>.from(widget.counts);
    if (n <= 0) {
      m.remove(d);
    } else {
      m[d] = n;
    }
    widget.onChanged(m);
  }

  void _bump(int d, int delta) {
    final n = ((widget.counts[d] ?? 0) + delta).clamp(0, 9999);
    _ctl[d]!.text = n == 0 ? '' : '$n';
    _set(d, n);
  }

  @override
  Widget build(BuildContext context) {
    final total = denominationTotalPaise(widget.counts);
    return Column(children: [
      for (final d in denominations)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 3),
          child: Row(children: [
            SizedBox(
              width: 64,
              child: Text('₹$d', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
            ),
            if (widget.reference != null)
              SizedBox(
                width: 52,
                child: Text('×${widget.reference![d] ?? 0}', style: const TextStyle(color: Colors.black45)),
              ),
            IconButton(onPressed: () => _bump(d, -1), icon: const Icon(Icons.remove_circle_outline)),
            SizedBox(
              width: 64,
              child: TextField(
                controller: _ctl[d],
                textAlign: TextAlign.center,
                keyboardType: TextInputType.number,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(4)],
                decoration: const InputDecoration(hintText: '0', isDense: true, border: OutlineInputBorder()),
                onChanged: (v) => _set(d, int.tryParse(v) ?? 0),
              ),
            ),
            IconButton(onPressed: () => _bump(d, 1), icon: const Icon(Icons.add_circle_outline)),
            Expanded(
              child: Text(rupees(d * 100 * (widget.counts[d] ?? 0)),
                  textAlign: TextAlign.right, style: const TextStyle(fontSize: 15)),
            ),
          ]),
        ),
      const Divider(),
      Row(children: [
        const Expanded(child: Text('Total', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800))),
        Text(rupees(total), style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w800)),
      ]),
    ]);
  }
}
