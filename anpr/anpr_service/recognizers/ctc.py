"""CTC greedy decoding for CRNN-style OCR models."""

from __future__ import annotations

import numpy as np


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def to_time_major(logits: np.ndarray) -> np.ndarray:
    """Normalise OCR output to (T, C) for batch size 1: accepts (T,1,C), (1,T,C) or (T,C)."""
    a = np.asarray(logits, dtype=np.float32)
    if a.ndim == 3:
        if a.shape[1] == 1:
            a = a[:, 0, :]
        elif a.shape[0] == 1:
            a = a[0]
        else:
            raise ValueError(f"expected batch size 1, got shape {logits.shape}")
    if a.ndim != 2:
        raise ValueError(f"unexpected OCR output shape {logits.shape}")
    return a


def ctc_greedy_decode(logits: np.ndarray, alphabet: str, blank: int = 0) -> tuple[str, list[float]]:
    """Best-path decode.  ``alphabet`` excludes the blank; class ``k`` maps to
    ``alphabet[k-1]`` when ``blank == 0`` and to ``alphabet[k]`` otherwise.

    Returns (text, per-character confidence = max probability over the frames
    that produced the character).
    """
    a = to_time_major(logits)
    probs = a if (a.min() >= 0 and np.allclose(a.sum(axis=1), 1.0, atol=1e-3)) else _softmax(a)
    best = probs.argmax(axis=1)
    text: list[str] = []
    confs: list[float] = []
    prev = -1
    for t, k in enumerate(best.tolist()):
        p = float(probs[t, k])
        if k == blank:
            prev = k
            continue
        if k == prev:
            confs[-1] = max(confs[-1], p)
            continue
        idx = k - 1 if blank == 0 else k
        if 0 <= idx < len(alphabet):
            text.append(alphabet[idx])
            confs.append(p)
        prev = k
    return "".join(text), confs
