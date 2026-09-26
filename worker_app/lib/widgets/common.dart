import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../screens/sync_status_screen.dart';
import '../state/app_state.dart';
import '../util/format.dart';

const Color dueRed = Color(0xFFC62828);
const Color paidGreen = Color(0xFF2E7D32);
const Color upiBlue = Color(0xFF1565C0);

/// Plate-crop thumbnail from `/api/images/...?token=`.
class PlateImage extends StatelessWidget {
  const PlateImage(this.path, {super.key, this.width = 96, this.height = 48, this.fit = BoxFit.cover});
  final String? path;
  final double width;
  final double height;
  final BoxFit fit;

  @override
  Widget build(BuildContext context) {
    final url = context.read<AppState>().api.imageUrl(path);
    final placeholder = Container(
      width: width,
      height: height,
      color: Colors.grey.shade300,
      alignment: Alignment.center,
      child: Icon(Icons.two_wheeler, color: Colors.grey.shade600, size: height * 0.6),
    );
    if (url == null) return ClipRRect(borderRadius: BorderRadius.circular(6), child: placeholder);
    return ClipRRect(
      borderRadius: BorderRadius.circular(6),
      child: Image.network(
        url,
        width: width,
        height: height,
        fit: fit,
        gaplessPlayback: true,
        errorBuilder: (_, _, _) => placeholder,
        loadingBuilder: (c, child, p) => p == null ? child : placeholder,
      ),
    );
  }
}

/// Number plate rendered like the real thing (large, monospace, bordered).
class PlateText extends StatelessWidget {
  const PlateText(this.plate, {super.key, this.size = 20});
  final String plate;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(horizontal: size * 0.4, vertical: size * 0.15),
      decoration: BoxDecoration(
        color: Colors.white,
        border: Border.all(color: Colors.black87, width: 1.5),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(plate,
          style: TextStyle(
              fontFamily: 'monospace', fontWeight: FontWeight.w800, fontSize: size, letterSpacing: 1.2, color: Colors.black)),
    );
  }
}

class Badge2 extends StatelessWidget {
  const Badge2(this.text, {super.key, this.color = Colors.indigo, this.icon});
  final String text;
  final Color color;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(10)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        if (icon != null) ...[Icon(icon, size: 12, color: color), const SizedBox(width: 3)],
        Text(text, style: TextStyle(fontSize: 11, color: color, fontWeight: FontWeight.w700)),
      ]),
    );
  }
}

/// Offline / sync banner shown at the top of every screen. Tap for sync status.
class ConnectivityBar extends StatelessWidget {
  const ConnectivityBar({super.key});

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final pending = app.sync?.pendingCount ?? 0;
    final failed = app.sync?.failedCount ?? 0;
    if (app.online && pending == 0 && failed == 0) return const SizedBox.shrink();
    final Color bg;
    final String text;
    if (!app.online) {
      bg = Colors.red.shade700;
      text = 'OFFLINE — cash & UPI still work. ${pending > 0 ? '$pending waiting to sync' : 'Nothing waiting'}';
    } else if (failed > 0) {
      bg = Colors.deepOrange.shade700;
      text = '$failed item(s) failed to sync — tap to see (show your supervisor)';
    } else {
      bg = Colors.blueGrey.shade700;
      text = 'Syncing $pending item(s)…';
    }
    return Material(
      color: bg,
      child: InkWell(
        onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const SyncStatusScreen())),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          child: Row(children: [
            Icon(app.online ? Icons.sync : Icons.cloud_off, color: Colors.white, size: 16),
            const SizedBox(width: 8),
            Expanded(child: Text(text, style: const TextStyle(color: Colors.white, fontSize: 13))),
            const Icon(Icons.chevron_right, color: Colors.white, size: 16),
          ]),
        ),
      ),
    );
  }
}

/// 80 % warning / limit-reached banner for the cash-in-hand limit.
class CashLimitBanner extends StatelessWidget {
  const CashLimitBanner({super.key});

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    if (app.user == null || !app.user!.isCollector || !app.cashAllowed) return const SizedBox.shrink();
    final pos = app.cashPosition;
    if (!pos.warn) return const SizedBox.shrink();
    final blocked = pos.blocked;
    return Material(
      color: blocked ? dueRed : Colors.amber.shade700,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: Row(children: [
          Icon(blocked ? Icons.block : Icons.warning_amber, color: Colors.white, size: 18),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              blocked
                  ? 'Cash limit reached (${rupees(pos.heldPaise)} / ${rupees(pos.limitPaise)}). Hand over cash — UPI still works.'
                  : 'Cash in hand ${rupees(pos.heldPaise)} of ${rupees(pos.limitPaise)} limit. Hand over soon.',
              style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
            ),
          ),
        ]),
      ),
    );
  }
}

class MoneyRow extends StatelessWidget {
  const MoneyRow(this.label, this.paise, {super.key, this.color, this.bold = false, this.size = 16, this.negative = false});
  final String label;
  final int paise;
  final Color? color;
  final bool bold;
  final double size;
  final bool negative;

  @override
  Widget build(BuildContext context) {
    final style = TextStyle(fontSize: size, color: color, fontWeight: bold ? FontWeight.w800 : FontWeight.w500);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(children: [
        Expanded(child: Text(label, style: style)),
        Text('${negative ? '− ' : ''}${rupees(paise)}', style: style),
      ]),
    );
  }
}

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.text, {super.key});
  final String text;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 16, 4, 6),
        child: Text(text.toUpperCase(),
            style: TextStyle(fontSize: 12, letterSpacing: 1, fontWeight: FontWeight.w700, color: Colors.grey.shade700)),
      );
}

class EmptyState extends StatelessWidget {
  const EmptyState(this.text, {super.key, this.icon = Icons.inbox});
  final String text;
  final IconData icon;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.all(32),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(icon, size: 48, color: Colors.grey),
          const SizedBox(height: 12),
          Text(text, textAlign: TextAlign.center, style: const TextStyle(color: Colors.grey, fontSize: 15)),
        ]),
      );
}

String errorText(Object e) {
  if (e is ApiException) return e.detail;
  if (e is NetworkException) return 'Server unreachable. Check Wi-Fi and try again.';
  return e.toString();
}

void showSnack(BuildContext context, String msg, {bool error = false}) {
  final m = ScaffoldMessenger.maybeOf(context);
  m?.hideCurrentSnackBar();
  m?.showSnackBar(SnackBar(content: Text(msg), backgroundColor: error ? dueRed : null));
}

void showError(BuildContext context, Object e) => showSnack(context, errorText(e), error: true);

/// A large, full-width action button (the app is used standing, one-handed).
class BigButton extends StatelessWidget {
  const BigButton({super.key, required this.label, required this.onPressed, this.icon, this.color, this.outlined = false, this.height = 60});
  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final Color? color;
  final bool outlined;
  final double height;

  @override
  Widget build(BuildContext context) {
    final child = Row(mainAxisAlignment: MainAxisAlignment.center, children: [
      if (icon != null) ...[Icon(icon, size: 26), const SizedBox(width: 10)],
      Flexible(child: Text(label, textAlign: TextAlign.center, style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w700))),
    ]);
    return SizedBox(
      width: double.infinity,
      height: height,
      child: outlined
          ? OutlinedButton(
              onPressed: onPressed,
              style: OutlinedButton.styleFrom(
                  foregroundColor: color, side: BorderSide(color: color ?? Colors.grey, width: 2)),
              child: child)
          : FilledButton(
              onPressed: onPressed,
              style: FilledButton.styleFrom(backgroundColor: color),
              child: child),
    );
  }
}
