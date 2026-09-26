import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../api/models.dart';
import '../../state/app_state.dart';
import '../../util/format.dart';
import '../../widgets/common.dart';

/// Zone assignments for today; assign a worker to a zone for a time window.
class ZonesScreen extends StatefulWidget {
  const ZonesScreen({super.key});

  @override
  State<ZonesScreen> createState() => _ZonesScreenState();
}

class _ZonesScreenState extends State<ZonesScreen> {
  List<ZoneAssignmentInfo>? _rows;
  List<ZoneInfo> _zones = [];
  List<UserInfo> _users = [];
  String? _error;

  AppState get _app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final z = await _app.api.zones();
      final u = await _app.api.users();
      final a = await _app.api.zoneAssignments();
      if (mounted) {
        setState(() {
          _zones = z;
          _users = u.where((x) => x.active && (x.role == Role.worker || x.role == Role.supervisor)).toList();
          _rows = a;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = errorText(e));
    }
  }

  String _zoneName(int id) => _zones
      .firstWhere(
        (z) => z.id == id,
        orElse: () => ZoneInfo(id: id, name: 'Zone $id'),
      )
      .name;

  Future<void> _add() async {
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _AssignSheet(zones: _zones, users: _users),
    );
    if (ok == true) await _load();
  }

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    return Scaffold(
      appBar: AppBar(title: const Text('Zone assignments (today)')),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _zones.isEmpty || _users.isEmpty ? null : _add,
        icon: const Icon(Icons.add),
        label: const Text('Assign'),
      ),
      body: Column(
        children: [
          const ConnectivityBar(),
          if (_error != null) Text(_error!, style: const TextStyle(color: dueRed)),
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
                  : _rows!.isEmpty
                  ? ListView(children: const [EmptyState('No assignments today')])
                  : ListView(
                      padding: const EdgeInsets.only(bottom: 80),
                      children: [
                        for (final a in _rows!)
                          ListTile(
                            leading: Icon(
                              Icons.person_pin_circle,
                              color: (a.startsAt?.isBefore(now) ?? false) && (a.endsAt?.isAfter(now) ?? false)
                                  ? paidGreen
                                  : Colors.grey,
                            ),
                            title: Text('${a.userName} → ${_zoneName(a.zoneId)}'),
                            subtitle: Text(
                              '${dateTimeIst(a.startsAt)} – ${timeIst(a.endsAt)}'
                              '${a.shiftLabel.isNotEmpty ? ' · ${a.shiftLabel}' : ''}',
                            ),
                          ),
                      ],
                    ),
            ),
          ),
        ],
      ),
    );
  }
}

class _AssignSheet extends StatefulWidget {
  const _AssignSheet({required this.zones, required this.users});
  final List<ZoneInfo> zones;
  final List<UserInfo> users;

  @override
  State<_AssignSheet> createState() => _AssignSheetState();
}

class _AssignSheetState extends State<_AssignSheet> {
  int? _zone;
  int? _user;
  late DateTime _start; // IST wall clock, stored as UTC fields
  late DateTime _end;
  final _label = TextEditingController();
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    final nowIst = toIst(DateTime.now());
    _start = DateTime.utc(nowIst.year, nowIst.month, nowIst.day, nowIst.hour);
    _end = _start.add(const Duration(hours: 8));
  }

  /// IST wall-clock (held in UTC fields) → real UTC instant.
  DateTime _toUtc(DateTime istWall) => istWall.subtract(siteOffset);

  Future<void> _pick(bool start) async {
    final cur = start ? _start : _end;
    final t = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(hour: cur.hour, minute: cur.minute),
    );
    if (t == null) return;
    setState(() {
      final d = DateTime.utc(cur.year, cur.month, cur.day, t.hour, t.minute);
      if (start) {
        _start = d;
        if (!_end.isAfter(_start)) _end = _start.add(const Duration(hours: 8));
      } else {
        _end = d.isAfter(_start) ? d : d.add(const Duration(days: 1)); // overnight shift
      }
    });
  }

  Future<void> _save() async {
    setState(() => _busy = true);
    try {
      await context.read<AppState>().api.assignZone(
        zoneId: _zone!,
        userId: _user!,
        startsAt: _toUtc(_start),
        endsAt: _toUtc(_end),
        shiftLabel: _label.text.trim(),
      );
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _fmt(DateTime istWall) =>
      '${istWall.day}/${istWall.month} ${istWall.hour.toString().padLeft(2, '0')}:${istWall.minute.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 20, 20, MediaQuery.of(context).viewInsets.bottom + 20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text('Assign zone', style: TextStyle(fontSize: 19, fontWeight: FontWeight.w800)),
          DropdownButtonFormField<int>(
            initialValue: _user,
            decoration: const InputDecoration(labelText: 'Worker'),
            items: [for (final u in widget.users) DropdownMenuItem(value: u.id, child: Text('${u.name} (${u.role})'))],
            onChanged: (v) => setState(() => _user = v),
          ),
          DropdownButtonFormField<int>(
            initialValue: _zone,
            decoration: const InputDecoration(labelText: 'Zone'),
            items: [for (final z in widget.zones) DropdownMenuItem(value: z.id, child: Text(z.name))],
            onChanged: (v) => setState(() => _zone = v),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(onPressed: () => _pick(true), child: Text('From ${_fmt(_start)}')),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton(onPressed: () => _pick(false), child: Text('To ${_fmt(_end)}')),
              ),
            ],
          ),
          TextField(
            controller: _label,
            decoration: const InputDecoration(labelText: 'Shift label (optional) e.g. Morning'),
          ),
          const SizedBox(height: 16),
          SizedBox(
            height: 52,
            child: FilledButton(
              onPressed: _busy || _zone == null || _user == null ? null : _save,
              child: const Text('Save assignment'),
            ),
          ),
        ],
      ),
    );
  }
}
