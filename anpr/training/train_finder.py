#!/usr/bin/env python3
"""Train the plate FINDER (locates number plates in a camera frame) on synthetic gate scenes; export ONNX.

Architecture: a small fully-convolutional CenterNet-style network. For every 4x4 pixel cell it predicts
"is a plate centred here" (heatmap), the plate's width/height and a sub-cell offset. Fully convolutional,
so it runs on any frame size (multiples of 16) at inference.

    python -m training.train_finder gen    --out training/data_finder --train 50000 --val 1500
    python -m training.train_finder train  --data training/data_finder --epochs 12 --out training/runs/finder
    python -m training.train_finder export --ckpt training/runs/finder/best.pt --out models/plate_finder.onnx

Only synthetic scenes are used: the finder only has to say "a plate is here", which transfers from
synthetic images far better than reading does. The reader is what gets fine-tuned on site crops.
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

STRIDE = 4
MAX_BOXES = 4


def _gen_chunk(args):
    seed, n, path = args
    rng = random.Random(seed)
    xs = np.empty((n, ps.DET_SIZE, ps.DET_SIZE), np.uint8)
    bs = np.zeros((n, MAX_BOXES, 4), np.float32)
    nb = np.zeros(n, np.uint8)
    for i in range(n):
        g, boxes = ps.detector_scene(rng)
        xs[i] = g
        k = min(len(boxes), MAX_BOXES)
        bs[i, :k] = boxes[:k]
        nb[i] = k
    np.savez(path, x=xs, boxes=bs, n=nb)
    return path


def cmd_gen(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    chunk, jobs = 2500, []
    for split, total, base in (("train", a.train, 2_000_000), ("val", a.val, 8_000_000)):
        for k in range(math.ceil(total / chunk)):
            jobs.append((base + a.seed * 10_000 + k, min(chunk, total - k * chunk), str(out / f"{split}_{k:04d}.npz")))
    t = time.time()
    with mp.Pool(a.workers) as pool:
        for i, p in enumerate(pool.imap_unordered(_gen_chunk, jobs), 1):
            print(f"[{i}/{len(jobs)}] {p} ({time.time() - t:.0f}s)", flush=True)


def load_split(data, split):
    files = sorted(Path(data).glob(f"{split}_*.npz"))
    if not files:
        raise SystemExit(f"no {split} data in {data}")
    parts = [np.load(f) for f in files]
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0].files}


# ----------------------------------------------------------------------------- model
def build_model():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    def cbr(ci, co, s=1):
        return nn.Sequential(nn.Conv2d(ci, co, 3, s, 1, bias=False), nn.BatchNorm2d(co), nn.ReLU(inplace=True))

    class PlateFinder(nn.Module):
        def __init__(self):
            super().__init__()
            self.s1 = nn.Sequential(cbr(1, 16, 2), cbr(16, 32, 2), cbr(32, 32))       # /4
            self.s2 = nn.Sequential(cbr(32, 48, 2), cbr(48, 48))                    # /8
            self.s3 = nn.Sequential(cbr(48, 64, 2), cbr(64, 64), cbr(64, 64))       # /16
            self.l2 = nn.Conv2d(48, 64, 1)
            self.l1 = nn.Conv2d(32, 64, 1)
            self.fuse = nn.Sequential(cbr(64, 48), cbr(48, 48))
            self.hm = nn.Conv2d(48, 1, 1)
            self.wh = nn.Conv2d(48, 2, 1)
            self.off = nn.Conv2d(48, 2, 1)
            nn.init.constant_(self.hm.bias, -2.2)

        def forward(self, x):
            f1 = self.s1(x)
            f2 = self.s2(f1)
            f3 = self.s3(f2)
            u = F.interpolate(f3, size=f2.shape[-2:], mode="nearest") + self.l2(f2)
            u = F.interpolate(u, size=f1.shape[-2:], mode="nearest") + self.l1(f1)
            u = self.fuse(u)
            return self.hm(u), self.wh(u), self.off(u)

    return PlateFinder()


def standardize(x):
    x = x.float()
    return (x - 128.0) / 64.0


def make_targets(boxes, counts, out_hw):
    """CenterNet targets on the stride-4 grid."""
    n = len(counts)
    H, W = out_hw
    hm = np.zeros((n, 1, H, W), np.float32)
    wh = np.zeros((n, 2, H, W), np.float32)
    off = np.zeros((n, 2, H, W), np.float32)
    mask = np.zeros((n, 1, H, W), np.float32)
    ys, xs = np.mgrid[0:H, 0:W]
    for i in range(n):
        for b in boxes[i, : counts[i]]:
            x1, y1, x2, y2 = b / STRIDE
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            bw, bh = max(x2 - x1, 1e-3), max(y2 - y1, 1e-3)
            ix, iy = int(np.clip(cx, 0, W - 1)), int(np.clip(cy, 0, H - 1))
            sx, sy = max(0.6, bw / 6), max(0.6, bh / 6)
            g = np.exp(-(((xs - cx) ** 2) / (2 * sx ** 2) + ((ys - cy) ** 2) / (2 * sy ** 2)))
            hm[i, 0] = np.maximum(hm[i, 0], g)
            hm[i, 0, iy, ix] = 1.0
            wh[i, :, iy, ix] = np.log([bw, bh])
            off[i, :, iy, ix] = [cx - ix, cy - iy]
            mask[i, 0, iy, ix] = 1.0
    return hm, wh, off, mask


def focal_loss(pred_logit, gt):
    import torch

    p = torch.sigmoid(pred_logit).clamp(1e-4, 1 - 1e-4)
    pos = gt.eq(1).float()
    neg = 1 - pos
    pos_loss = torch.log(p) * (1 - p) ** 2 * pos
    neg_loss = torch.log(1 - p) * p ** 2 * (1 - gt) ** 4 * neg
    npos = pos.sum().clamp(min=1)
    return -(pos_loss.sum() + neg_loss.sum()) / npos


def decode(hm, wh, off, thr=0.3, topk=20):
    """numpy decode of one image's outputs -> list of (x1,y1,x2,y2,score) in input pixels."""
    h = hm[0]
    mx = np.maximum.reduce([np.pad(h, 1, constant_values=0)[dy:dy + h.shape[0], dx:dx + h.shape[1]]
                            for dy in range(3) for dx in range(3)])
    peaks = (h == mx) & (h >= thr)
    ys, xs = np.nonzero(peaks)
    order = np.argsort(-h[ys, xs])[:topk]
    out = []
    for k in order:
        y, x = ys[k], xs[k]
        bw, bh = np.exp(wh[0, y, x]) * STRIDE, np.exp(wh[1, y, x]) * STRIDE
        cx, cy = (x + off[0, y, x]) * STRIDE, (y + off[1, y, x]) * STRIDE
        out.append((cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2, float(h[y, x])))
    return out


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def evaluate(net, va, thr=0.3, n=None):
    import torch

    net.eval()
    x, boxes, counts = va["x"], va["boxes"], va["n"]
    n = n or len(x)
    tp = fp = fn = 0
    with torch.no_grad():
        for i in range(0, n, 128):
            xb = standardize(torch.from_numpy(x[i:i + 128]).unsqueeze(1))
            hm, wh, off = net(xb)
            hm = torch.sigmoid(hm).numpy()
            wh, off = wh.numpy(), off.numpy()
            for j in range(hm.shape[0]):
                pred = decode(hm[j], wh[j], off[j], thr)
                gts = [tuple(b) for b in boxes[i + j, : counts[i + j]]]
                used = set()
                for p in pred:
                    best = max(range(len(gts)), key=lambda k: _iou(p, gts[k]), default=None)
                    if best is not None and best not in used and _iou(p, gts[best]) >= 0.5:
                        used.add(best)
                        tp += 1
                    else:
                        fp += 1
                fn += len(gts) - len(used)
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    return {"precision": prec, "recall": rec, "f1": 2 * prec * rec / max(1e-9, prec + rec), "tp": tp, "fp": fp, "fn": fn}


def cmd_train(a):
    import torch

    torch.set_num_threads(a.threads)
    torch.manual_seed(a.seed)
    rng = np.random.default_rng(a.seed)
    tr, va = load_split(a.data, "train"), load_split(a.data, "val")
    net = build_model()
    if a.init:
        net.load_state_dict(torch.load(a.init, map_location="cpu")["model"])
    x, boxes, counts = tr["x"], tr["boxes"], tr["n"]
    print(f"params {sum(p.numel() for p in net.parameters()) / 1e6:.2f}M  train {len(x)}  val {len(va['x'])}")
    opt = torch.optim.AdamW(net.parameters(), lr=a.lr, weight_decay=1e-4)
    steps = a.epochs * math.ceil(len(x) / a.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.15)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    grid = (ps.DET_SIZE // STRIDE, ps.DET_SIZE // STRIDE)
    best, log, step, t0 = -1.0, [], 0, time.time()
    for ep in range(a.epochs):
        net.train()
        order = rng.permutation(len(x))
        tot = nb = 0
        for i in range(0, len(order), a.batch):
            idx = order[i:i + a.batch]
            xb = torch.from_numpy(x[idx]).unsqueeze(1).float()
            bb, cc = boxes[idx].copy(), counts[idx]
            if rng.random() < 0.5:  # horizontal flip (plates are symmetric enough for localisation)
                xb = torch.flip(xb, dims=[3])
                x1 = ps.DET_SIZE - bb[..., 2]
                bb[..., 2] = ps.DET_SIZE - bb[..., 0]
                bb[..., 0] = x1
            xb = (xb - 128) * float(rng.uniform(0.75, 1.25)) + 128 + float(rng.uniform(-20, 20))
            hm_t, wh_t, off_t, m_t = (torch.from_numpy(v) for v in make_targets(bb, cc, grid))
            hm, wh, off = net(standardize(xb.clamp(0, 255)))
            npos = m_t.sum().clamp(min=1)
            loss = focal_loss(hm, hm_t) + 0.5 * (torch.abs(wh - wh_t) * m_t).sum() / npos + (torch.abs(off - off_t) * m_t).sum() / npos
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            step += 1
            tot += loss.item()
            nb += 1
            if step % 100 == 0:
                rate = step * a.batch / (time.time() - t0)
                print(f"ep {ep + 1} step {step}/{steps} loss {tot / nb:.4f}  {rate:.0f} img/s  eta {(steps - step) * a.batch / rate / 60:.0f} min", flush=True)
        m = evaluate(net, va)
        m.update(epoch=ep + 1, train_loss=tot / max(nb, 1), minutes=(time.time() - t0) / 60)
        log.append(m)
        print("VAL", json.dumps(m), flush=True)
        torch.save({"model": net.state_dict(), "metrics": m}, out / "last.pt")
        if m["f1"] > best:
            best = m["f1"]
            torch.save({"model": net.state_dict(), "metrics": m}, out / "best.pt")
        (out / "log.json").write_text(json.dumps(log, indent=1))
    print(f"done: best val F1 {best:.4f}")


def cmd_export(a):
    import torch
    import torch.nn as nn

    net = build_model()
    ck = torch.load(a.ckpt, map_location="cpu")
    net.load_state_dict(ck["model"])
    net.eval()

    class M(nn.Module):
        def __init__(self, n):
            super().__init__()
            self.n = n

        def forward(self, x):  # raw 0-255 grayscale [1,1,H,W], H and W multiples of 16
            hm, wh, off = self.n((x - 128.0) / 64.0)
            return torch.sigmoid(hm), wh, off

    m = M(net).eval()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(m, torch.rand(1, 1, 256, 256) * 255, a.out, input_names=["x"], output_names=["heatmap", "wh", "offset"],
                      dynamic_axes={"x": {2: "h", 3: "w"}, "heatmap": {2: "gh", 3: "gw"}, "wh": {2: "gh", 3: "gw"},
                                    "offset": {2: "gh", 3: "gw"}}, opset_version=17, dynamo=False)
    meta = {"kind": "plate_finder", "version": a.version, "stride": STRIDE, "input": "raw 0-255 grayscale [1,1,H,W], H,W multiple of 16",
            "outputs": {"heatmap": "sigmoid, [1,1,H/4,W/4]", "wh": "log(width,height) in stride-4 cells", "offset": "cell offset"},
            "trained_on": "synthetic gate scenes (training/platesynth.py)", "plate_width_px_trained": [18, 130],
            "val_metrics": ck.get("metrics")}
    Path(a.out).with_suffix(".json").write_text(json.dumps(meta, indent=2))
    import onnxruntime as ort

    s = ort.InferenceSession(a.out, providers=["CPUExecutionProvider"])
    x = (np.random.rand(1, 1, 352, 640) * 255).astype(np.float32)
    with torch.no_grad():
        ref = m(torch.from_numpy(x))[0].numpy()
    got = s.run(None, {"x": x})[0]
    print(f"exported {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB); max diff {np.abs(ref - got).max():.1e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("--out", default="training/data_finder")
    g.add_argument("--train", type=int, default=50_000)
    g.add_argument("--val", type=int, default=1_500)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    t = sub.add_parser("train")
    t.add_argument("--data", default="training/data_finder")
    t.add_argument("--out", default="training/runs/finder")
    t.add_argument("--epochs", type=int, default=12)
    t.add_argument("--batch", type=int, default=32)
    t.add_argument("--lr", type=float, default=2e-3)
    t.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--init")
    e = sub.add_parser("export")
    e.add_argument("--ckpt", required=True)
    e.add_argument("--out", default="models/plate_finder.onnx")
    e.add_argument("--version", default="synthetic-1")
    a = ap.parse_args()
    {"gen": cmd_gen, "train": cmd_train, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
