import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../offline/local_store.dart';
import '../state/app_state.dart';
import '../util/format.dart';
import '../widgets/common.dart';
import '../widgets/denomination_grid.dart';
import '../widgets/dialogs.dart';

/// Worker declares a cash handover: count notes/coins; the amount is the
/// auto-summed total. The supervisor then counts and confirms with a photo.
class HandoverScreen extends StatefulWidget {
  const HandoverScreen({super.key});

  @override
  State<HandoverScreen> createState() => _HandoverScreenState();
}

class _HandoverScreenState extends State<HandoverScreen> {
  Map<int, int> _counts = {};
  bool _busy = false;
  final String _uuid = LocalStore.newUuid();

  Future<void> _submit() async {
    final app = context.read<AppState>();
    final total = denominationTotalPaise(_counts);
    final held = app.cashPosition.heldPaise;
    final ok = await confirmDialog(
      context,
      'Hand over ${rupees(total)}?',
      total == held
          ? 'Matches your cash in hand.'
          : 'Your cash in hand is ${rupees(held)}; you are declaring ${rupees(total)}. Any difference is logged against your shift.',
      ok: 'Declare ${rupees(total)}',
    );
    if (!ok || !mounted) return;
    setState(() => _busy = true);
    try {
      await app.api.declareHandover(total, _counts, _uuid);
      if (mounted) showSnack(context, 'Handover declared. Give the cash to the supervisor to count.');
    } on NetworkException {
      await app.enqueue(
        SyncType.handover,
        {
          'amount_paise': total,
          'denominations': {
            for (final e in _counts.entries)
              if (e.value > 0) '${e.key}': e.value,
          },
        },
        clientUuid: _uuid,
        label: 'Handover ${rupees(total)}',
      );
      if (mounted) showSnack(context, 'Offline: handover saved and will be sent when the server is back.');
    } catch (e) {
      if (mounted) showError(context, e);
      setState(() => _busy = false);
      return;
    }
    if (mounted) Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final total = denominationTotalPaise(_counts);
    return Scaffold(
      appBar: AppBar(title: const Text('Cash handover')),
      body: Column(
        children: [
          const ConnectivityBar(),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Cash in hand: ${rupees(app.cashPosition.heldPaise)}',
                  style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                ),
                const Text(
                  'Count your notes and coins. The total is added up for you.',
                  style: TextStyle(color: Colors.black54),
                ),
                const SizedBox(height: 12),
                DenominationGrid(counts: _counts, onChanged: (m) => setState(() => _counts = m)),
                const SizedBox(height: 20),
                BigButton(
                  label: _busy ? 'Sending…' : 'Declare ${rupees(total)}',
                  icon: Icons.handshake,
                  onPressed: total <= 0 || _busy ? null : _submit,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
