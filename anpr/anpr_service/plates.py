"""Indian number-plate validation, confusion-aware correction and matching.

Formats (after removing spaces / punctuation):

* Standard: ``^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$``  e.g. ``MH43AB1234``
* BH series: ``^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$``           e.g. ``22BH1234AA``

OCR engines routinely confuse visually similar glyphs.  The confusion groups
``0/O/D/Q``, ``1/I/L``, ``2/Z``, ``5/S``, ``8/B`` and ``6/G`` are used

* for *position-aware correction*: a digit read where the format requires a
  letter (or vice versa) is swapped for a member of its group;
* for *confusion-aware equality*: two reads are the same plate when they are
  identical after every character is mapped to its group representative.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

STANDARD_RE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$")
BH_RE = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")

# All state / UT registration codes in use (including legacy ones still seen
# on older vehicles).  Configurable through ``plates.state_codes``.
DEFAULT_STATE_CODES: tuple[str, ...] = (
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ",
    "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP",
    "MZ", "NL", "OD", "OR", "PB", "PY", "RJ", "SK", "TN", "TR", "TS", "UK",
    "UA", "UP", "WB",
)

CONFUSION_GROUPS: tuple[str, ...] = ("0ODQ", "1IL", "2Z", "5S", "8B", "6G")

_CANON: dict[str, str] = {c: g[0] for g in CONFUSION_GROUPS for c in g}
_GROUP_OF: dict[str, str] = {c: g for g in CONFUSION_GROUPS for c in g}

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class PlateRules:
    state_codes: frozenset[str] = frozenset(DEFAULT_STATE_CODES)
    allow_bh: bool = True
    require_state_code: bool = True
    # Letters never issued in the series part of standard plates; a correction
    # will not *introduce* them (a genuinely read I/O is left alone).
    series_excluded: frozenset[str] = frozenset("IO")

    @classmethod
    def from_config(
        cls,
        state_codes: Iterable[str] | None = None,
        allow_bh: bool = True,
        require_state_code: bool = True,
    ) -> "PlateRules":
        codes = frozenset(c.strip().upper() for c in (state_codes or DEFAULT_STATE_CODES))
        return cls(state_codes=codes, allow_bh=allow_bh, require_state_code=require_state_code)


DEFAULT_RULES = PlateRules()


@dataclass(frozen=True, order=True)
class Correction:
    substitutions: int
    plate: str = field(compare=False)
    fmt: str = field(compare=False)  # "STANDARD" | "BH"


def normalize(text: str | None) -> str:
    """Upper-case and strip everything that is not A-Z / 0-9."""
    if not text:
        return ""
    return _NON_ALNUM.sub("", text.upper())


def plate_format(plate: str, rules: PlateRules = DEFAULT_RULES) -> str | None:
    """Return ``"STANDARD"``/``"BH"`` when ``plate`` is valid, else ``None``."""
    p = normalize(plate)
    if STANDARD_RE.match(p):
        if not rules.require_state_code or p[:2] in rules.state_codes:
            return "STANDARD"
        return None
    if rules.allow_bh and BH_RE.match(p):
        return "BH"
    return None


def is_valid(plate: str, rules: PlateRules = DEFAULT_RULES) -> bool:
    return plate_format(plate, rules) is not None


def canonical(plate: str) -> str:
    """Map every character to its confusion-group representative."""
    return "".join(_CANON.get(c, c) for c in normalize(plate))


def plates_equal(a: str | None, b: str | None) -> bool:
    """Confusion-aware equality (``MH12AB1234 == MHI2A81234``)."""
    if not a or not b:
        return False
    return canonical(a) == canonical(b)


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def approx_match(a: str | None, b: str | None, max_distance: int = 1) -> bool:
    """Confusion-aware match allowing ``max_distance`` edits (Section 4 matching)."""
    if not a or not b:
        return False
    return levenshtein(canonical(a), canonical(b)) <= max_distance


def _letters_for(ch: str) -> list[str]:
    return [c for c in _GROUP_OF.get(ch, "") if c.isalpha() and c != ch]


def _digits_for(ch: str) -> list[str]:
    return [c for c in _GROUP_OF.get(ch, "") if c.isdigit() and c != ch]


def _patterns(length: int, rules: PlateRules) -> list[tuple[str, str]]:
    """Character-class patterns ('L' letter, 'D' digit, literal) of this length."""
    out: list[tuple[str, str]] = []
    for d in (2, 1):
        for a in (2, 1, 3, 0):
            if 2 + d + a + 4 == length:
                out.append(("STANDARD", "SS" + "D" * d + "L" * a + "DDDD"))
    if rules.allow_bh:
        for a in (2, 1):
            if 2 + 2 + 4 + a == length:
                out.append(("BH", "DD" + "BH" + "DDDD" + "L" * a))
    return out


def _options(ch: str, cls: str, rules: PlateRules) -> list[tuple[str, int]]:
    """Possible characters (with substitution cost) at a position of class ``cls``."""
    if cls == "D":
        if ch.isdigit():
            return [(ch, 0)]
        return [(c, 1) for c in _digits_for(ch)]
    if cls in ("L", "S"):
        if ch.isalpha():
            opts = [(ch, 0)]
            if cls == "S":  # state code: letter-letter confusions (O/D/Q, I/L) allowed
                opts += [(c, 1) for c in _letters_for(ch)]
            return opts
        letters = _letters_for(ch)
        if cls == "L":
            preferred = [c for c in letters if c not in rules.series_excluded]
            letters = preferred or letters
        return [(c, 1) for c in letters]
    # literal character (e.g. 'B', 'H' of the BH series)
    if ch == cls:
        return [(ch, 0)]
    if cls in _GROUP_OF.get(ch, ""):
        return [(cls, 1)]
    return []


def correct(text: str, rules: PlateRules = DEFAULT_RULES, max_substitutions: int = 3) -> list[Correction]:
    """All valid readings reachable from ``text`` by confusion substitutions.

    Sorted by number of substitutions (fewest first).  An already-valid plate is
    returned with zero substitutions.
    """
    raw = normalize(text)
    results: dict[str, Correction] = {}
    for fmt, pattern in _patterns(len(raw), rules):
        per_pos: list[list[tuple[str, int]]] = []
        for ch, cls in zip(raw, pattern):
            opts = _options(ch, cls, rules)
            if not opts:
                break
            per_pos.append(opts)
        else:
            # Bound the search: only positions with alternatives multiply out.
            combos = 1
            for opts in per_pos:
                combos *= len(opts)
            if combos > 4096:
                continue
            for combo in itertools.product(*per_pos):
                cost = sum(c for _, c in combo)
                if cost > max_substitutions:
                    continue
                cand = "".join(ch for ch, _ in combo)
                if plate_format(cand, rules) != fmt:
                    continue
                prev = results.get(cand)
                if prev is None or cost < prev.substitutions:
                    results[cand] = Correction(cost, cand, fmt)
    return sorted(results.values())


def best_correction(text: str, rules: PlateRules = DEFAULT_RULES) -> Correction | None:
    corrections = correct(text, rules)
    return corrections[0] if corrections else None


def split_rows(plate: str) -> tuple[str, str]:
    """Split a plate into the two rows used on two-line plates.

    Standard: ``MH43`` / ``AB1234``; BH series: ``22BH`` / ``1234AA``.
    Used for synthetic rendering and for OCR training labels.
    """
    p = normalize(plate)
    m = re.match(r"^([A-Z]{2}[0-9]{1,2})([A-Z]{0,3}[0-9]{4})$", p)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^([0-9]{2}BH)([0-9]{4}[A-Z]{1,2})$", p)
    if m:
        return m.group(1), m.group(2)
    half = (len(p) + 1) // 2
    return p[:half], p[half:]


def format_display(plate: str) -> str:
    """Human-friendly spacing: ``MH 43 AB 1234`` / ``22 BH 1234 AA``."""
    p = normalize(plate)
    m = re.match(r"^([A-Z]{2})([0-9]{1,2})([A-Z]{0,3})([0-9]{4})$", p)
    if m:
        return " ".join(g for g in m.groups() if g)
    m = re.match(r"^([0-9]{2})(BH)([0-9]{4})([A-Z]{1,2})$", p)
    if m:
        return " ".join(m.groups())
    return p


def rank_by_validity(texts: Sequence[str], rules: PlateRules = DEFAULT_RULES) -> list[str]:
    """Stable ordering that puts valid plates first."""
    return sorted(texts, key=lambda t: 0 if is_valid(t, rules) else 1)
