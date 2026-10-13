import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../util/format.dart';

Future<bool> confirmDialog(
  BuildContext context,
  String title,
  String body, {
  String ok = 'Yes',
  String cancel = 'Cancel',
  Color? okColor,
}) async {
  final r = await showDialog<bool>(
    context: context,
    builder: (c) => AlertDialog(
      title: Text(title),
      content: Text(body, style: const TextStyle(fontSize: 16)),
      actions: [
        TextButton(onPressed: () => Navigator.pop(c, false), child: Text(cancel)),
        FilledButton(
          style: FilledButton.styleFrom(backgroundColor: okColor),
          onPressed: () => Navigator.pop(c, true),
          child: Text(ok),
        ),
      ],
    ),
  );
  return r ?? false;
}

/// Free-text prompt. Returns null on cancel.
Future<String?> textPrompt(
  BuildContext context,
  String title, {
  String label = '',
  String initial = '',
  bool required = true,
  int maxLines = 1,
  TextInputType? keyboard,
}) async {
  final ctl = TextEditingController(text: initial);
  final r = await showDialog<String>(
    context: context,
    builder: (c) => StatefulBuilder(
      builder: (c, set) => AlertDialog(
        title: Text(title),
        content: TextField(
          controller: ctl,
          autofocus: true,
          maxLines: maxLines,
          keyboardType: keyboard,
          decoration: InputDecoration(labelText: label),
          onChanged: (_) => set(() {}),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
          FilledButton(
            onPressed: required && ctl.text.trim().isEmpty ? null : () => Navigator.pop(c, ctl.text.trim()),
            child: const Text('OK'),
          ),
        ],
      ),
    ),
  );
  return r;
}

/// Result of the mobile-number prompt.
class PhoneAnswer {
  const PhoneAnswer.number(this.phone) : declined = false, useOnFile = false;
  const PhoneAnswer.declined() : phone = null, declined = true, useOnFile = false;
  const PhoneAnswer.onFile() : phone = null, declined = false, useOnFile = true;
  final String? phone;
  final bool declined;

  /// The server already has this vehicle's number; the receipt goes there.
  final bool useOnFile;
}

/// Asked for EVERY cash payment (spec 5A): capture the customer's mobile so the
/// receipt goes by SMS/WhatsApp. Returns null if the worker backed out.
Future<PhoneAnswer?> askPhone(BuildContext context, {bool phoneOnFile = false, String purpose = 'receipt'}) {
  final ctl = TextEditingController();
  return showModalBottomSheet<PhoneAnswer>(
    context: context,
    isScrollControlled: true,
    isDismissible: false,
    builder: (c) => StatefulBuilder(
      builder: (c, set) {
        final digits = normalisePhone(ctl.text);
        return Padding(
          padding: EdgeInsets.fromLTRB(20, 20, 20, MediaQuery.of(c).viewInsets.bottom + 20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'Customer mobile number for the $purpose?',
                style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 4),
              const Text(
                'Ask every time. The receipt is sent by SMS / WhatsApp.',
                style: TextStyle(color: Colors.black54),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: ctl,
                autofocus: true,
                keyboardType: TextInputType.phone,
                inputFormatters: [
                  FilteringTextInputFormatter.allow(RegExp(r'[0-9+ ]')),
                  LengthLimitingTextInputFormatter(14),
                ],
                style: const TextStyle(fontSize: 24, letterSpacing: 2),
                decoration: const InputDecoration(
                  prefixText: '+91 ',
                  hintText: '98xxxxxxxx',
                  border: OutlineInputBorder(),
                ),
                onChanged: (_) => set(() {}),
              ),
              const SizedBox(height: 12),
              SizedBox(
                height: 56,
                child: FilledButton.icon(
                  icon: const Icon(Icons.sms),
                  onPressed: digits == null ? null : () => Navigator.pop(c, PhoneAnswer.number(digits)),
                  label: const Text('Send receipt to this number', style: TextStyle(fontSize: 17)),
                ),
              ),
              if (phoneOnFile) ...[
                const SizedBox(height: 8),
                SizedBox(
                  height: 52,
                  child: OutlinedButton.icon(
                    icon: const Icon(Icons.contact_phone),
                    onPressed: () => Navigator.pop(c, const PhoneAnswer.onFile()),
                    label: const Text('Use the number already on file'),
                  ),
                ),
              ],
              const SizedBox(height: 8),
              SizedBox(
                height: 52,
                child: OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(foregroundColor: Colors.deepOrange),
                  icon: const Icon(Icons.qr_code_2),
                  onPressed: () => Navigator.pop(c, const PhoneAnswer.declined()),
                  label: const Text('Customer declined — show receipt QR'),
                ),
              ),
              TextButton(onPressed: () => Navigator.pop(c), child: const Text('Back')),
            ],
          ),
        );
      },
    ),
  );
}

/// Manual amount override: needs a reason and a supervisor PIN typed on this phone.
class OverrideRequest {
  const OverrideRequest(this.amountPaise, this.reason, this.supervisorPin);
  final int amountPaise;
  final String reason;
  final String supervisorPin;
}

Future<OverrideRequest?> overrideDialog(BuildContext context, int systemAmountPaise) {
  final amt = TextEditingController();
  final reason = TextEditingController();
  final pin = TextEditingController();
  return showDialog<OverrideRequest>(
    context: context,
    builder: (c) => StatefulBuilder(
      builder: (c, set) {
        final rupeesVal = int.tryParse(amt.text.trim());
        final ok = rupeesVal != null && rupeesVal >= 0 && reason.text.trim().length >= 3 && pin.text.trim().length >= 4;
        return AlertDialog(
          title: const Text('Different amount'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'System amount: ${rupees(systemAmountPaise)}. A different amount needs a reason and '
                  'a supervisor typing their PIN on this phone. It appears in the daily override report.',
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: amt,
                  keyboardType: TextInputType.number,
                  inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                  decoration: const InputDecoration(labelText: 'New amount (₹)', prefixText: '₹ '),
                  onChanged: (_) => set(() {}),
                ),
                TextField(
                  controller: reason,
                  decoration: const InputDecoration(labelText: 'Reason'),
                  onChanged: (_) => set(() {}),
                ),
                TextField(
                  controller: pin,
                  obscureText: true,
                  keyboardType: TextInputType.number,
                  inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                  decoration: const InputDecoration(labelText: 'Supervisor PIN'),
                  onChanged: (_) => set(() {}),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
            FilledButton(
              onPressed: ok
                  ? () => Navigator.pop(c, OverrideRequest(rupeesVal * 100, reason.text.trim(), pin.text.trim()))
                  : null,
              child: const Text('Apply'),
            ),
          ],
        );
      },
    ),
  );
}
