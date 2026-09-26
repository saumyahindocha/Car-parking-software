import 'package:flutter/material.dart';

import '../search_screen.dart';
import 'deposit_screen.dart';
import 'disputes_screen.dart';
import 'handovers_screen.dart';
import 'holdings_screen.dart';
import 'zones_screen.dart';

/// Supervisor tools (the supervisor also has all worker screens).
class SupervisorHome extends StatelessWidget {
  const SupervisorHome({super.key});

  @override
  Widget build(BuildContext context) {
    void go(Widget w) => Navigator.of(context).push(MaterialPageRoute(builder: (_) => w));
    final tiles = <(IconData, String, String, VoidCallback)>[
      (Icons.handshake, 'Cash handovers', 'Count, photograph and confirm', () => go(const HandoversScreen())),
      (Icons.account_balance_wallet, 'Cash in hand', 'Live, every worker', () => go(const HoldingsScreen())),
      (Icons.gavel, 'Disputes', 'Resolve open disputes', () => go(const DisputesScreen())),
      (Icons.map, 'Zones', 'Assign workers to zones', () => go(const ZonesScreen())),
      (
        Icons.undo,
        'Reverse / refund',
        'Cash reversal or UPI refund, with reason',
        () => go(const SearchScreen(supervisorMode: true)),
      ),
      (Icons.account_balance, 'Bank deposit', 'Record deposit with slip photo', () => go(const DepositScreen())),
    ];
    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 8,
          crossAxisSpacing: 8,
          childAspectRatio: 1.25,
          children: [
            for (final (icon, title, sub, onTap) in tiles)
              Card(
                child: InkWell(
                  onTap: onTap,
                  borderRadius: BorderRadius.circular(12),
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(icon, size: 34, color: Theme.of(context).colorScheme.primary),
                        const Spacer(),
                        Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800)),
                        Text(sub, style: const TextStyle(fontSize: 12, color: Colors.black54)),
                      ],
                    ),
                  ),
                ),
              ),
          ],
        ),
        const SizedBox(height: 8),
        const Card(
          child: ListTile(
            leading: Icon(Icons.password),
            title: Text('Approving an amount override'),
            subtitle: Text(
              'Overrides are approved on the worker\'s phone: the worker taps "Different amount?", '
              'enters the amount and reason, and you type your supervisor PIN there. Every override is in the daily report.',
            ),
          ),
        ),
      ],
    );
  }
}
