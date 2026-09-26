#!/usr/bin/env python3
"""Train a CRNN + CTC plate OCR model for Indian plates (single- and two-line) and export ONNX.

The model reads ONE text row.  Two-line plates are split into rows with the
same horizontal-projection splitter the service uses at inference
(``anpr_service.rows``), and each row gets its label from
``anpr_service.plates.split_rows`` (``MH43`` / ``AB1234``; BH: ``22BH`` /
``1234AA``).  At inference ``LocalOnnxRecognizer`` reads the rows top to
bottom and concatenates them, so training and inference see identical inputs.

Data:
  * real crops labelled with ``tools/label_crops.py`` (``--labels``, ``--crops``)
  * optional synthetic plates rendered on the fly (``--synthetic N``) with
    random Indian registrations, both fonts, both layouts and augmentation
    (blur, noise, perspective, brightness, JPEG) - useful for pre-training;
    always fine-tune on real crops from the site's cameras (day + night).

Output: ``<out>.onnx`` (input ``x`` float32 [1,1,32,128] normalised as
(pixel/255 - 0.5)/0.5; output ``logits`` [T,1,37] time-major, blank = 0,
alphabet ``0-9A-Z``) and ``<out>.alphabet.json``.  These match the defaults of
``recognizer.onnx`` in the service config.

PyTorch (BSD-3-Clause) is only needed for this script:
    pip install torch --index-url https://download.pytorch.org/whl/cu121   # or /cpu

Example:
    python tools/train_ocr.py --labels crops/labels.csv --crops crops --synthetic 50000 \\
        --epochs 40 --out models/plate_ocr_crnn
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anpr_service.plates import DEFAULT_STATE_CODES, normalize, split_rows  # noqa: E402
from anpr_service.rows import detect_rows  # noqa: E402
from anpr_service.synth import render_plate  # noqa: E402

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch.utils.data import DataLoader, Dataset
except ImportError:  # pragma: no cover - torch is optional for the service
    print("PyTorch is required for training: pip install torch", file=sys.stderr)
    raise

log = logging.getLogger("train_ocr")

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CHAR2IDX = {c: i + 1 for i, c in enumerate(ALPHABET)}  # 0 = CTC blank
IMG_H, IMG_W = 32, 128
SERIES_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # I and O are not issued in series


# ------------------------------------------------------------------ data
def random_plate(rng: random.Random) -> str:
    if rng.random() < 0.08:
        return (f"{rng.randint(21, 26):02d}BH{rng.randint(0, 9999):04d}"
                + "".join(rng.choice(SERIES_LETTERS) for _ in range(rng.choice([1, 2]))))
    state = rng.choice(DEFAULT_STATE_CODES)
    district = str(rng.randint(1, 99)) if rng.random() < 0.85 else f"{rng.randint(1, 9):02d}"
    series = "".join(rng.choice(SERIES_LETTERS) for _ in range(rng.choice([0, 1, 2, 2, 2, 3])))
    return f"{state}{district}{series}{rng.randint(0, 9999):04d}"


def augment(img: np.ndarray, rng: random.Random) -> np.ndarray:
    h, w = img.shape[:2]
    # perspective jitter
    d = 0.06
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[x + rng.uniform(-d, d) * w, y + rng.uniform(-d, d) * h] for x, y in src])
    img = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w, h), borderMode=cv2.BORDER_REPLICATE)
    # downscale to a realistic camera size, then back
    scale = rng.uniform(0.25, 0.8)
    small = cv2.resize(img, (max(8, int(w * scale)), max(4, int(h * scale))), interpolation=cv2.INTER_AREA)
    if rng.random() < 0.5:
        k = rng.choice([3, 5])
        small = cv2.GaussianBlur(small, (k, 1) if rng.random() < 0.5 else (1, k), 0)  # motion blur
    small = cv2.convertScaleAbs(small, alpha=rng.uniform(0.6, 1.3), beta=rng.uniform(-40, 30))
    noise = np.random.default_rng(rng.randint(0, 2**31)).normal(0, rng.uniform(0, 8), small.shape)
    small = np.clip(small.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(40, 95)])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else small


def rows_with_labels(img: np.ndarray, plate: str, layout: str) -> list[tuple[np.ndarray, str]]:
    """Split a plate crop into (row image, row label) pairs; [] if the split is unreliable."""
    plate = normalize(plate)
    if layout == "two_line":
        layout_rows = detect_rows(img).rows
        if len(layout_rows) != 2:
            return []
        r1, r2 = split_rows(plate)
        return [(img[y0:y1], lab) for (y0, y1), lab in zip(layout_rows, (r1, r2))]
    (y0, y1), = detect_rows(img, max_rows=1).rows
    return [(img[y0:y1], plate)]


def to_tensor(row: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(row, cv2.COLOR_BGR2GRAY) if row.ndim == 3 else row
    h, w = gray.shape[:2]
    new_w = min(IMG_W, max(1, int(round(w * IMG_H / float(h)))))
    gray = cv2.resize(gray, (new_w, IMG_H), interpolation=cv2.INTER_CUBIC)
    canvas = np.full((IMG_H, IMG_W), 255, dtype=np.uint8)
    canvas[:, :new_w] = gray
    return ((canvas.astype(np.float32) / 255.0 - 0.5) / 0.5)[None]


@dataclass
class Sample:
    image: np.ndarray  # (1, H, W) float32
    label: str
    plate_id: int  # samples of the same plate share an id (for plate-level accuracy)


def load_real(labels_csv: Path, crops_dir: Path, start_id: int) -> list[Sample]:
    out: list[Sample] = []
    with open(labels_csv, newline="", encoding="utf-8") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            plate = (row.get("plate") or "").strip()
            if plate in ("", "-"):
                continue
            img = cv2.imread(str(crops_dir / row["filename"]))
            if img is None:
                continue
            for r, lab in rows_with_labels(img, plate, row.get("layout") or "single"):
                if r.size and lab:
                    out.append(Sample(to_tensor(r), lab, start_id + i))
    return out


def make_synthetic(n: int, seed: int, start_id: int) -> list[Sample]:
    rng = random.Random(seed)
    fonts = [cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_DUPLEX, cv2.FONT_HERSHEY_TRIPLEX]
    out: list[Sample] = []
    for i in range(n):
        plate = random_plate(rng)
        layout = "two_line" if rng.random() < 0.6 else "single"
        bg = (236, 236, 236) if rng.random() < 0.85 else (40, 200, 230)  # white or yellow
        img = augment(render_plate(plate, layout, rng.choice(fonts), background=bg), rng)
        for r, lab in rows_with_labels(img, plate, layout):
            if r.size:
                out.append(Sample(to_tensor(r), lab, start_id + i))
    return out


class RowDataset(Dataset):  # type: ignore[misc]
    def __init__(self, samples: list[Sample]) -> None:
        self.samples = samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, i: int) -> tuple[torch.Tensor, torch.Tensor, int]:
        s = self.samples[i]
        target = torch.tensor([CHAR2IDX[c] for c in s.label if c in CHAR2IDX], dtype=torch.long)
        return torch.from_numpy(s.image), target, s.plate_id


def collate(batch: list[tuple[torch.Tensor, torch.Tensor, int]]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, list[int]]:
    images = torch.stack([b[0] for b in batch])
    targets = torch.cat([b[1] for b in batch])
    lengths = torch.tensor([len(b[1]) for b in batch], dtype=torch.long)
    return images, targets, lengths, [b[2] for b in batch]


# ----------------------------------------------------------------- model
class CRNN(nn.Module):  # type: ignore[misc]
    """VGG-style CNN -> 2-layer BiLSTM -> per-column classifier (CTC)."""

    def __init__(self, n_classes: int = len(ALPHABET) + 1) -> None:
        super().__init__()

        def block(cin: int, cout: int) -> list[nn.Module]:
            return [nn.Conv2d(cin, cout, 3, 1, 1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True)]

        self.cnn = nn.Sequential(
            *block(1, 64), nn.MaxPool2d(2, 2),                  # 16 x 64
            *block(64, 128), nn.MaxPool2d(2, 2),                # 8 x 32
            *block(128, 256), *block(256, 256), nn.MaxPool2d((2, 1), (2, 1)),  # 4 x 32
            *block(256, 384), *block(384, 384), nn.MaxPool2d((2, 1), (2, 1)),  # 2 x 32
            *block(384, 384),
        )
        self.rnn = nn.LSTM(384 * 2, 192, num_layers=2, bidirectional=True, dropout=0.2)
        self.fc = nn.Linear(384, n_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        f = self.cnn(x)  # (B, C, 2, T)
        b, c, h, t = f.shape
        f = f.permute(3, 0, 1, 2).reshape(t, b, c * h)  # (T, B, C*H)
        out, _ = self.rnn(f)
        return self.fc(out)  # (T, B, classes) logits


def greedy(logits: torch.Tensor) -> list[str]:
    best = logits.argmax(dim=2).transpose(0, 1).cpu().numpy()  # (B, T)
    out = []
    for seq in best:
        prev, chars = 0, []
        for k in seq:
            if k != prev and k != 0:
                chars.append(ALPHABET[k - 1])
            prev = k
        out.append("".join(chars))
    return out


def evaluate(model: nn.Module, loader: DataLoader, device: torch.device) -> tuple[float, float]:
    model.eval()
    row_ok = row_n = 0
    plate_ok: dict[int, bool] = {}
    with torch.no_grad():
        for images, targets, lengths, ids in loader:
            preds = greedy(model(images.to(device)))
            offs = 0
            for p, n, pid in zip(preds, lengths.tolist(), ids):
                truth = "".join(ALPHABET[k - 1] for k in targets[offs : offs + n].tolist())
                offs += n
                ok = p == truth
                row_ok += ok
                row_n += 1
                plate_ok[pid] = plate_ok.get(pid, True) and ok
    return row_ok / max(row_n, 1), sum(plate_ok.values()) / max(len(plate_ok), 1)


def export_onnx(model: nn.Module, out: Path) -> None:
    model.eval().cpu()
    dummy = torch.zeros(1, 1, IMG_H, IMG_W)
    kwargs = {"input_names": ["x"], "output_names": ["logits"], "opset_version": 17}
    try:  # torch >= 2.5: use the TorchScript exporter (no onnxscript dependency)
        torch.onnx.export(model, dummy, str(out.with_suffix(".onnx")), dynamo=False, **kwargs)
    except TypeError:  # older torch without the ``dynamo`` argument
        torch.onnx.export(model, dummy, str(out.with_suffix(".onnx")), **kwargs)
    out.with_suffix(".alphabet.json").write_text(json.dumps({"alphabet": ALPHABET, "blank": 0,
                                                             "input": [1, 1, IMG_H, IMG_W],
                                                             "normalise": {"mean": 0.5, "std": 0.5}}))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, help="labels.csv from tools/label_crops.py")
    ap.add_argument("--crops", type=Path, help="folder with the labelled crops")
    ap.add_argument("--synthetic", type=int, default=0, help="number of synthetic plates to add")
    ap.add_argument("--val-fraction", type=float, default=0.1)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=Path("plate_ocr_crnn"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    torch.manual_seed(args.seed)
    random.seed(args.seed)

    samples: list[Sample] = []
    if args.labels:
        samples += load_real(args.labels, args.crops or args.labels.parent, 0)
        log.info("real rows: %d", len(samples))
    if args.synthetic:
        syn = make_synthetic(args.synthetic, args.seed, 10_000_000)
        log.info("synthetic rows: %d", len(syn))
        samples += syn
    if not samples:
        ap.error("no training data: pass --labels/--crops and/or --synthetic N")

    # Split by plate so the rows of one plate never straddle train/val.
    ids = sorted({s.plate_id for s in samples})
    random.Random(args.seed).shuffle(ids)
    val_ids = set(ids[: max(1, int(len(ids) * args.val_fraction))])
    train = RowDataset([s for s in samples if s.plate_id not in val_ids])
    val = RowDataset([s for s in samples if s.plate_id in val_ids])
    tl = DataLoader(train, batch_size=args.batch, shuffle=True, collate_fn=collate, num_workers=2, drop_last=True)
    vl = DataLoader(val, batch_size=args.batch, shuffle=False, collate_fn=collate)

    device = torch.device(args.device)
    model = CRNN().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=max(1, args.epochs * len(tl)))
    ctc = nn.CTCLoss(blank=0, zero_infinity=True)
    best = -1.0
    for epoch in range(1, args.epochs + 1):
        model.train()
        total = 0.0
        for images, targets, lengths, _ids in tl:
            images = images.to(device)
            logits = model(images)
            log_probs = F.log_softmax(logits, dim=2)
            input_lengths = torch.full((images.size(0),), logits.size(0), dtype=torch.long)
            loss = ctc(log_probs, targets, input_lengths, lengths)
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            sched.step()
            total += float(loss)
        row_acc, plate_acc = evaluate(model, vl, device)
        log.info("epoch %d loss %.4f val row-acc %.4f plate-acc %.4f", epoch, total / max(1, len(tl)), row_acc,
                 plate_acc)
        if plate_acc > best:
            best = plate_acc
            args.out.parent.mkdir(parents=True, exist_ok=True)
            torch.save(model.state_dict(), args.out.with_suffix(".pt"))
    model.load_state_dict(torch.load(args.out.with_suffix(".pt"), map_location="cpu"))
    export_onnx(model, args.out)
    log.info("best val plate accuracy %.4f; exported %s", best, args.out.with_suffix(".onnx"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
