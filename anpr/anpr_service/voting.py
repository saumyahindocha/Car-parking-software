"""Multi-frame plate voting for one vehicle track.

Every usable frame of a track contributes one OCR reading.  Readings are
first corrected to a valid Indian format where possible (position-aware
confusion correction), grouped by length, and then combined by
*character-position voting*: each position accumulates
``frame_weight x char_confidence`` per character and the heaviest character
wins.  Candidates valid under Indian formats are preferred; invalid ones are
penalised so that they only win when nothing valid is available.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .plates import DEFAULT_RULES, PlateRules, best_correction, is_valid, normalize
from .types import OcrResult, PlateCandidate

INVALID_PENALTY = 0.55
SUBSTITUTION_PENALTY = 0.85


@dataclass
class _Vote:
    text: str
    confs: list[float]
    weight: float
    substitutions: int
    valid: bool


@dataclass
class VoteResult:
    best: PlateCandidate | None
    candidates: list[PlateCandidate] = field(default_factory=list)
    n_votes: int = 0

    @property
    def confidence(self) -> float:
        return self.best.confidence if self.best else 0.0


class PlateVoter:
    def __init__(self, rules: PlateRules = DEFAULT_RULES, min_votes: int = 2, max_votes: int = 40) -> None:
        self.rules = rules
        self.min_votes = max(1, min_votes)
        self.max_votes = max_votes
        self._votes: list[_Vote] = []

    @property
    def n_votes(self) -> int:
        return len(self._votes)

    def add(self, ocr: OcrResult, weight: float = 1.0) -> None:
        raw = normalize(ocr.text)
        if len(raw) < 4 or weight <= 0:
            return
        confs = list(ocr.char_confs) if len(ocr.char_confs) == len(raw) else [ocr.confidence] * len(raw)
        corr = best_correction(raw, self.rules)
        if corr is not None:
            text = corr.plate
            confs = [c if a == b else c * SUBSTITUTION_PENALTY for a, b, c in zip(raw, text, confs)]
            vote = _Vote(text, confs, float(weight), corr.substitutions, True)
        else:
            vote = _Vote(raw, confs, float(weight), 0, False)
        self._votes.append(vote)
        if len(self._votes) > self.max_votes:
            # Drop the weakest vote rather than the oldest: early (close-up)
            # frames of a rear-view track are usually the best ones.
            weakest = min(range(len(self._votes)), key=lambda i: self._votes[i].weight * _mean(self._votes[i].confs))
            del self._votes[weakest]

    def result(self, top_k: int = 3) -> VoteResult:
        if not self._votes:
            return VoteResult(best=None, candidates=[], n_votes=0)
        total_weight = sum(v.weight for v in self._votes)
        groups: dict[int, list[_Vote]] = defaultdict(list)
        for v in self._votes:
            groups[len(v.text)].append(v)

        scored: dict[str, float] = {}

        def offer(text: str, score: float) -> None:
            text = normalize(text)
            if not text:
                return
            if not is_valid(text, self.rules):
                corr = best_correction(text, self.rules)
                if corr is not None:
                    text = corr.plate
                    score *= SUBSTITUTION_PENALTY**corr.substitutions
                else:
                    score *= INVALID_PENALTY
            score = max(0.0, min(1.0, score))
            if score > scored.get(text, -1.0):
                scored[text] = score

        for length, votes in groups.items():
            group_weight = sum(v.weight for v in votes)
            share = group_weight / total_weight if total_weight > 0 else 0.0
            support = min(1.0, len(votes) / self.min_votes) ** 0.5
            tallies: list[dict[str, float]] = [defaultdict(float) for _ in range(length)]
            for v in votes:
                for i, (ch, c) in enumerate(zip(v.text, v.confs)):
                    tallies[i][ch] += v.weight * max(c, 1e-3)
            consensus_chars: list[str] = []
            pos_scores: list[float] = []
            runner_ups: list[tuple[float, int, str, float]] = []
            for i, tally in enumerate(tallies):
                ranked = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)
                ch, w = ranked[0]
                consensus_chars.append(ch)
                pos_scores.append(w / group_weight if group_weight > 0 else 0.0)
                if len(ranked) > 1:
                    ch2, w2 = ranked[1]
                    runner_ups.append((w2 / w if w > 0 else 0.0, i, ch2, w2))
            base = (sum(pos_scores) / len(pos_scores)) if pos_scores else 0.0
            consensus_score = base * (share**0.5) * support
            consensus = "".join(consensus_chars)
            offer(consensus, consensus_score)
            # Alternates: swap the least certain positions to their runner-up.
            for rel, i, ch2, _w2 in sorted(runner_ups, reverse=True)[:3]:
                alt = consensus[:i] + ch2 + consensus[i + 1 :]
                offer(alt, consensus_score * rel)
            # Whole-string tallies give realistic alternatives too.
            string_weight: dict[str, float] = defaultdict(float)
            string_conf: dict[str, list[float]] = defaultdict(list)
            for v in votes:
                string_weight[v.text] += v.weight
                string_conf[v.text].append(_mean(v.confs))
            for text, w in string_weight.items():
                offer(text, (w / total_weight) * _mean(string_conf[text]) * support)

        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        candidates = [PlateCandidate(plate=t, confidence=s) for t, s in ranked]
        return VoteResult(best=candidates[0] if candidates else None, candidates=candidates, n_votes=len(self._votes))


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0
