/// Indian number-plate normalisation, validation, confusion-aware correction and
/// fuzzy compare. Port of `backend/app/domain/plates.py`, plus [extractPlate]
/// which turns on-device OCR lines into the most plausible plate.
library;

final RegExp _standardRe = RegExp(r'^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$');
final RegExp _bhRe = RegExp(r'^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$');
final RegExp _nonAlnum = RegExp(r'[^A-Z0-9]');
final RegExp _isLetter = RegExp(r'^[A-Z]$');
final RegExp _isDigit = RegExp(r'^[0-9]$');

const List<String> defaultStateCodes = [
  'AN', 'AP', 'AR', 'AS', 'BR', 'CG', 'CH', 'DD', 'DL', 'DN', 'GA', 'GJ', 'HP', 'HR', 'JH', //
  'JK', 'KA', 'KL', 'LA', 'LD', 'MH', 'ML', 'MN', 'MP', 'MZ', 'NL', 'OD', 'OR', 'PB', 'PY', //
  'RJ', 'SK', 'TN', 'TR', 'TS', 'UK', 'UA', 'UP', 'WB',
];

/// Characters ANPR commonly confuses. Canonical form maps each group to its first symbol.
const List<String> confusionGroups = ['0ODQ', '1IL', '2Z', '5S', '8B', '6G'];

final Map<String, String> _canon = {
  for (final g in confusionGroups)
    for (final c in g.split('')) c: g[0],
};
final Map<String, String> _letterToDigit = {
  for (final g in confusionGroups)
    for (final c in g.substring(1).split('')) c: g[0],
};
final Map<String, String> _digitToLetter = {for (final g in confusionGroups) g[0]: g[1]};

/// Upper-case, strip spaces/dashes/dots. 'mh 43-ab 1234' -> 'MH43AB1234'.
String normalise(String? raw) {
  if (raw == null || raw.isEmpty) return '';
  return raw.toUpperCase().replaceAll(_nonAlnum, '');
}

/// Collapse confusable characters so near-identical reads compare equal.
String canonical(String plate) => plate.split('').map((c) => _canon[c] ?? c).join();

bool isValid(String plate, [Iterable<String>? stateCodes]) {
  if (_bhRe.hasMatch(plate)) return true;
  if (_standardRe.hasMatch(plate)) {
    final codes = (stateCodes ?? defaultStateCodes).toSet();
    return codes.contains(plate.substring(0, 2));
  }
  return false;
}

/// Human format: 'MH43AB1234' -> 'MH 43 AB 1234', BH: '22 BH 1234 AA'.
String display(String plate) {
  final bh = RegExp(r'^([0-9]{2})(BH)([0-9]{4})([A-Z]{1,2})$').firstMatch(plate);
  if (bh != null) return [bh[1], bh[2], bh[3], bh[4]].join(' ');
  final m = RegExp(r'^([A-Z]{2})([0-9]{1,2})([A-Z]{0,3})([0-9]{4})$').firstMatch(plate);
  if (m != null) {
    return [m[1], m[2], m[3], m[4]].where((g) => g != null && g.isNotEmpty).join(' ');
  }
  return plate;
}

class Correction {
  const Correction(this.plate, this.substitutions, this.valid);
  final String plate;
  final int substitutions;
  final bool valid;

  @override
  String toString() => 'Correction($plate, subs=$substitutions, valid=$valid)';
}

/// Type templates (L=letter, D=digit, B=literal from 'BH') of every valid layout of length n.
List<String> _templates(int n) {
  final out = <String>[];
  for (final d1 in [1, 2]) {
    for (var letters = 0; letters < 4; letters++) {
      if (2 + d1 + letters + 4 == n) out.add('LL${'D' * d1}${'L' * letters}DDDD');
    }
  }
  for (final tail in [1, 2]) {
    if (2 + 2 + 4 + tail == n) out.add('DDBBDDDD${'L' * tail}');
  }
  return out;
}

/// Position-aware correction using the confusion map (see backend `plates.correct`).
Correction correct(String raw, [Iterable<String>? stateCodes]) {
  final plate = normalise(raw);
  final codes = (stateCodes ?? defaultStateCodes).toList();
  Correction? best;
  for (final tpl in _templates(plate.length)) {
    var subs = 0;
    final chars = StringBuffer();
    var ok = true;
    for (var i = 0; i < plate.length; i++) {
      final c = plate[i];
      final t = tpl[i];
      if (t == 'B') {
        final want = 'BH'[i - 2];
        if (c == want) {
          chars.write(c);
        } else if (canonical(c) == canonical(want)) {
          chars.write(want);
          subs++;
        } else {
          ok = false;
          break;
        }
      } else if (t == 'L') {
        if (_isLetter.hasMatch(c)) {
          chars.write(c);
        } else if (_digitToLetter.containsKey(c)) {
          chars.write(_digitToLetter[c]);
          subs++;
        } else {
          ok = false;
          break;
        }
      } else {
        if (_isDigit.hasMatch(c)) {
          chars.write(c);
        } else if (_letterToDigit.containsKey(c)) {
          chars.write(_letterToDigit[c]);
          subs++;
        } else {
          ok = false;
          break;
        }
      }
    }
    if (!ok) continue;
    var cand = chars.toString();
    if (!isValid(cand, codes)) {
      if (tpl.startsWith('LL') && !codes.contains(cand.substring(0, 2))) {
        final fixed = _fixState(cand, codes);
        if (fixed == null) continue;
        for (var i = 0; i < 2; i++) {
          if (cand[i] != fixed[i]) subs++;
        }
        cand = fixed;
      } else {
        continue;
      }
    }
    final c = Correction(cand, subs, true);
    if (best == null || c.substitutions < best.substitutions) best = c;
  }
  return best ?? Correction(plate, 0, isValid(plate, codes));
}

String? _fixState(String cand, List<String> codes) {
  for (final code in codes) {
    if (canonical(cand[0]) == canonical(code[0]) && canonical(cand[1]) == canonical(code[1])) {
      return code + cand.substring(2);
    }
  }
  return null;
}

/// Edit distance with optional early exit when it exceeds [limit].
int levenshtein(String a, String b, [int? limit]) {
  if (a == b) return 0;
  if (limit != null && (a.length - b.length).abs() > limit) return limit + 1;
  if (a.length < b.length) {
    final t = a;
    a = b;
    b = t;
  }
  var prev = List<int>.generate(b.length + 1, (i) => i);
  for (var i = 1; i <= a.length; i++) {
    final cur = <int>[i];
    var rowMin = i;
    for (var j = 1; j <= b.length; j++) {
      final cost = a[i - 1] == b[j - 1] ? 0 : 1;
      var v = prev[j] + 1;
      if (cur[j - 1] + 1 < v) v = cur[j - 1] + 1;
      if (prev[j - 1] + cost < v) v = prev[j - 1] + cost;
      cur.add(v);
      if (v < rowMin) rowMin = v;
    }
    if (limit != null && rowMin > limit) return limit + 1;
    prev = cur;
  }
  return prev.last;
}

/// Distance after applying the confusion map to both sides.
int fuzzyDistance(String a, String b, [int? limit]) => levenshtein(canonical(a), canonical(b), limit);

/// Partial masking for public display: 'MH43AB1234' -> 'MH43••••34'.
String mask(String plate) {
  if (plate.length <= 6) {
    return plate.substring(0, plate.length < 2 ? plate.length : 2) + '•' * (plate.length > 2 ? plate.length - 2 : 0);
  }
  return '${plate.substring(0, 4)}${'•' * (plate.length - 6)}${plate.substring(plate.length - 2)}';
}

/// Picks the most plausible Indian plate from OCR text lines.
///
/// Two-wheeler plates are usually printed on two rows ("MH 43 AB" / "1234"),
/// so words are joined across lines too. Tier 1 joins *whole words* (in
/// reading order) into 8–10 character candidates; tier 2, used only when tier
/// 1 finds nothing, slides a window over each line (words glued together by
/// OCR, e.g. "KA01AB1234SERVICE"). Each candidate goes through [correct]; the
/// valid result with the fewest confusion substitutions (then the longest)
/// wins. If nothing validates, the longest normalised line of 6+ characters is
/// returned so the worker can still search approximately; `null` when there is
/// nothing usable.
String? extractPlate(List<String> lines, [Iterable<String>? stateCodes]) {
  final norm = lines.map(normalise).where((l) => l.isNotEmpty).toList();
  if (norm.isEmpty) return null;

  Correction? pick(Iterable<String> cands) {
    Correction? best;
    for (final cand in cands) {
      final c = correct(cand, stateCodes);
      if (!c.valid) continue;
      if (best == null ||
          c.substitutions < best.substitutions ||
          (c.substitutions == best.substitutions && c.plate.length > best.plate.length)) {
        best = c;
      }
    }
    return best;
  }

  // Tier 1: whole words, joined in reading order across lines.
  final words = <String>[
    for (final l in lines)
      for (final w in l.split(RegExp(r'[\s,;:|]+')))
        if (normalise(w).isNotEmpty) normalise(w),
  ];
  final tier1 = <String>{};
  for (var i = 0; i < words.length; i++) {
    var acc = '';
    for (var j = i; j < words.length; j++) {
      acc += words[j];
      if (acc.length > 13) break;
      for (final cand in [acc, if (acc.startsWith('IND')) acc.substring(3)]) {
        if (cand.length >= 8 && cand.length <= 10) tier1.add(cand);
      }
    }
  }
  final best1 = pick(tier1);
  if (best1 != null) return best1.plate;

  // Tier 2: sliding windows over lines and adjacent-line joins.
  final sources = <String>[...norm];
  for (var i = 0; i + 1 < norm.length; i++) {
    sources.add(norm[i] + norm[i + 1]);
  }
  final tier2 = <String>{};
  for (final src in sources) {
    for (var len = 10; len >= 8; len--) {
      for (var start = 0; start + len <= src.length; start++) {
        tier2.add(src.substring(start, start + len));
      }
    }
  }
  final best2 = pick(tier2);
  if (best2 != null) return best2.plate;

  final longest = norm.reduce((a, b) => b.length > a.length ? b : a);
  return longest.length >= 6 ? longest : null;
}
