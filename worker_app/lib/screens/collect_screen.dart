import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/models.dart';
import '../domain/pay_request.dart';
import '../state/app_state.dart';
import '../util/format.dart';
import '../widgets/common.dart';
import 'collect_flow_screen.dart';

CollectTarget targetFromItem(CollectItem i) => CollectTarget(
      vehicleId: i.vehicleId,
      plate: i.plate,
      displayPlate: i.displayPlate,
      vehicleClass: i.vehicleClass,
      sessionId: i.sessionId,
      entryAt: i.entryAt,
      gateId: i.gateId,
      zoneId: i.zoneId,
      plateImage: i.plateImage,
      duesPaise: i.previousDuePaise,
      creditPaise: i.creditPaise,
      passCandidate: i.passCandidate,
      phoneKnown: i.phoneKnown,
    );

/// Primary worker screen: open sessions without payment, newest first.
class CollectScreen extends StatelessWidget {
  const CollectScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final model = app.collect;
    if (model == null) return const EmptyState('Not available for this role');
    return ListenableBuilder(
      listenable: model,
      builder: (context, _) {
        final items = model.items;
        final zone = app.bootstrap?.zone;
        return RefreshIndicator(
          onRefresh: () => model.refresh(),
          child: CustomScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
                  child: Row(children: [
                    Text('${items.length} to collect', style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                    const SizedBox(width: 8),
                    if (model.loading) const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
                    const Spacer(),
                    if (zone != null)
                      FilterChip(
                        label: Text('Only ${zone.name}'),
                        selected: model.onlyMyZone,
                        onSelected: model.setOnlyMyZone,
                      ),
                  ]),
                ),
              ),
              if (model.error != null || model.fromCache)
                SliverToBoxAdapter(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                    child: Text(
                      model.error ?? 'Cached list',
                      style: const TextStyle(color: Colors.deepOrange, fontSize: 12),
                    ),
                  ),
                ),
              if (model.updatedAt != null)
                SliverToBoxAdapter(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: Text('Updated ${ago(model.updatedAt)}', style: const TextStyle(color: Colors.black45, fontSize: 12)),
                  ),
                ),
              if (items.isEmpty && !model.loading)
                const SliverFillRemaining(
                  hasScrollBody: false,
                  child: EmptyState('Nothing to collect right now.\nNew entries appear here automatically.', icon: Icons.check_circle_outline),
                ),
              SliverList.separated(
                itemCount: items.length,
                separatorBuilder: (_, _) => const Divider(height: 1),
                itemBuilder: (context, i) => _CollectTile(item: items[i], gateName: app.bootstrap?.gates[items[i].gateId]),
              ),
              const SliverToBoxAdapter(child: SizedBox(height: 24)),
            ],
          ),
        );
      },
    );
  }
}

class _CollectTile extends StatelessWidget {
  const _CollectTile({required this.item, this.gateName});
  final CollectItem item;
  final String? gateName;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => CollectFlowScreen(target: targetFromItem(item)))),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        child: Row(children: [
          PlateImage(item.plateImage, width: 110, height: 56),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Flexible(child: PlateText(item.displayPlate, size: 17)),
                const SizedBox(width: 6),
                if (item.vehicleClass != 'BIKE') Badge2(item.vehicleClass, color: Colors.teal),
              ]),
              const SizedBox(height: 4),
              Text(
                'In ${timeIst(item.entryAt)} (${ago(item.entryAt)}) · ${gateName ?? item.gateId ?? '-'}',
                style: const TextStyle(fontSize: 13, color: Colors.black54),
              ),
              const SizedBox(height: 3),
              Wrap(spacing: 6, runSpacing: 3, children: [
                if (item.previousDuePaise > 0) Badge2('Dues ${rupees(item.previousDuePaise)}', color: dueRed, icon: Icons.warning),
                if (item.creditPaise > 0) Badge2('Credit ${rupees(item.creditPaise)}', color: paidGreen),
                if (item.passCandidate) const Badge2('Pass candidate', color: Colors.purple, icon: Icons.card_membership),
                if (item.unpaidFlagged) const Badge2('30 min+ unpaid', color: Colors.orange),
                if (item.entryMatch == 'APPROX') const Badge2('Approx read', color: Colors.blueGrey),
              ]),
            ]),
          ),
          const Icon(Icons.chevron_right),
        ]),
      ),
    );
  }
}
