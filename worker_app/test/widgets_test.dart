import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:parking_worker/util/format.dart';
import 'package:parking_worker/widgets/denomination_grid.dart';

void main() {
  testWidgets('denomination grid auto-sums the handover amount', (tester) async {
    var counts = <int, int>{};
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(
            builder: (c, set) => SingleChildScrollView(
              child: DenominationGrid(counts: counts, onChanged: (m) => set(() => counts = m)),
            ),
          ),
        ),
      ),
    );
    // 3 x ₹500 typed, then 2 x ₹10 via the + button
    await tester.enterText(find.byType(TextField).first, '3');
    await tester.pump();
    final plus = find.byIcon(Icons.add_circle_outline);
    await tester.tap(plus.at(5)); // ₹10 row
    await tester.tap(plus.at(5));
    await tester.pump();
    expect(counts, {500: 3, 10: 2});
    expect(denominationTotalPaise(counts), 152000);
    expect(find.text('₹1,520'), findsWidgets);
  });

  test('money and phone formatting', () {
    expect(rupees(152000), '₹1,520');
    expect(rupees(1050), '₹10.50');
    expect(rupees(-2000), '-₹20');
    expect(rupees(10000000), '₹1,00,000');
    expect(normalisePhone('+91 98765-43210'), '9876543210');
    expect(normalisePhone('12345'), isNull);
    expect(durationLabel(1440), 'Full day');
    expect(durationLabel(240), '4 h');
  });
}
