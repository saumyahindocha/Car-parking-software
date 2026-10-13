"""Indian number-plate normalisation, validation, confusion-aware correction and fuzzy compare."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

STANDARD_RE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$")
BH_RE = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")

DEFAULT_STATE_CODES: tuple[str, ...] = (
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ", "HP", "HR", "JH",
    "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP", "MZ", "NL", "OD", "OR", "PB", "PY",
    "RJ", "SK", "TN", "TR", "TS", "UK", "UA", "UP", "WB",
)

# Characters that ANPR commonly confuses. Canonical form maps each group to one symbol.
CONFUSION_GROUPS: tuple[str, ...] = ("0ODQ", "1IL", "2Z", "5S", "8B", "6G")
_CANON: dict[str, str] = {c: g[0] for g in CONFUSION_GROUPS for c in g}
LETTER_TO_DIGIT: dict[str, str] = {c: g[0] for g in CONFUSION_GROUPS for c in g[1:]}
DIGIT_TO_LETTER: dict[str, str] = {g[0]: g[1] for g in CONFUSION_GROUPS}

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalise(raw: str | None) -> str:
    """Upper-case, strip spaces/dashes/dots. 'mh 43-ab 1234' -> 'MH43AB1234'."""
    if not raw:
        return ""
    return _NON_ALNUM.sub("", raw.upper())


def canonical(plate: str) -> str:
    """Collapse confusable characters so 'MH43AB1Z34' and 'MH43A81234' compare equal-ish."""
    return "".join(_CANON.get(c, c) for c in plate)


def is_valid(plate: str, state_codes: Iterable[str] | None = None) -> bool:
    if BH_RE.match(plate):
        return True
    if STANDARD_RE.match(plate):
        codes = set(state_codes) if state_codes is not None else set(DEFAULT_STATE_CODES)
        return plate[:2] in codes
    return False


def display(plate: str) -> str:
    """Human format: 'MH43AB1234' -> 'MH 43 AB 1234', BH: '22 BH 1234 AA'."""
    m = re.match(r"^([0-9]{2})(BH)([0-9]{4})([A-Z]{1,2})$", plate)
    if m:
        return " ".join(m.groups())
    m = re.match(r"^([A-Z]{2})([0-9]{1,2})([A-Z]{0,3})([0-9]{4})$", plate)
    if m:
        return " ".join(g for g in m.groups() if g)
    return plate


@dataclass(frozen=True)
class Correction:
    plate: str
    substitutions: int
    valid: bool


def _templates(n: int) -> list[str]:
    """Type templates (L=letter, D=digit) of every valid plate layout of length n."""
    out = []
    for d1 in (1, 2):
        for letters in range(0, 4):
            if 2 + d1 + letters + 4 == n:
                out.append("LL" + "D" * d1 + "L" * letters + "DDDD")
    for tail in (1, 2):
        if 2 + 2 + 4 + tail == n:
            out.append("DDBBDDDD" + "L" * tail)  # B = literal from 'BH'
    return out


def correct(raw: str, state_codes: Iterable[str] | None = None) -> Correction:
    """Position-aware correction using the confusion map.

    Tries every valid Indian layout of the same length; in each position where the
    character type is wrong, substitutes its confusable twin. Returns the valid plate with
    the fewest substitutions, or the normalised input (valid=False) if none fits.
    """
    plate = normalise(raw)
    codes = set(state_codes) if state_codes is not None else set(DEFAULT_STATE_CODES)
    best: Correction | None = None
    for tpl in _templates(len(plate)):
        subs = 0
        chars: list[str] = []
        ok = True
        for i, (c, t) in enumerate(zip(plate, tpl)):
            if t == "B":
                want = "BH"[i - 2]
                if c == want:
                    chars.append(c)
                elif canonical(c) == canonical(want):
                    chars.append(want)
                    subs += 1
                else:
                    ok = False
                    break
            elif t == "L":
                if c.isalpha():
                    chars.append(c)
                elif c in DIGIT_TO_LETTER:
                    chars.append(DIGIT_TO_LETTER[c])
                    subs += 1
                else:
                    ok = False
                    break
            else:
                if c.isdigit():
                    chars.append(c)
                elif c in LETTER_TO_DIGIT:
                    chars.append(LETTER_TO_DIGIT[c])
                    subs += 1
                else:
                    ok = False
                    break
        if not ok:
            continue
        cand = "".join(chars)
        if not is_valid(cand, codes):
            # state-code slot: try the other letters of the confusion group (e.g. 0->O vs D)
            if tpl.startswith("LL") and cand[:2] not in codes:
                fixed = _fix_state(cand, codes)
                if fixed is None:
                    continue
                subs += sum(1 for a, b in zip(cand[:2], fixed[:2]) if a != b)
                cand = fixed
            else:
                continue
        c = Correction(cand, subs, True)
        if best is None or c.substitutions < best.substitutions:
            best = c
    return best or Correction(plate, 0, is_valid(plate, codes))


def _fix_state(cand: str, codes: set[str]) -> str | None:
    for code in codes:
        if all(canonical(a) == canonical(b) for a, b in zip(cand[:2], code)):
            return code + cand[2:]
    return None


def levenshtein(a: str, b: str, limit: int | None = None) -> int:
    """Edit distance with optional early exit when it exceeds `limit`."""
    if a == b:
        return 0
    if limit is not None and abs(len(a) - len(b)) > limit:
        return limit + 1
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            cur.append(v)
            row_min = min(row_min, v)
        if limit is not None and row_min > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def fuzzy_distance(a: str, b: str, limit: int | None = None) -> int:
    """Distance after applying the confusion map to both sides."""
    return levenshtein(canonical(a), canonical(b), limit)


def rank_candidates(read: str, plates: Sequence[str], tolerance: int) -> list[tuple[str, int]]:
    """Plates within tolerance of `read`, closest first."""
    ca = canonical(read)
    out = []
    for p in plates:
        d = levenshtein(ca, canonical(p), tolerance)
        if d <= tolerance:
            out.append((p, d))
    out.sort(key=lambda x: (x[1], x[0]))
    return out


def mask(plate: str) -> str:
    """Partial masking for public pages: 'MH43AB1234' -> 'MH43••••34'."""
    if len(plate) <= 6:
        return plate[:2] + "•" * max(0, len(plate) - 2)
    return plate[:4] + "•" * (len(plate) - 6) + plate[-2:]
