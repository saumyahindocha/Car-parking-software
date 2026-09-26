import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';

import '../../state/app_state.dart';
import '../../util/format.dart';
import '../../widgets/common.dart';
import '../../widgets/dialogs.dart';

/// Record the day's bank deposit with the slip reference and a photo of the slip.
class DepositScreen extends StatefulWidget {
  const DepositScreen({super.key});

  @override
  State<DepositScreen> createState() => _DepositScreenState();
}

class _DepositScreenState extends State<DepositScreen> {
  late DateTime _date; // IST business date
  final _amount = TextEditingController();
  final _slip = TextEditingController();
  final _note = TextEditingController();
  File? _photo;
  bool _busy = false;
  List<dynamic>? _recent;

  AppState get _app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    final t = toIst(DateTime.now());
    _date = DateTime(t.year, t.month, t.day);
    _loadRecent();
  }

  Future<void> _loadRecent() async {
    try {
      final r = await _app.api.deposits();
      if (mounted) setState(() => _recent = r);
    } catch (_) {}
  }

  String get _dateStr =>
      '${_date.year.toString().padLeft(4, '0')}-${_date.month.toString().padLeft(2, '0')}-${_date.day.toString().padLeft(2, '0')}';

  Future<void> _photoSlip() async {
    try {
      final x = await ImagePicker().pickImage(source: ImageSource.camera, maxWidth: 1800, imageQuality: 85);
      if (x != null) setState(() => _photo = File(x.path));
    } catch (e) {
      if (mounted) showSnack(context, 'Camera unavailable: $e', error: true);
    }
  }

  Future<void> _save() async {
    final paise = (int.tryParse(_amount.text.trim()) ?? 0) * 100;
    final ok = await confirmDialog(context, 'Record deposit ${rupees(paise)}?', 'Business date $_dateStr, slip ${_slip.text.trim()}.',
        ok: 'Record');
    if (!ok || !mounted) return;
    setState(() => _busy = true);
    try {
      await _app.api.recordDeposit(
          businessDate: _dateStr, amountPaise: paise, slipRef: _slip.text.trim(), slipPhoto: _photo!, note: _note.text);
      if (!mounted) return;
      showSnack(context, 'Deposit recorded');
      setState(() {
        _amount.clear();
        _slip.clear();
        _note.clear();
        _photo = null;
      });
      await _loadRecent();
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final amountOk = (int.tryParse(_amount.text.trim()) ?? 0) > 0;
    final ready = amountOk && _slip.text.trim().isNotEmpty && _photo != null && !_busy;
    return Scaffold(
      appBar: AppBar(title: const Text('Bank deposit')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        const ConnectivityBar(),
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.event),
          title: Text('Business date $_dateStr'),
          trailing: TextButton(
            onPressed: () async {
              final d = await showDatePicker(
                  context: context, initialDate: _date, firstDate: DateTime(2024), lastDate: DateTime.now().add(const Duration(days: 1)));
              if (d != null) setState(() => _date = d);
            },
            child: const Text('Change'),
          ),
        ),
        TextField(
          controller: _amount,
          keyboardType: TextInputType.number,
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
          decoration: const InputDecoration(labelText: 'Amount deposited (₹)', prefixText: '₹ '),
          onChanged: (_) => setState(() {}),
        ),
        TextField(
          controller: _slip,
          decoration: const InputDecoration(labelText: 'Deposit slip reference'),
          onChanged: (_) => setState(() {}),
        ),
        TextField(controller: _note, decoration: const InputDecoration(labelText: 'Note (optional)')),
        const SizedBox(height: 12),
        if (_photo != null) ClipRRect(borderRadius: BorderRadius.circular(8), child: Image.file(_photo!, height: 200, fit: BoxFit.cover)),
        OutlinedButton.icon(
          onPressed: _photoSlip,
          icon: const Icon(Icons.photo_camera),
          label: Text(_photo == null ? 'Photo of the deposit slip (required)' : 'Retake photo'),
        ),
        const SizedBox(height: 16),
        BigButton(label: 'Record deposit', icon: Icons.account_balance, onPressed: ready ? _save : null),
        const SectionTitle('Recent deposits'),
        for (final d in _recent ?? const [])
          if (d is Map)
            ListTile(
              dense: true,
              title: Text('${d['business_date']} · ${rupees((d['amount_paise'] as num?)?.toInt())}'),
              subtitle: Text('Slip ${d['slip_ref']} · bank ${d['bank_status'] ?? '—'}'),
            ),
      ]),
    );
  }
}
