#!/usr/bin/env python3
"""Run our plate models over any video (e.g. a phone recording at the gate) and list the plates.

No site configuration needed: every Nth frame is searched for plates, each plate is read, and reads
of the same plate are grouped. Writes a contact sheet of the plate crops so a person can check them.

    python tools/try_video.py gate_day.mp4                   # prints plates, writes gate_day_plates.jpg
    python tools/try_video.py gate_night.mov --every 3 --finder-width 960

This is a rough first look (no tracking or voting across cameras, which the real service adds).
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anpr_service.config import TrainedConfig  # noqa: E402
from anpr_service.plates import is_valid  # noqa: E402
from anpr_service.recognizers.trained import PlateFinder, PlateReader, resolve_model  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--every", type=int, default=2, help="look at every Nth frame (default 2)")
    ap.add_argument("--finder-width", type=int, default=960, help="frame width for the plate finder")
    ap.add_argument("--min-conf", type=float, default=0.5, help="ignore single reads below this confidence")
    ap.add_argument("--min-frames", type=int, default=3, help="a plate must be read this often to be listed")
    ap.add_argument("--all", action="store_true", help="also list stray reads (seen in fewer frames)")
    ap.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0, help="rotate frames (portrait videos)")
    a = ap.parse_args(argv)

    cfg = TrainedConfig()
    finder = PlateFinder(resolve_model(cfg.finder_model), a.finder_width, cfg.finder_threshold, threads=2)
    reader = PlateReader(resolve_model(cfg.reader_model), threads=2)
    rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(a.rotate)

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        print(f"cannot open {a.video}")
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    reads: dict[str, list[tuple[float, float, np.ndarray]]] = defaultdict(list)
    low = 0
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        i += 1
        if i % a.every:
            continue
        if rot is not None:
            frame = cv2.rotate(frame, rot)
        fh, fw = frame.shape[:2]
        for x1, y1, x2, y2, _s in finder.find(frame):
            px, py = 0.04 * (x2 - x1), 0.04 * (y2 - y1)
            X1, Y1 = max(0, int(x1 - px)), max(0, int(y1 - py))
            X2, Y2 = min(fw, int(x2 + px)), min(fh, int(y2 + py))
            if X2 - X1 < 12 or Y2 - Y1 < 6:
                continue
            crop = frame[Y1:Y2, X1:X2]
            res = reader.read(crop)
            if res is None:
                continue
            if res.confidence < a.min_conf:
                low += 1
                continue
            reads[res.text].append((i / fps, res.confidence, crop))
    cap.release()

    plates = sorted(reads.items(), key=lambda kv: kv[1][0][0])
    solid = [(t, rs) for t, rs in plates if len(rs) >= a.min_frames and is_valid(t)]
    stray = [(t, rs) for t, rs in plates if (t, rs) not in solid]
    print(f"{a.video}: {i} frames at {fps:.0f} fps\n")
    print(f"PLATES (read in at least {a.min_frames} frames, valid Indian format): {len(solid)}")
    print(f"{'time':>7}  {'plate':<12} {'frames':>6}  {'best conf':>9}")
    tiles = []
    for text, rs in (plates if a.all else solid):
        best = max(rs, key=lambda r: r[1])
        mark = "" if (text, rs) in solid else "   (stray)"
        print(f"{rs[0][0]:6.1f}s  {text:<12} {len(rs):>6}  {best[1]:9.2f}{mark}")
        if (text, rs) not in solid:
            continue
        tile = cv2.resize(best[2], (256, max(40, int(256 * best[2].shape[0] / max(1, best[2].shape[1])))))
        label = np.full((28, 256, 3), 255, np.uint8)
        cv2.putText(label, f"{text}  {best[1]:.2f}", (4, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
        tiles.append(np.vstack([label, tile]))
    print(f"\n{sum(len(rs) for _, rs in stray)} stray reads of {len(stray)} other texts not listed (--all shows them); "
          f"{low} reads below confidence {a.min_conf} ignored.")
    if tiles:
        h = max(t.shape[0] for t in tiles)
        tiles = [np.vstack([t, np.full((h - t.shape[0], 256, 3), 255, np.uint8)]) for t in tiles]
        rows = [np.hstack(tiles[k:k + 4] + [np.full((h, 256, 3), 255, np.uint8)] * (4 - len(tiles[k:k + 4])))
                for k in range(0, len(tiles), 4)]
        out = Path(a.video).with_name(Path(a.video).stem + "_plates.jpg")
        cv2.imwrite(str(out), np.vstack(rows))
        print(f"\ncontact sheet: {out}")
    print("Check each crop on the sheet against the plate you saw. The real service also tracks each bike and")
    print("votes over all its frames and both cameras, so it does better than this frame-by-frame look.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
