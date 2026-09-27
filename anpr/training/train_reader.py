#!/usr/bin/env python3
"""Train the plate READER (whole-plate OCR, single- and two-line) on synthetic plates; export ONNX.

The network sees a 64x160 grayscale crop and predicts MAX_LEN character slots (top row first, then
bottom row), each one of 36 characters or "empty". No row splitting is needed.

    python -m training.train_reader gen    --out training/data --train 240000 --val 6000
    python -m training.train_reader train  --data training/data --epochs 14 --out training/runs/reader
    python -m training.train_reader export --ckpt training/runs/reader/best.pt --out models/plate_reader.onnx
    python -m training.train_reader eval   --model models/plate_reader.onnx --data training/data

Fine-tuning on real crops from the site later (labelled with tools/label_crops.py):
    python -m training.train_reader train --data training/data --real crops/labels.csv --real-dir crops \\
        --init training/runs/reader/best.pt --epochs 6 --lr 5e-4 --out training/runs/reader_site

Needs PyTorch (BSD-3-Clause) for training only; the service runs the ONNX file with onnxruntime.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import multiprocessing as mp
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training import platesynth as ps  # noqa: E402

N_CLASSES = len(ps.ALPHABET) + 1  # + empty slot


# ----------------------------------------------------------------------------- data generation
def _gen_chunk(args: tuple[int, int, str]) -> str:
    seed, n, path = args
    rng = random.Random(seed)
    np.random.seed(seed % (2 ** 32))
    xs = np.empty((n, ps.OCR_H, ps.OCR_W), np.uint8)
    ys = np.empty((n, ps.MAX_LEN), np.uint8)
    night = np.empty(n, np.uint8)
    rows = np.empty(n, np.uint8)
    for i in range(n):
        x, text, meta = ps.ocr_sample(rng)
        xs[i] = x
        ys[i] = ps.encode(text)
        night[i] = meta["night"]
        rows[i] = meta["rows"]
    np.savez(path, x=xs, y=ys, night=night, rows=rows)
    return path


def cmd_gen(a: argparse.Namespace) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    chunk = 4000
    jobs = []
    for split, total, base in (("train", a.train, 1_000_000), ("val", a.val, 9_000_000)):
        for k in range(math.ceil(total / chunk)):
            n = min(chunk, total - k * chunk)
            jobs.append((base + a.seed * 10_000 + k, n, str(out / f"{split}_{k:04d}.npz")))
    t = time.time()
    with mp.Pool(a.workers) as pool:
        for i, p in enumerate(pool.imap_unordered(_gen_chunk, jobs), 1):
            print(f"[{i}/{len(jobs)}] {p}  ({time.time() - t:.0f}s)", flush=True)


def load_split(data: str, split: str) -> dict[str, np.ndarray]:
    files = sorted(Path(data).glob(f"{split}_*.npz"))
    if not files:
        raise SystemExit(f"no {split} data in {data}")
    parts = [np.load(f) for f in files]
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0].files}


def load_real(csv_path: str, root: str) -> tuple[np.ndarray, np.ndarray]:
    """Real crops: CSV with columns filename,plate (tools/label_crops.py output; plate "-" = unusable)."""
    import cv2

    xs, ys = [], []
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            text = "".join(c for c in (r.get("plate") or r.get("text") or "").upper() if c.isalnum())
            if not text or len(text) > ps.MAX_LEN:
                continue
            img = cv2.imread(os.path.join(root, r.get("filename") or r.get("path") or ""), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            xs.append(cv2.resize(img, (ps.OCR_W, ps.OCR_H), interpolation=cv2.INTER_LINEAR))
            ys.append(ps.encode(text))
    return np.array(xs, np.uint8), np.array(ys, np.uint8)


# ----------------------------------------------------------------------------- model
def build_model():
    """~1.2 M parameters; ~280 img/s training on 4 CPU cores, a few ms per plate at inference."""
    import torch.nn as nn

    def cbr(cin, cout, stride=1):
        return [nn.Conv2d(cin, cout, 3, stride, 1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True)]

    class PlateReader(nn.Module):
        def __init__(self):
            super().__init__()
            self.features = nn.Sequential(
                *cbr(1, 32, 2), *cbr(32, 48), nn.MaxPool2d(2),               # 16 x 32
                *cbr(48, 64), *cbr(64, 64), nn.MaxPool2d(2),                 # 8 x 16
                *cbr(64, 128), *cbr(128, 128), nn.MaxPool2d(2),              # 4 x 8
                nn.Conv2d(128, 64, 1, bias=False), nn.BatchNorm2d(64), nn.ReLU(inplace=True))
            self.head = nn.Sequential(nn.Flatten(), nn.Dropout(0.25), nn.Linear(64 * (ps.OCR_H // 16) * (ps.OCR_W // 16), 384),
                                      nn.ReLU(inplace=True), nn.Dropout(0.15), nn.Linear(384, ps.MAX_LEN * N_CLASSES))

        def forward(self, x):
            return self.head(self.features(x)).view(-1, ps.MAX_LEN, N_CLASSES)

    return PlateReader()


def standardize(x):
    """Per-image standardisation (robust to exposure). Same maths as the runtime (numpy) version."""
    import torch

    x = x.float()
    m = x.mean(dim=(1, 2, 3), keepdim=True)
    s = x.std(dim=(1, 2, 3), keepdim=True) + 8.0
    return (x - m) / s


class _Exportable:
    """Wraps the network with preprocessing and softmax so the ONNX file takes raw 0-255 pixels."""

    @staticmethod
    def make(net):
        import torch
        import torch.nn as nn

        class M(nn.Module):
            def __init__(self, n):
                super().__init__()
                self.n = n

            def forward(self, x):  # x: float32 [N,1,64,160] raw pixels 0..255
                m = x.mean(dim=(1, 2, 3), keepdim=True)
                s = torch.sqrt(((x - m) ** 2).sum(dim=(1, 2, 3), keepdim=True) / (x[0].numel() - 1)) + 8.0
                return torch.softmax(self.n((x - m) / s), dim=-1)

        return M(net)


# ----------------------------------------------------------------------------- training
def _augment(xb, rng: np.random.Generator):
    """Cheap per-batch augmentation on top of the synthetic degradations: contrast, noise, shifts."""
    import torch

    n = xb.shape[0]
    c = torch.from_numpy(rng.uniform(0.7, 1.3, (n, 1, 1, 1)).astype(np.float32))
    b = torch.from_numpy(rng.uniform(-25, 25, (n, 1, 1, 1)).astype(np.float32))
    xb = (xb - 128) * c + 128 + b
    xb = xb + torch.randn_like(xb) * float(rng.uniform(0, 6))
    dx, dy = int(rng.integers(-6, 7)), int(rng.integers(-3, 4))
    xb = torch.roll(xb, shifts=(dy, dx), dims=(2, 3))
    if rng.random() < 0.5:  # invert a few images: dark plates with light text (EV, rental)
        k = rng.random(n) < 0.08
        xb[k] = 255 - xb[k]
    return xb.clamp(0, 255)


def evaluate(net, x: np.ndarray, y: np.ndarray, bs: int = 512) -> dict:
    import torch

    net.eval()
    preds, confs = [], []
    with torch.no_grad():
        for i in range(0, len(x), bs):
            xb = standardize(torch.from_numpy(x[i:i + bs]).unsqueeze(1))
            p = torch.softmax(net(xb), -1)
            c, k = p.max(-1)
            preds.append(k.numpy())
            confs.append(c.numpy())
    preds, confs = np.concatenate(preds), np.concatenate(confs)
    exact = (preds == y).all(1)
    char = (preds == y).mean()
    plate_conf = confs.min(1)
    out = {"plate_acc": float(exact.mean()), "char_acc": float(char), "n": int(len(y))}
    for thr in (0.5, 0.7, 0.9):
        keep = plate_conf >= thr
        out[f"acc@conf>={thr}"] = float(exact[keep].mean()) if keep.any() else 0.0
        out[f"coverage@conf>={thr}"] = float(keep.mean())
    return out


def cmd_train(a: argparse.Namespace) -> None:
    import torch
    import torch.nn as nn

    torch.set_num_threads(a.threads)
    torch.manual_seed(a.seed)
    rng = np.random.default_rng(a.seed)
    tr, va = load_split(a.data, "train"), load_split(a.data, "val")
    x, y = tr["x"], tr["y"].astype(np.int64)
    if a.real:
        rx, ry = load_real(a.real, a.real_dir or os.path.dirname(a.real))
        reps = max(1, int(len(x) * a.real_weight / max(1, len(rx))))
        print(f"real crops: {len(rx)} (x{reps})")
        x = np.concatenate([x] + [rx] * reps)
        y = np.concatenate([y] + [ry.astype(np.int64)] * reps)
    net = build_model()
    if a.init:
        net.load_state_dict(torch.load(a.init, map_location="cpu")["model"])
    print(f"params: {sum(p.numel() for p in net.parameters()) / 1e6:.2f}M  train: {len(x)}  val: {len(va['y'])}")
    opt = torch.optim.AdamW(net.parameters(), lr=a.lr, weight_decay=1e-4)
    steps = a.epochs * math.ceil(len(x) / a.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.15)
    loss_fn = nn.CrossEntropyLoss(label_smoothing=0.05)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    best, log = -1.0, []
    t0 = time.time()
    step = 0
    for ep in range(a.epochs):
        net.train()
        order = rng.permutation(len(x))
        tot, nb = 0.0, 0
        for i in range(0, len(order), a.batch):
            idx = order[i:i + a.batch]
            xb = _augment(torch.from_numpy(x[idx]).unsqueeze(1).float(), rng)
            yb = torch.from_numpy(y[idx])
            logits = net(standardize(xb))
            loss = loss_fn(logits.reshape(-1, N_CLASSES), yb.reshape(-1))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            step += 1
            tot += loss.item()
            nb += 1
            if step % 200 == 0:
                rate = step * a.batch / (time.time() - t0)
                eta = (steps - step) * a.batch / rate / 60
                print(f"ep {ep + 1} step {step}/{steps} loss {tot / nb:.4f}  {rate:.0f} img/s  eta {eta:.0f} min", flush=True)
        m = evaluate(net, va["x"], va["y"].astype(np.int64))
        m.update(epoch=ep + 1, train_loss=tot / max(nb, 1), minutes=(time.time() - t0) / 60)
        log.append(m)
        print("VAL", json.dumps(m), flush=True)
        torch.save({"model": net.state_dict(), "metrics": m}, out / "last.pt")
        if m["plate_acc"] > best:
            best = m["plate_acc"]
            torch.save({"model": net.state_dict(), "metrics": m}, out / "best.pt")
        (out / "log.json").write_text(json.dumps(log, indent=1))
    print(f"done: best val plate accuracy {best:.4f}")


def cmd_export(a: argparse.Namespace) -> None:
    import torch

    net = build_model()
    ck = torch.load(a.ckpt, map_location="cpu")
    net.load_state_dict(ck["model"])
    net.eval()
    m = _Exportable.make(net).eval()
    dummy = torch.rand(1, 1, ps.OCR_H, ps.OCR_W) * 255
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(m, dummy, a.out, input_names=["x"], output_names=["probs"],
                      dynamic_axes={"x": {0: "n"}, "probs": {0: "n"}}, opset_version=17, dynamo=False)
    meta = {"kind": "plate_reader", "version": a.version, "alphabet": ps.ALPHABET, "max_len": ps.MAX_LEN,
            "empty_index": len(ps.ALPHABET), "input": {"height": ps.OCR_H, "width": ps.OCR_W, "channels": 1,
                                                        "pixels": "raw 0-255 grayscale, NCHW float32"},
            "output": "probs [N, max_len, 37]: slot k = k-th character (top row first); last class = empty",
            "trained_on": "synthetic Indian plates (training/platesynth.py)" + (" + site crops" if a.site else ""),
            "val_metrics": ck.get("metrics")}
    Path(a.out).with_suffix(".json").write_text(json.dumps(meta, indent=2))
    # parity check against PyTorch
    import onnxruntime as ort

    s = ort.InferenceSession(a.out, providers=["CPUExecutionProvider"])
    x = (np.random.rand(4, 1, ps.OCR_H, ps.OCR_W) * 255).astype(np.float32)
    with torch.no_grad():
        ref = m(torch.from_numpy(x)).numpy()
    got = s.run(None, {"x": x})[0]
    print(f"exported {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB); max |onnx - torch| = {np.abs(ref - got).max():.2e}")


def cmd_eval(a: argparse.Namespace) -> None:
    import onnxruntime as ort

    s = ort.InferenceSession(a.model, providers=["CPUExecutionProvider"])
    va = load_split(a.data, "val")
    x, y = va["x"].astype(np.float32)[:, None], va["y"]
    probs = np.concatenate([s.run(None, {"x": x[i:i + 256]})[0] for i in range(0, len(x), 256)])
    pred, conf = probs.argmax(-1), probs.max(-1).min(1)
    exact = (pred == y).all(1)
    print(json.dumps({
        "plate_acc": float(exact.mean()), "day": float(exact[va["night"] == 0].mean()),
        "night": float(exact[va["night"] == 1].mean()), "two_line": float(exact[va["rows"] == 2].mean()),
        "single_line": float(exact[va["rows"] == 1].mean()),
        "acc@conf>=0.7": float(exact[conf >= 0.7].mean()) if (conf >= 0.7).any() else 0.0,
        "coverage@conf>=0.7": float((conf >= 0.7).mean()),
    }, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("--out", default="training/data")
    g.add_argument("--train", type=int, default=240_000)
    g.add_argument("--val", type=int, default=6_000)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    t = sub.add_parser("train")
    t.add_argument("--data", default="training/data")
    t.add_argument("--out", default="training/runs/reader")
    t.add_argument("--epochs", type=int, default=14)
    t.add_argument("--batch", type=int, default=128)
    t.add_argument("--lr", type=float, default=2e-3)
    t.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--init")
    t.add_argument("--real", help="CSV of real labelled crops (path,plate)")
    t.add_argument("--real-dir")
    t.add_argument("--real-weight", type=float, default=0.3, help="share of real crops per epoch")
    e = sub.add_parser("export")
    e.add_argument("--ckpt", required=True)
    e.add_argument("--out", default="models/plate_reader.onnx")
    e.add_argument("--version", default="synthetic-1")
    e.add_argument("--site", action="store_true")
    v = sub.add_parser("eval")
    v.add_argument("--model", default="models/plate_reader.onnx")
    v.add_argument("--data", default="training/data")
    a = ap.parse_args()
    {"gen": cmd_gen, "train": cmd_train, "export": cmd_export, "eval": cmd_eval}[a.cmd](a)


if __name__ == "__main__":
    main()
