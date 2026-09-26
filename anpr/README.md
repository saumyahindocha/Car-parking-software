# anpr/ — ANPR edge service

Reads the rear number plates of moving two-wheelers (and later cars) at the
two gates. It merges the reads of the two overlapping cameras per gate and
posts one event per vehicle to the backend. When the backend is down,
events go into a durable local outbox.

The full design, config reference, event contract, and model/library
licence table are in [`../docs/ANPR.md`](../docs/ANPR.md).

```
anpr/
├── anpr_service/
│   ├── cli.py            python -m anpr_service {run,replay,synth,evaluate,check-config}
│   ├── service.py        multi-process supervisor: camera workers, gate aggregators, emitter
│   ├── runner.py         deterministic single-process replay (tests, evaluate, --inline)
│   ├── ingest.py         RTSP (FFmpeg/GStreamer) with reconnect + file replay
│   ├── pipeline.py       per-camera pipeline: detect → track → plate → OCR → vote → crossing
│   ├── tracker.py        SORT-style Kalman + Hungarian tracker (own implementation)
│   ├── hungarian.py      assignment solver (SciPy if present, else built-in)
│   ├── rows.py           single / two-line row layout detection (horizontal projection)
│   ├── voting.py         per-track character-position voting
│   ├── plates.py         Indian formats, state codes, confusion map, matching
│   ├── aggregator.py     cross-camera merge, UNREAD merge, dedupe, direction / wrong-way
│   ├── events.py         event contract + image writing
│   ├── outbox.py         SQLite outbox (strict order, dead-letter)
│   ├── emitter.py        in-order delivery with exponential backoff
│   ├── backend.py        HTTP client (events, heartbeat, config), JSONL sink
│   ├── heartbeat.py      10 s camera heartbeats
│   ├── synth.py          synthetic rear-view gate videos + ground truth
│   ├── evaluate.py       Section 15 evaluation report
│   └── recognizers/      PlateRecognizer interface + classical / onnx / commercial engines
├── config/site.example.yaml
├── tools/label_crops.py   label plate crops (GUI or headless)
├── tools/train_ocr.py     CRNN+CTC training (PyTorch) → ONNX
├── tools/train_detector.md
├── tests/                 pytest suite (about 15 s)
├── Dockerfile             CUDA runtime image (CPU variant via build args)
└── requirements*.txt
```

## Quick start (laptop, no cameras, no models)

```bash
cd anpr
pip install -r requirements-dev.txt

# 1. Render synthetic gate videos (2 gates × L/R ANPR + overview, 1280x720) + ground truth
python -m anpr_service synth --out demo            # --full-res for 2560x1440

# 2. Replay them through the full multi-process service (classical recogniser).
#    Without BACKEND_URL the events go to demo/events.jsonl; the images go to demo/images/.
python -m anpr_service replay --config demo/site.synth.yaml            # as fast as possible
python -m anpr_service replay --config demo/site.synth.yaml --realtime # paced like live cameras
BACKEND_URL=http://localhost:8000 ANPR_API_KEY=dev-key \
  python -m anpr_service replay --config demo/site.synth.yaml         # post to the backend

# 3. Accuracy report (per-camera exact / approximate / read rate, side-by-side subset)
python -m anpr_service evaluate --labels demo

# Tests
python -m pytest -q
```

## Production

```bash
cp config/site.example.yaml /srv/anpr/site.yaml     # edit RTSP URLs, ROIs, capture lines
docker build -t anpr-service .                      # CUDA + onnxruntime-gpu
docker run --gpus all --restart unless-stopped \
  -e BACKEND_URL=http://backend:8000 -e ANPR_API_KEY=... -e ANPR_RECOGNIZER=onnx \
  -v /srv/anpr/site.yaml:/config/site.yaml:ro -v /srv/models:/models:ro \
  -v /srv/images:/data/images -v /srv/anpr:/data/anpr anpr-service
```

CPU-only image: `docker build --build-arg BASE_IMAGE=ubuntu:24.04 --build-arg ONNX_PACKAGE=onnxruntime -t anpr-service:cpu .`

`onnxruntime` is optional. The service imports it only when
`recognizer.kind: onnx`. Choose a recogniser per site:

| kind | what | when |
|---|---|---|
| `classical` | OpenCV only: background subtraction, plate finder, Hershey-glyph template OCR | demo / CI on synthetic video only |
| `onnx` | your detector + CRNN OCR (see `tools/`), CUDA/TensorRT | production, after training on site data |
| `commercial` | Plate Recognizer Snapshot API / on-prem SDK, `regions=in` | quick start with a paid licence |
