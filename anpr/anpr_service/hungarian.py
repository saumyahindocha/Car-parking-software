"""Minimum-cost rectangular assignment (Hungarian / Kuhn-Munkres).

Uses SciPy's ``linear_sum_assignment`` when available, otherwise a compact
O(n^2 m) shortest-augmenting-path implementation.  Written from the textbook
algorithm; no third-party code is vendored.
"""

from __future__ import annotations

import numpy as np

try:  # pragma: no cover - optional dependency
    from scipy.optimize import linear_sum_assignment as _scipy_lsa
except Exception:  # noqa: BLE001
    _scipy_lsa = None


def _hungarian_rows_le_cols(cost: np.ndarray) -> list[tuple[int, int]]:
    n, m = cost.shape  # n <= m
    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)  # p[j]: row (1-based) assigned to column j
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = 0
            row = cost[i0 - 1]
            for j in range(1, m + 1):
                if not used[j]:
                    cur = row[j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    return [(p[j] - 1, j - 1) for j in range(1, m + 1) if p[j] != 0]


def linear_assignment(cost: np.ndarray) -> list[tuple[int, int]]:
    """Return (row, col) pairs minimising total cost; every row or every column is matched."""
    cost = np.asarray(cost, dtype=np.float64)
    if cost.size == 0:
        return []
    if _scipy_lsa is not None:
        rows, cols = _scipy_lsa(cost)
        return sorted(zip(rows.tolist(), cols.tolist()))
    if cost.shape[0] <= cost.shape[1]:
        return sorted(_hungarian_rows_le_cols(cost))
    pairs = _hungarian_rows_le_cols(cost.T)
    return sorted((r, c) for c, r in pairs)
