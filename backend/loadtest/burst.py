"""Burst load test for ANPR ingest (spec 14: bursts of 10 events/s across all cameras, no backlog).

Against a running server:
    python loadtest/burst.py --url http://localhost:8000 --key $PARK_ANPR_API_KEY --rate 10 --seconds 60
In-process (no server; uses FastAPI TestClient and a temporary SQLite DB):
    python loadtest/burst.py --inprocess --rate 50 --seconds 20

Each second it sends `rate` events (half IN, half OUT of earlier plates, 30 % side-by-side pairs),
concurrently, and reports throughput, latency percentiles, errors and whether it kept up.
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def plate(rng: random.Random) -> str:
    return f"MH{rng.randint(1, 50):02d}{rng.choice('ABCDEFGHJK')}{rng.choice('ABCDEFGHJK')}{rng.randint(1, 9999):04d}"


def make_events(rng: random.Random, n: int, inside: list[str]) -> list[dict]:
    evs = []
    ts = int(time.time() * 1000)
    for i in range(n):
        if inside and rng.random() < 0.5:
            p, direction, gate = inside.pop(rng.randrange(len(inside))), "OUT", "G2"
        else:
            p, direction, gate = plate(rng), "IN", "G1"
            inside.append(p)
        evs.append({"event_id": str(uuid.uuid4()), "gate_id": gate, "camera_ids": [f"{gate}-L"], "direction": direction,
                    "vehicle_class": "BIKE", "ts_ms": ts, "status": "READ", "plate": p, "confidence": 0.95,
                    "candidates": [{"plate": p, "confidence": 0.95}], "images": {}, "latency_ms": 800})
    return evs


def run(post, rate: int, seconds: int, workers: int = 16) -> dict:
    rng = random.Random(1)
    inside: list[str] = []
    lat, errors = [], 0
    t_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for sec in range(seconds):
            batch = make_events(rng, rate, inside)
            t0 = time.monotonic()
            for ok, dt in pool.map(post, batch):
                lat.append(dt)
                errors += 0 if ok else 1
            spent = time.monotonic() - t0
            if spent < 1:
                time.sleep(1 - spent)
    total = time.monotonic() - t_start
    lat.sort()
    n = len(lat)
    return {"events": n, "errors": errors, "wall_s": round(total, 2), "throughput_eps": round(n / total, 1),
            "p50_ms": round(lat[n // 2] * 1000, 1), "p95_ms": round(lat[int(n * 0.95)] * 1000, 1),
            "p99_ms": round(lat[int(n * 0.99)] * 1000, 1), "max_ms": round(lat[-1] * 1000, 1),
            "mean_ms": round(statistics.mean(lat) * 1000, 1), "kept_up": total <= seconds * 1.1}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--key", default=os.environ.get("PARK_ANPR_API_KEY", "dev-anpr-key"))
    ap.add_argument("--rate", type=int, default=10)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--inprocess", action="store_true")
    args = ap.parse_args()
    if args.inprocess:
        import tempfile

        os.environ["PARK_DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/load.db"
        os.environ["PARK_RUN_BACKGROUND_JOBS"] = "false"
        from fastapi.testclient import TestClient

        from app.db import Base, get_engine, session_scope
        from app.main import app
        from app.seed import seed_reference

        Base.metadata.create_all(get_engine())
        with session_scope() as db:
            seed_reference(db)
        client = TestClient(app)
        key = args.key

        def post(ev):
            t = time.monotonic()
            r = client.post("/api/anpr/events", json=ev, headers={"X-Device-Key": key})
            return r.status_code == 200, time.monotonic() - t
        workers = 1  # SQLite: single writer
    else:
        import httpx

        client = httpx.Client(base_url=args.url, timeout=10)

        def post(ev):
            t = time.monotonic()
            try:
                r = client.post("/api/anpr/events", json=ev, headers={"X-Device-Key": args.key})
                return r.status_code == 200, time.monotonic() - t
            except httpx.HTTPError:
                return False, time.monotonic() - t
        workers = 16
    print(run(post, args.rate, args.seconds, workers))


if __name__ == "__main__":
    main()
