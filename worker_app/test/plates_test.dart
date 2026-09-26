// Mirrors backend/tests/test_plates.py, plus OCR-line extraction for the camera scan.
import 'package:flutter_test/flutter_test.dart';
import 'package:parking_worker/domain/plates.dart' as plates;

void main() {
  test('normalise and display', () {
    expect(plates.normalise(' mh 43-ab.1234 '), 'MH43AB1234');
    expect(plates.display('MH43AB1234'), 'MH 43 AB 1234');
    expect(plates.display('22BH1234AA'), '22 BH 1234 AA');
    expect(plates.display('MH431234'), 'MH 43 1234');
  });

  test('validation formats', () {
    expect(plates.isValid('MH43AB1234'), isTrue);
    expect(plates.isValid('MH4A1234'), isTrue);
    expect(plates.isValid('DL1CAB1234'), isTrue);
    expect(plates.isValid('22BH1234AA'), isTrue);
    expect(plates.isValid('XX43AB1234'), isFalse); // unknown state code
    expect(plates.isValid('MH43AB123'), isFalse);
    expect(plates.isValid('XX43AB1234', ['XX']), isTrue);
  });

  test('confusion correction is position aware', () {
    expect(plates.correct('MH43AB1Z34').plate, 'MH43AB1234'); // Z in digit block -> 2
    expect(plates.correct('MH43A81234').plate, 'MH43AB1234'); // 8 in letter block -> B
    expect(plates.correct('M H 4 3 A B l 2 3 4').plate, 'MH43AB1234');
    final c = plates.correct('0L5CAB1234'); // state slot fixed to a valid code
    expect(c.valid, isTrue);
    expect(c.plate.startsWith('DL5'), isTrue);
    expect(plates.correct('22BH1234AA').substitutions, 0);
    final bh = plates.correct('22B41234AA');
    expect(bh.valid == false || bh.plate == '22BH1234AA', isTrue);
  });

  test('canonical and fuzzy', () {
    expect(plates.canonical('MH43AB1234'), plates.canonical('MH43A81234'));
    expect(plates.fuzzyDistance('MH43AB1234', 'MH43AB1Z34'), 0);
    expect(plates.fuzzyDistance('MH43AB1234', 'MH43AB1235'), 1);
    expect(plates.fuzzyDistance('MH43AB1234', 'MH43AB123'), 1);
    expect(plates.fuzzyDistance('MH43AB1234', 'KA01ZZ9999', 1), 2);
  });

  test('levenshtein', () {
    expect(plates.levenshtein('kitten', 'sitting'), 3);
    expect(plates.levenshtein('', 'abc'), 3);
    expect(plates.levenshtein('abc', 'abc'), 0);
    expect(plates.levenshtein('abcdef', 'a', 1), 2);
  });

  test('mask', () {
    expect(plates.mask('MH43AB1234'), 'MH43••••34');
  });

  group('extractPlate (on-device OCR lines)', () {
    test('single line with noise', () {
      expect(plates.extractPlate(['IND', 'MH 43 AB 1234']), 'MH43AB1234');
    });
    test('two-row bike plate', () {
      expect(plates.extractPlate(['MH 43 AB', '1234']), 'MH43AB1234');
    });
    test('confusable characters are corrected', () {
      expect(plates.extractPlate(['MH43A8 1Z34']), 'MH43AB1234');
    });
    test('surrounding text on the same line', () {
      expect(plates.extractPlate(['HONDA', 'KA01AB1234 SERVICE']), 'KA01AB1234');
    });
    test('BH series', () {
      expect(plates.extractPlate(['22 BH 1234 AA']), '22BH1234AA');
    });
    test('nothing valid falls back to the longest read (for approximate search)', () {
      expect(plates.extractPlate(['XYZ123', 'AB']), 'XYZ123');
      expect(plates.extractPlate(['AB', '12']), isNull);
      expect(plates.extractPlate([]), isNull);
    });
  });
}
