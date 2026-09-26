import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';

import '../../api/models.dart';
import '../../api/ws_client.dart';
import '../../state/app_state.dart';
import '../../util/format.dart';
import '../../widgets/common.dart';
import '../../widgets/denomination_grid.dart';
import '../../widgets/dialogs.dart';

/// Pending worker handovers. Two-party control: the worker declared; the
/// supervisor counts, photographs the counted cash, and confirms (or rejects).
class HandoversScreen extends StatefulWidget {
  const HandoversScreen({super.key});

  @override
  State<HandoversScreen> createState() => _HandoversScreenState();
}

class _HandoversScreenState extends State<HandoversScreen> {
  List<HandoverInfo>? _rows;
  String? _error;
  String _status = 'PENDING';
  StreamSubscription<WsMessage>? _sub;

  AppState get _app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
    _sub = context.read<AppState>().ws?.messages.where((m) => m.topic.startsWith('handover.')).listen((_) => _load());
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final r = await _app.api.handovers(status: _status == 'ALL' ? null : _status);
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
    final me = context.watch<AppState>().user;
    return Scaffold(
      appBar: AppBar(title: const Text('Cash handovers')),
      body: Column(children: [
        const ConnectivityBar(),
        Padding(
          padding: const EdgeInsets.all(8),
          child: SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'PENDING', label: Text('Pending')),
              ButtonSegment(value: 'CONFIRMED', label: Text('Confirmed')),
              ButtonSegment(value: 'ALL', label: Text('All')),
            ],
            selected: {_status},
            onSelectionChanged: (s) {
              setState(() {
                _status = s.first;
                _rows = null;
              });
              _load();
            },
          ),
        ),
        if (_error != null) Text(_error!, style: const TextStyle(color: dueRed)),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _load,
            child: _rows == null
                ? ListView(children: const [Padding(padding: EdgeInsets.all(32), child: Center(child: CircularProgressIndicator()))])
                : _rows!.isEmpty
                    ? ListView(children: const [EmptyState('No handovers')])
                    : ListView(children: [
                        for (final h in _rows!)
                          Card(
                            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
                            child: ListTile(
                              title: Text('${h.fromName} — ${rupees(h.declaredPaise)}',
                                  style: const TextStyle(fontWeight: FontWeight.w700)),
                              subtitle: Text([
                                'Declared ${dateTimeIst(h.declaredAt)}',
                                'system expected ${rupees(h.expectedPaise)}',
                                if (h.countedPaise != null) 'counted ${rupees(h.countedPaise)}',
                                if ((h.variancePaise ?? 0) != 0) 'variance ${rupees(h.variancePaise)}',
                                h.status,
                              ].join(' · ')),
                              trailing: h.status == 'PENDING' && h.fromUser != me?.id ? const Icon(Icons.chevron_right) : null,
                              onTap: h.status == 'PENDING' && h.fromUser != me?.id
                                  ? () => Navigator.of(context)
                                      .push(MaterialPageRoute(builder: (_) => HandoverConfirmScreen(handover: h)))
                                      .then((_) => _load())
                                  : null,
                            ),
                          ),
                      ]),
          ),
        ),
      ]),
    );
  }
}

class HandoverConfirmScreen extends StatefulWidget {
  const HandoverConfirmScreen({super.key, required this.handover});
  final HandoverInfo handover;

  @override
  State<HandoverConfirmScreen> createState() => _HandoverConfirmScreenState();
}

class _HandoverConfirmScreenState extends State<HandoverConfirmScreen> {
  Map<int, int> _counted = {};
  File? _photo;
  final _note = TextEditingController();
  bool _busy = false;

  HandoverInfo get h => widget.handover;

  Future<void> _takePhoto() async {
    try {
      final x = await ImagePicker().pickImage(source: ImageSource.camera, maxWidth: 1600, imageQuality: 80);
      if (x != null) setState(() => _photo = File(x.path));
    } catch (e) {
      if (mounted) showSnack(context, 'Camera unavailable: $e', error: true);
    }
  }

  Future<void> _confirm() async {
    final counted = denominationTotalPaise(_counted);
    final variance = counted - h.declaredPaise;
    if (variance != 0 && _note.text.trim().isEmpty) {
      showSnack(context, 'Count differs from the declaration: add a note.', error: true);
      return;
    }
    final ok = await confirmDialog(context, 'Confirm ${rupees(counted)} from ${h.fromName}?',
        variance == 0 ? 'Matches the declaration.' : 'Variance ${rupees(variance)} will be logged against the worker\'s shift.',
        ok: 'Confirm');
    if (!ok || !mounted) return;
    setState(() => _busy = true);
    try {
      await context.read<AppState>().api.confirmHandover(h.id, _counted, _photo!, note: _note.text);
      if (mounted) {
        showSnack(context, 'Handover confirmed');
        Navigator.pop(context, true);
      }
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _reject() async {
    final note = await textPrompt(context, 'Reject handover', label: 'Reason', maxLines: 2);
    if (note == null || !mounted) return;
    try {
      await context.read<AppState>().api.rejectHandover(h.id, note);
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final counted = denominationTotalPaise(_counted);
    final variance = counted - h.declaredPaise;
    return Scaffold(
      appBar: AppBar(title: Text('Count: ${h.fromName}')),
      body: AbsorbPointer(
        absorbing: _busy,
        child: ListView(padding: const EdgeInsets.all(16), children: [
          MoneyRow('Worker declared', h.declaredPaise, bold: true),
          MoneyRow('System cash in hand at declaration', h.expectedPaise),
          const SizedBox(height: 4),
          const Text('Count the cash yourself (grey = worker\'s declared count).', style: TextStyle(color: Colors.black54)),
          const SizedBox(height: 8),
          DenominationGrid(counts: _counted, reference: h.declaredDenoms, onChanged: (m) => setState(() => _counted = m)),
          if (counted > 0 && variance != 0)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text('Variance ${rupees(variance)}', style: const TextStyle(color: dueRed, fontSize: 18, fontWeight: FontWeight.w800)),
            ),
          TextField(
            controller: _note,
            decoration: InputDecoration(labelText: variance != 0 ? 'Note (required: count differs)' : 'Note (optional)'),
          ),
          const SizedBox(height: 16),
          if (_photo != null) ClipRRect(borderRadius: BorderRadius.circular(8), child: Image.file(_photo!, height: 200, fit: BoxFit.cover)),
          OutlinedButton.icon(
            onPressed: _takePhoto,
            icon: const Icon(Icons.photo_camera),
            label: Text(_photo == null ? 'Take photo of the counted cash (required)' : 'Retake photo'),
          ),
          const SizedBox(height: 16),
          BigButton(
            label: 'Confirm ${rupees(counted)}',
            icon: Icons.verified,
            color: paidGreen,
            onPressed: counted > 0 && _photo != null ? _confirm : null,
          ),
          const SizedBox(height: 8),
          TextButton(onPressed: _reject, child: const Text('Reject handover', style: TextStyle(color: dueRed))),
        ]),
      ),
    );
  }
}
