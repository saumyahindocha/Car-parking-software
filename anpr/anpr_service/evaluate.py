"""ANPR evaluation over labelled clips (spec Section 15).

A *label set* is a directory containing ``ground_truth.json`` and the clips
it references (the format written by ``python -m anpr_service synth``; the
same format is used for real recorded clips)::

    {
      "gate_id": "G1", "direction": "IN",
      "roi": [[x,y],...], "capture_line": [[x,y],[x,y]], "in_vector": [0,-1],
      "cameras": [{"id": "G1-L", "role": "ANPR", "file": "G1-L.mp4", "gate_span": [0,0.65]}, ...],
      "vehicles": [{"plate": "MH43AB1234", "cross_ms": 1694, "lateral": 0.2,
                    "visible_in": ["G1-L"], "side_by_side": true, "unreadable": false}, ...]
    }

``cross_ms`` is the capture-line crossing time relative to the start of the
clips; ``lateral`` (0 = left edge of the gate, 1 = right edge) is optional
for real clips (matching then uses time only).  Camera-level entries may
override ``roi`` / ``capture_line`` / ``in_vector``.

Reported per camera and per gate: exact-match accuracy, approximate-match
rate (confusion-aware, Levenshtein <= 1), read rate, and the same metrics on
the side-by-side subset; at gate level also missed vehicles, duplicate
events and false events.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .config import ServiceConfig
from .plates import approx_match, normalize
from .runner import InlineReplayRunner
from .synth import camera_entries
from .types import CameraRole, ReadStatus

log = logging.getLogger(__name__)

TIME_TOL_MS = 1200
LATERAL_TOL = 0.2


@dataclass
class _Obs:
    ts_offset: int
    lateral: float | None
    status: str
    plate: str | None


def find_label_sets(root: str | Path) -> list[Path]:
    root = Path(root)
    if (root / "ground_truth.json").exists():
        return [root]
    return sorted(p.parent for p in root.rglob("ground_truth.json"))


def config_for_label_set(gt: dict[str, Any], base_dir: Path, work_dir: Path,
                         base: ServiceConfig | None = None) -> ServiceConfig:
    data = base.model_dump(mode="json") if base is not None else {}
    data["image_root"] = str(work_dir / "images")
    data["outbox_path"] = str(work_dir / "outbox.sqlite")
    data["backend"] = {"url": ""}
    data["gates"] = [{
        "id": gt["gate_id"],
        "name": gt.get("gate_name", gt["gate_id"]),
        "direction": gt.get("direction", "BOTH"),
        "cameras": camera_entries(gt, base_dir),
    }]
    return ServiceConfig.model_validate(data)


def _match(gts: Sequence[dict[str, Any]], obs: Sequence[_Obs]) -> dict[int, int]:
    """Greedy one-to-one matching of GT vehicles to observations by time (+ lateral)."""
    pairs: list[tuple[float, int, int]] = []
    for gi, g in enumerate(gts):
        for oi, o in enumerate(obs):
            dt = abs(o.ts_offset - int(g["cross_ms"]))
            if dt > TIME_TOL_MS:
                continue
            cost = dt / TIME_TOL_MS
            if g.get("lateral") is not None and o.lateral is not None:
                dx = abs(o.lateral - float(g["lateral"]))
                if dx > LATERAL_TOL:
                    continue
                cost += dx / LATERAL_TOL
            # Prefer an observation carrying the right plate when timing is ambiguous.
            if o.plate and normalize(o.plate) == normalize(g["plate"]):
                cost -= 0.5
            pairs.append((cost, gi, oi))
    pairs.sort()
    used_g: set[int] = set()
    used_o: set[int] = set()
    out: dict[int, int] = {}
    for _c, gi, oi in pairs:
        if gi in used_g or oi in used_o:
            continue
        used_g.add(gi)
        used_o.add(oi)
        out[gi] = oi
    return out


def _metrics(gts: Sequence[dict[str, Any]], obs: Sequence[_Obs]) -> dict[str, Any]:
    matches = _match(gts, obs)
    readable = [i for i, g in enumerate(gts) if not g.get("unreadable")]
    n = len(readable)
    read = exact = approx = 0
    errors: list[dict[str, Any]] = []
    for gi in readable:
        g = gts[gi]
        oi = matches.get(gi)
        o = obs[oi] if oi is not None else None
        if o is not None and o.status == ReadStatus.READ.value and o.plate:
            read += 1
            if normalize(o.plate) == normalize(g["plate"]):
                exact += 1
            else:
                errors.append({"truth": g["plate"], "read": o.plate})
            if approx_match(o.plate, g["plate"], 1):
                approx += 1
        else:
            errors.append({"truth": g["plate"], "read": None if o is None else o.status})
    unreadable = [i for i, g in enumerate(gts) if g.get("unreadable")]
    unread_ok = sum(
        1 for gi in unreadable if gi in matches and obs[matches[gi]].status == ReadStatus.UNREAD.value
    )
    return {
        "vehicles": n,
        "read_rate": round(read / n, 4) if n else None,
        "exact_accuracy": round(exact / n, 4) if n else None,
        "approx_match_rate": round(approx / n, 4) if n else None,
        "exact_of_read": round(exact / read, 4) if read else None,
        "unreadable_vehicles": len(unreadable),
        "unreadable_emitted_unread": unread_ok,
        "detected": len(matches),
        "unmatched_observations": len(obs) - len(matches),
        "errors": errors,
    }


def evaluate_label_set(path: Path, base: ServiceConfig | None = None) -> dict[str, Any]:
    gt = json.loads((path / "ground_truth.json").read_text(encoding="utf-8"))
    vehicles = gt["vehicles"]
    with tempfile.TemporaryDirectory(prefix="anpr-eval-") as tmp:
        cfg = config_for_label_set(gt, path, Path(tmp), base)
        start = 1_700_000_000_000
        result = InlineReplayRunner(cfg, start_epoch_ms=start).run()
    report: dict[str, Any] = {"label_set": str(path), "gate_id": gt["gate_id"], "cameras": {}, "wall_s": round(result.wall_s, 2)}
    for cam in cfg.gate(gt["gate_id"]).cameras:
        if cam.role != CameraRole.ANPR:
            continue
        cam_gts = [v for v in vehicles if cam.id in v.get("visible_in", [cam.id])]
        cam_obs = [
            _Obs(r.ts_ms - start, r.lateral, r.status.value, r.plate) for r in result.reads if r.camera_id == cam.id
        ]
        m = _metrics(cam_gts, cam_obs)
        m["side_by_side"] = _metrics([v for v in cam_gts if v.get("side_by_side")], cam_obs)
        m["side_by_side"].pop("unmatched_observations")
        report["cameras"][cam.id] = m
    ev_obs = [
        _Obs(e["ts_ms"] - start, None, e["status"], e["plate"]) for e in result.events
    ]
    # Gate-level matching uses time and plate (events carry no lateral position).
    gate = _metrics(vehicles, ev_obs)
    gate["events"] = len(result.events)
    gate["extra_events"] = gate.pop("unmatched_observations")
    gate["side_by_side"] = _metrics([v for v in vehicles if v.get("side_by_side")], ev_obs)
    gate["side_by_side"].pop("unmatched_observations")
    gate["merged_cross_camera"] = result.aggregation.get(gt["gate_id"], {}).get("merged_cross_camera", 0)
    report["gate"] = gate
    report["timings"] = result.timings
    return report


def evaluate(labels_dir: str | Path, base: ServiceConfig | None = None) -> dict[str, Any]:
    sets = find_label_sets(labels_dir)
    if not sets:
        raise FileNotFoundError(f"no ground_truth.json under {labels_dir}")
    reports = [evaluate_label_set(p, base) for p in sets]
    # Micro-average the headline numbers across all sets.
    totals: dict[str, dict[str, float]] = {}
    for rep in reports:
        for cid, m in list(rep["cameras"].items()) + [("GATE:" + rep["gate_id"], rep["gate"])]:
            t = totals.setdefault(cid, {"vehicles": 0, "read": 0.0, "exact": 0.0, "approx": 0.0})
            n = m["vehicles"] or 0
            t["vehicles"] += n
            t["read"] += (m["read_rate"] or 0) * n
            t["exact"] += (m["exact_accuracy"] or 0) * n
            t["approx"] += (m["approx_match_rate"] or 0) * n
    summary = {
        k: {
            "vehicles": int(v["vehicles"]),
            "read_rate": round(v["read"] / v["vehicles"], 4) if v["vehicles"] else None,
            "exact_accuracy": round(v["exact"] / v["vehicles"], 4) if v["vehicles"] else None,
            "approx_match_rate": round(v["approx"] / v["vehicles"], 4) if v["vehicles"] else None,
        }
        for k, v in totals.items()
    }
    return {"summary": summary, "sets": reports}


def format_report(report: dict[str, Any]) -> str:
    lines = [f"{'camera/gate':<16}{'vehicles':>9}{'read rate':>11}{'exact':>9}{'approx':>9}"]
    for k, v in report["summary"].items():
        def pct(x: float | None) -> str:
            return "   -" if x is None else f"{100 * x:6.1f}%"
        lines.append(f"{k:<16}{v['vehicles']:>9}{pct(v['read_rate']):>11}{pct(v['exact_accuracy']):>9}"
                     f"{pct(v['approx_match_rate']):>9}")
    for rep in report["sets"]:
        g = rep["gate"]
        lines.append("")
        lines.append(f"[{rep['label_set']}] gate {rep['gate_id']}: events={g['events']} extra={g['extra_events']} "
                     f"merged_cross_camera={g['merged_cross_camera']} "
                     f"unreadable->UNREAD {g['unreadable_emitted_unread']}/{g['unreadable_vehicles']}")
        for cid, m in list(rep["cameras"].items()) + [("gate", g)]:
            sbs = m["side_by_side"]
            if sbs["vehicles"]:
                lines.append(f"  side-by-side {cid:<8} n={sbs['vehicles']} read={sbs['read_rate']} "
                             f"exact={sbs['exact_accuracy']} approx={sbs['approx_match_rate']}")
            for e in m["errors"]:
                lines.append(f"  miss {cid:<8} truth={e['truth']} got={e['read']}")
    return "\n".join(lines)
