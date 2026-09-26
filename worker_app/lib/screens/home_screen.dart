import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../state/app_state.dart';
import '../widgets/common.dart';
import '../widgets/dialogs.dart';
import 'cash_screen.dart';
import 'collect_screen.dart';
import 'guard_alerts_screen.dart';
import 'search_screen.dart';
import 'shift_screen.dart';
import 'supervisor/supervisor_home.dart';
import 'sync_status_screen.dart';

class _Tab {
  const _Tab(this.label, this.icon, this.builder);
  final String label;
  final IconData icon;
  final Widget Function() builder;
}

/// Role-based home: WORKER (collect), GUARD (exit alerts), SUPERVISOR (worker
/// screens + supervisor tools).
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _index = 0;

  List<_Tab> _tabs(AppState app) {
    final u = app.user!;
    if (u.isGuard) {
      return [
        _Tab('Exit alerts', Icons.notifications_active, () => const GuardAlertsScreen()),
        _Tab('Sync', Icons.sync, () => const SyncStatusScreen(embedded: true)),
      ];
    }
    final tabs = <_Tab>[
      _Tab('To collect', Icons.format_list_bulleted, () => const CollectScreen()),
      _Tab('Search', Icons.search, () => const SearchScreen()),
      _Tab('Cash', Icons.payments, () => const CashScreen()),
    ];
    if (u.isSupervisor) {
      tabs.add(_Tab('Supervisor', Icons.admin_panel_settings, () => const SupervisorHome()));
    } else {
      tabs.add(_Tab('Shift', Icons.schedule, () => const ShiftScreen()));
    }
    tabs.add(_Tab('Sync', Icons.sync, () => const SyncStatusScreen(embedded: true)));
    return tabs;
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final tabs = _tabs(app);
    if (_index >= tabs.length) _index = 0;
    final u = app.user!;
    final zone = app.bootstrap?.zone;
    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(tabs[_index].label),
            Text(
              '${u.name} · ${u.role}${zone != null ? ' · ${zone.name}' : ''}',
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w400),
            ),
          ],
        ),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 4),
            child: Icon(
              app.online ? Icons.cloud_done : Icons.cloud_off,
              color: app.online ? Colors.greenAccent.shade700 : Colors.red,
            ),
          ),
          PopupMenuButton<String>(
            onSelected: (v) async {
              switch (v) {
                case 'shift':
                  Navigator.of(context).push(MaterialPageRoute(builder: (_) => const ShiftScreen(standalone: true)));
                case 'refresh':
                  await app.refreshBootstrap();
                  await app.refreshCash();
                  if (context.mounted) {
                    showSnack(context, app.online ? 'Settings refreshed' : 'Offline: using cached settings');
                  }
                case 'logout':
                  final pending = app.sync?.pendingCount ?? 0;
                  final ok = await confirmDialog(
                    context,
                    'Log out?',
                    pending > 0
                        ? '$pending action(s) are not synced yet. They stay on this phone and sync when you log in again.'
                        : 'You will need your PIN to log in again.',
                    ok: 'Log out',
                  );
                  if (ok) await app.logout();
              }
            },
            itemBuilder: (_) => [
              if (u.isSupervisor) const PopupMenuItem(value: 'shift', child: Text('My shift')),
              const PopupMenuItem(value: 'refresh', child: Text('Refresh settings & tariffs')),
              const PopupMenuItem(value: 'logout', child: Text('Log out')),
            ],
          ),
        ],
      ),
      body: Column(
        children: [
          const ConnectivityBar(),
          if (u.isCollector) const CashLimitBanner(),
          Expanded(child: tabs[_index].builder()),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: [
          for (final t in tabs)
            NavigationDestination(
              icon: t.label == 'Sync' && ((app.sync?.pendingCount ?? 0) + (app.sync?.failedCount ?? 0)) > 0
                  ? Badge(
                      label: Text('${(app.sync?.pendingCount ?? 0) + (app.sync?.failedCount ?? 0)}'),
                      child: Icon(t.icon),
                    )
                  : Icon(t.icon),
              label: t.label,
            ),
        ],
      ),
    );
  }
}
