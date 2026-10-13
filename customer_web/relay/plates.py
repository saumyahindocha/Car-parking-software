"""Plate helpers mirroring the edge (backend/app/domain/plates.py): normalise, mask, fuzzy match."""
from __future__ import annotations

import re
from typing import Iterable, Optional

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
CONFUSION_GROUPS = ("0ODQ", "1IL", "2Z", "5S", "8B", "6G")
_CANON = {c: g[0] for g in CONFUSION_GROUPS for c in g}
_PLATE_OK = re.compile(r"^[A-Z0-9]{4,12}$")


def normalise(raw: Optional[str]) -> str:
    """'mh 12-ab 1234' -> 'MH12AB1234'."""
    return _NON_ALNUM.sub("", (raw or "").upper())


def plausible(plate: str) -> bool:
    return bool(_PLATE_OK.match(plate))


def canonical(plate: str) -> str:
    return "".join(_CANON.get(c, c) for c in plate)


def mask(plate: str) -> str:
    """Same as the edge: 'MH12AB1234' -> 'MH12••••34'."""
    if len(plate) <= 6:
        return plate[:2] + "•" * max(0, len(plate) - 2)
    return plate[:4] + "•" * (len(plate) - 6) + plate[-2:]


def display(plate: str) -> str:
    m = re.match(r"^([0-9]{2})(BH)([0-9]{4})([A-Z]{1,2})$", plate)
    if m:
        return " ".join(m.groups())
    m = re.match(r"^([A-Z]{2})([0-9]{1,2})([A-Z]{0,3})([0-9]{4})$", plate)
    if m:
        return " ".join(g for g in m.groups() if g)
    return plate


def levenshtein(a: str, b: str, limit: Optional[int] = None) -> int:
    if a == b:
        return 0
    if abs(len(a) - len(b)) > (limit if limit is not None else 99):
        return (limit or 0) + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if limit is not None and min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def approx_matches(typed: str, plates: Iterable[str], max_distance: int = 1) -> list[tuple[str, int]]:
    """Plates within `max_distance` edits, or equal after collapsing OCR-confusable characters."""
    out = []
    ct = canonical(typed)
    for p in plates:
        if p == typed:
            continue
        d = levenshtein(typed, p, max_distance)
        if canonical(p) == ct:
            d = min(d, 1)
        if d <= max_distance:
            out.append((p, d))
    return sorted(out, key=lambda x: (x[1], x[0]))
