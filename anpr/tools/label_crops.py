#!/usr/bin/env python3
"""Label a folder of plate crops for OCR fine-tuning.

Writes ``labels.csv`` with columns ``filename,plate,layout,valid`` where
``layout`` is ``single`` or ``two_line`` and ``valid`` says whether the plate
matches an Indian format (standard or BH).  Existing labels are kept and can
be edited; the file is rewritten atomically after every change.

Interactive (needs a GUI build of OpenCV, i.e. ``pip install opencv-python``)::

    python tools/label_crops.py /data/crops --labels /data/crops/labels.csv --suggest

    type characters      A-Z / 0-9 (lower case is fine)
    Backspace            delete last character
    Tab                  toggle layout single <-> two_line
    Enter                save label and go to the next crop
    ]  /  [              skip forward / go back without saving
    Delete               mark crop as unusable (plate = "-"), next
    Esc                  save and quit

Headless (servers, CI, bulk import)::

    python tools/label_crops.py /data/crops --headless --prefill reviewed.csv
    python tools/label_crops.py /data/crops --headless --suggest      # machine pre-labels for review

``--prefill`` takes a CSV with ``filename,plate[,layout]``; plates are
normalised, layouts are detected from the crop when missing, and invalid or
missing files are reported.  ``--suggest`` pre-fills unlabeled crops with the
recogniser's reading (classical by default, or the engine in ``--config``);
suggestions are marked in the ``source`` column so they can be reviewed.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anpr_service.plates import is_valid, normalize  # noqa: E402
from anpr_service.rows import detect_rows  # noqa: E402

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}
FIELDS = ["filename", "plate", "layout", "valid", "source"]


@dataclass
class Label:
    filename: str
    plate: str
    layout: str
    source: str = "human"

    @property
    def valid(self) -> bool:
        return self.plate not in ("", "-") and is_valid(self.plate)


def list_crops(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT)


def detect_layout(path: Path) -> str:
    img = cv2.imread(str(path))
    if img is None:
        return "single"
    return "two_line" if detect_rows(img).n_rows == 2 else "single"


def load_labels(path: Path) -> dict[str, Label]:
    if not path.exists():
        return {}
    out: dict[str, Label] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("filename"):
                out[row["filename"]] = Label(row["filename"], (row.get("plate") or "").strip(),
                                             row.get("layout") or "single", row.get("source") or "human")
    return out


def save_labels(path: Path, labels: dict[str, Label]) -> None:
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for name in sorted(labels):
            lab = labels[name]
            w.writerow({"filename": lab.filename, "plate": lab.plate, "layout": lab.layout,
                        "valid": int(lab.valid), "source": lab.source})
    os.replace(tmp, path)


def suggest(folder: Path, names: list[str], labels: dict[str, Label], config: str | None) -> int:
    from anpr_service.config import ServiceConfig, load_config
    from anpr_service.recognizers import create_recognizer

    cfg = load_config(config) if config else ServiceConfig()
    rec = create_recognizer(cfg)
    n = 0
    for name in names:
        if name in labels:
            continue
        img = cv2.imread(str(folder / name))
        if img is None:
            continue
        res = rec.read_plate(img)
        if res is None:
            continue
        labels[name] = Label(name, normalize(res.text), "two_line" if res.rows == 2 else "single", "suggested")
        n += 1
    return n


def headless(folder: Path, labels_path: Path, prefill: Path | None, do_suggest: bool, config: str | None) -> int:
    names = list_crops(folder)
    labels = load_labels(labels_path)
    problems = 0
    if prefill is not None:
        with open(prefill, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("filename") or "").strip()
                if not name:
                    continue
                if name not in names:
                    print(f"missing crop: {name}", file=sys.stderr)
                    problems += 1
                    continue
                plate = "-" if (row.get("plate") or "").strip() == "-" else normalize(row.get("plate"))
                layout = (row.get("layout") or "").strip() or detect_layout(folder / name)
                if layout not in ("single", "two_line"):
                    print(f"bad layout for {name}: {layout}", file=sys.stderr)
                    problems += 1
                    continue
                labels[name] = Label(name, plate, layout, "human")
                if plate != "-" and not is_valid(plate):
                    print(f"not a valid Indian plate (kept, flagged): {name} -> {plate}", file=sys.stderr)
    if do_suggest:
        print(f"suggested {suggest(folder, names, labels, config)} labels")
    save_labels(labels_path, labels)
    done = sum(1 for n in names if n in labels)
    print(f"{done}/{len(names)} crops labelled -> {labels_path} ({problems} problems)")
    return 1 if problems else 0


def interactive(folder: Path, labels_path: Path, do_suggest: bool, config: str | None) -> int:
    names = list_crops(folder)
    if not names:
        print("no crops found")
        return 1
    labels = load_labels(labels_path)
    if do_suggest:
        suggest(folder, names, labels, config)
    try:
        cv2.namedWindow("label", cv2.WINDOW_NORMAL)
    except cv2.error:
        print("This OpenCV build has no GUI (opencv-python-headless). Install opencv-python, "
              "or use --headless --prefill.", file=sys.stderr)
        return 2
    idx = next((i for i, n in enumerate(names) if n not in labels or labels[n].source != "human"), 0)
    while 0 <= idx < len(names):
        name = names[idx]
        cur = labels.get(name)
        text = cur.plate if cur else ""
        layout = cur.layout if cur else detect_layout(folder / name)
        img = cv2.imread(str(folder / name))
        if img is None:
            idx += 1
            continue
        scale = max(1.0, 480.0 / img.shape[1])
        big = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        while True:
            canvas = np.full((big.shape[0] + 90, max(big.shape[1], 640), 3), 30, dtype=np.uint8)
            canvas[: big.shape[0], : big.shape[1]] = big
            ok = is_valid(text)
            colour = (80, 220, 80) if ok else (60, 60, 230)
            cv2.putText(canvas, f"{idx + 1}/{len(names)} {name}", (10, big.shape[0] + 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.putText(canvas, f"{text or '_'}   [{layout}]", (10, big.shape[0] + 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, colour, 2, cv2.LINE_AA)
            cv2.imshow("label", canvas)
            key = cv2.waitKeyEx(0)
            k = key & 0xFF
            if k == 27:  # Esc
                save_labels(labels_path, labels)
                cv2.destroyAllWindows()
                return 0
            if k in (13, 10):  # Enter
                if text:
                    labels[name] = Label(name, text, layout, "human")
                    save_labels(labels_path, labels)
                idx += 1
                break
            if k == 9:  # Tab
                layout = "two_line" if layout == "single" else "single"
            elif k == 8:  # Backspace
                text = text[:-1]
            elif key in (0xFF, 0x2E0000, 65535):  # Delete
                labels[name] = Label(name, "-", layout, "human")
                save_labels(labels_path, labels)
                idx += 1
                break
            elif k == ord("]"):
                idx += 1
                break
            elif k == ord("["):
                idx = max(0, idx - 1)
                break
            elif chr(k).isalnum():
                text = normalize(text + chr(k))
    save_labels(labels_path, labels)
    cv2.destroyAllWindows()
    print(f"labels saved to {labels_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("crops", type=Path, help="folder of plate crop images")
    ap.add_argument("--labels", type=Path, help="labels CSV (default: <crops>/labels.csv)")
    ap.add_argument("--headless", action="store_true", help="no GUI: merge --prefill and/or --suggest")
    ap.add_argument("--prefill", type=Path, help="CSV filename,plate[,layout] to import")
    ap.add_argument("--suggest", action="store_true", help="pre-label unlabeled crops with the recogniser")
    ap.add_argument("--config", help="service config selecting the recogniser for --suggest")
    args = ap.parse_args(argv)
    labels_path = args.labels or (args.crops / "labels.csv")
    if args.headless:
        return headless(args.crops, labels_path, args.prefill, args.suggest, args.config)
    return interactive(args.crops, labels_path, args.suggest, args.config)


if __name__ == "__main__":
    sys.exit(main())
