# ANPR edge service

Code: `anpr/`. Entry point: `python -m anpr_service`. Target runtime is
Python 3.12 on Ubuntu 24.04 with an NVIDIA GPU.

## 1. Architecture

```
            ┌──────────── edge server (one container) ─────────────────────────────┐
 RTSP  ───► │ cam worker G1-L ─┐                                                    │
 RTSP  ───► │ cam worker G1-R ─┼─► gate aggregator G1 ─┐                           │
 RTSP  ───► │ cam worker G1-O ─┘  (merge, dedupe,      │  SQLite    ┌─────────┐    │  POST /api/anpr/events
            │                      direction, images)  ├─► outbox ─►│ emitter │ ──►│  (X-Device-Key)
 RTSP  ───► │ cam worker G2-L ─┐                       │            └─────────┘    │
 RTSP  ───► │ cam worker G2-R ─┼─► gate aggregator G2 ─┘                           │
 RTSP  ───► │ cam worker G2-O ─┘                                                    │
            │ supervisor: config refresh (GET /api/anpr/config), watchdog, signals  │
            └───────────────────────────────────────────────────────────────────────┘
   every camera worker: POST /api/devices/heartbeat every 10 s
   images: IMAGE_ROOT/YYYY/MM/DD/<gate>/<event_id>_<kind>.jpg (volume shared with the backend)
```

* **One process per camera** (`multiprocessing`, `spawn`). Each has a
  grabber thread with a bounded queue, the recognition pipeline, and a
  heartbeat thread. Overview cameras send a downscaled JPEG snapshot every
  0.25 s instead of running recognition.
* **One aggregator process per gate** receives `CameraRead` messages. These
  carry JPEG bytes, never raw frames. The aggregator merges, de-duplicates,
  resolves direction, writes the images and appends each event to the outbox.
* **One emitter process** delivers from the outbox strictly in order.
* **Supervisor.** It restarts dead camera workers. It refreshes the backend
  config every 30 s: a direction or settings change goes to the aggregators
  and pipelines, and a camera URL/ROI/line change restarts that worker.
  SIGTERM drains gracefully.
* **Replay mode** (`replay`) runs the same processes on video files. All
  cameras share one start epoch, so their timelines line up. `--realtime`
  paces the frames and `--loop` repeats the videos. `replay --inline` (and the
  tests and `evaluate`) runs every component in one deterministic process.

### 1.1 Per-camera pipeline (`pipeline.py`)

1. **Detect and classify** vehicles (BIKE / CAR / OTHER) and plates through the
   `PlateRecognizer` engine. Detections outside the ROI polygon are dropped.
2. **Track** with the in-house SORT tracker (`tracker.py`): a constant-velocity
   Kalman filter over (cx, cy, w, h) and Hungarian assignment on IoU, with a
   normalised centre-distance fallback and a size-change gate. Tracks coast
   for up to `max_age` frames, so a bike hidden by its neighbour keeps its ID.
3. **Associate plates to vehicles.** A plate goes to the vehicle box that
   contains it. When several boxes do, the nearest vehicle (lowest box bottom
   in a rear view) wins. A plausibility rule applies: the plate centre must
   be no more than 4 plate-widths above the vehicle's ground contact. This
   stops a far bike's plate from being assigned to a near bike whose box
   overlaps it.
4. **Quality gate.** A frame is skipped for voting when the plate is narrower
   than `min_plate_width_px`, when a nearer vehicle covers more than
   `max_plate_occlusion` of the plate, when it is blurred (Laplacian variance
   below `min_sharpness`), or when its text row is under 13 px tall.
5. **OCR.** `rows.py` binarises the crop, removes the plate border and splits
   the horizontal ink profile into one or two rows. Each row is read, then
   the rows are joined top to bottom (`MH43` + `AB1234`).
6. **Voting** (`voting.py`). Each read is first corrected position by
   position to a valid Indian format where possible. Reads are grouped by
   length. Each character position sums `frame weight × char confidence`,
   where frame weight comes from plate size, sharpness and occlusion.
   Candidates are the consensus, runner-up swaps and whole-string tallies.
   Invalid candidates are penalised, so a valid reading wins when one exists.
7. **Crossing.** The bottom-centre of the vehicle box (`track_anchor`) is
   tested against the capture-line segment. The travel sign is
   `sign(dot(track displacement, in_vector))`. The track is finalised at once
   if the vote is confident (at least `min_votes` votes, confidence at least
   `min_confidence`, valid format), otherwise after `post_cross_s`. It is then
   emitted as a `CameraRead`: READ, or UNREAD with the best crop and the
   annotated full frame.

### 1.2 Gate aggregation (`aggregator.py`)

| Situation | Rule |
|---|---|
| Same plate from both cameras | Confusion-aware equality within `merge_window_s` (10 s): **one event**. The highest-confidence read supplies `plate_crop`/`full_frame`, the other camera's images go in `images.extra`, and `camera_ids` lists both. |
| Near-identical reads (Levenshtein ≤ 1) of the **same crossing** from both cameras | Merged. Two vehicles cannot be at the same place at the same time. |
| UNREAD from one camera and any read from the **other** camera | Merged when the crossing times are within `unread_merge_s` and the lateral positions within `unread_merge_dx`. Lateral position is the fraction of the gate width, taken from each camera's `gate_span`. |
| UNREAD with no partner | Emitted as `status: "UNREAD"` with images, so nothing passes silently. |
| Same plate at the same gate within `dedupe_window_s` (60 s) | Dropped and counted. A late read within the merge window is folded into the event already sent. |
| Direction | `observed = IN if travel_sign > 0 else OUT`. Gate BOTH gives `wrong_way=false`. A single-direction gate facing opposite travel gives `wrong_way=true`, with `direction` set to the observed travel. The direction comes from the latest backend config, which applies the admin schedule. |

**When a crossing is emitted.** A cluster is emitted when every live ANPR
camera of the gate has processed frames up to `first crossing + merge_hold_s`
(0.5 s). Progress is tracked with event-time watermarks. A cluster that
already has reads from both cameras is emitted at once. A camera at EOS, or
silent for `camera_stale_s`, does not hold up the gate. Because merging uses
event time, it behaves the same live and in as-fast-as-possible replay.

**Latency budget** (target < 1.5 s from crossing to event, 4 vehicles per gate):

| Stage | Budget |
|---|---|
| Frame processing lag | ≈ 1 frame |
| `post_cross_s` (only when not yet confident) | 0–0.25 s |
| `merge_hold_s` | 0.5 s (skipped when both cameras have reported) |
| Image write + outbox + POST | < 50 ms |

`latency_ms` in each event is the time from the crossing frame's grab (wall
clock) to the event being queued. Per-stage timings (`detect`, `track`,
`plates`, `ocr`, `finalize`: mean, p95, max) are logged every 60 s per camera.
They are also returned by `replay --inline` and `evaluate`.

Measured with the classical engine on a 4-core container (no GPU), running 6 camera
processes at 1280×720 and 25 fps with `merge_hold_s` = 0.5 s. This was a realtime replay of the
two-gate synthetic demo, posting to the real backend: 19 events, min 222 ms, median 527 ms,
p95 818 ms, max 838 ms. Events seen by both cameras go out at about 0.22–0.29 s. Single-camera
events wait for the hold, at about 0.53–0.84 s. The classical engine costs about 8–12 ms per frame
per camera.

## 2. The recognition engine interface

```python
class PlateRecognizer(ABC):
    def analyze(self, frame, roi_mask=None) -> list[VehicleObservation]  # box, class, plate box, optional read
    def read_plate(self, crop) -> OcrResult | None                       # rows joined top to bottom
```

| Engine | `recognizer.kind` | Notes |
|---|---|---|
| `LocalOnnxRecognizer` | `onnx` | A YOLO-style detector ONNX (`yolov8` layout `(1,4+nc,N)` or `yolov5`/YOLOX `(1,N,5+nc)`), letterbox, class-aware NMS in NumPy, an optional second-stage plate detector, and a CRNN/CTC OCR ONNX run per row. Providers are tried in order: TensorRT, then CUDA, then CPU. `onnxruntime` is imported only when this engine is selected. |
| `CommercialApiRecognizer` | `commercial` | Plate Recognizer Snapshot API (`Authorization: Token $PLATE_RECOGNIZER_API_KEY`) or the on-prem SDK URL, with `regions=in`. It detects and reads in one call. Each analysed frame is billable, so raise `pipeline.process_every_n` or `commercial.min_interval_s`. |
| `TrainedRecognizer` | `trained` | **Our own models, no third-party service.** Vehicles from motion (MOG2, as in `classical`), plates from `models/plate_finder.onnx`, characters from `models/plate_reader.onnx`, both trained on computer-generated Indian plates (section 9). CPU only via `onnxruntime`. Recommended engine for the site. |
| `ClassicalRecognizer` | `classical` | Pure OpenCV: MOG2 blobs, plate-guided splitting of merged blobs, a white/yellow plate finder, and template OCR against Hershey glyphs. **It is only good enough for the synthetic replay videos.** |

Training our own models: section 9. For the `onnx` engine: `tools/label_crops.py` labels crops, `tools/train_ocr.py` trains
the CRNN+CTC model and exports the ONNX the service expects, and
`tools/train_detector.md` covers the detector.

## 3. Event contract

`POST {BACKEND}/api/anpr/events` with header `X-Device-Key: <ANPR_API_KEY>`:

```json
{
  "event_id": "5b0c0f5e-2f64-4c1e-9a53-3d8f0c1b7e21",
  "gate_id": "G1",
  "camera_ids": ["G1-L", "G1-R"],
  "direction": "IN",
  "wrong_way": false,
  "vehicle_class": "BIKE",
  "ts_ms": 1760000003923,
  "status": "READ",
  "plate": "MH14GX0786",
  "confidence": 0.95,
  "candidates": [{"plate": "MH14GX0786", "confidence": 0.95}, {"plate": "MH14OX0786", "confidence": 0.41}],
  "images": {
    "plate_crop": "2026/09/26/G1/5b0c0f5e-..._plate_crop.jpg",
    "full_frame": "2026/09/26/G1/5b0c0f5e-..._full_frame.jpg",
    "overview":   "2026/09/26/G1/5b0c0f5e-..._overview.jpg",
    "extra": ["2026/09/26/G1/5b0c0f5e-..._extra1_G1-L_plate_crop.jpg",
              "2026/09/26/G1/5b0c0f5e-..._extra1_G1-L_full_frame.jpg"]
  },
  "latency_ms": 640
}
```

* `ts_ms` is the capture-line crossing (epoch ms, the earliest camera).
* `status: "UNREAD"` has `plate: null`. `candidates` may still list
  low-confidence guesses for the review queue.
* Image paths are relative to `IMAGE_ROOT`. Any image kind can be `null`, for
  example when there is no overview camera or no plate was ever seen.
* Delivery is at-least-once and in order. The backend must be idempotent on
  `event_id`. 2xx and 409 count as delivered. 5xx, 408, 425, 429, 401, 403 and
  network errors are retried with exponential backoff (1 s doubling to 30 s,
  with jitter) and nothing behind the failing event is sent first. Other 4xx
  responses move the event to the `dead_letter` table and the queue continues.

Heartbeat, every 10 s per camera: `POST {BACKEND}/api/devices/heartbeat`

```json
{"device_id": "G1-L", "kind": "CAMERA", "gate_id": "G1",
 "metrics": {"fps": 25.0, "last_frame_ts_ms": 1760000003923, "read_rate": 0.97, "stream_ok": true, "queue_depth": 0}}
```

`read_rate` is the share of the last 100 crossings at this camera that were
READ.

Config refresh: `GET {BACKEND}/api/anpr/config` with `X-Device-Key` returns
`{"gates":[{"id","name","direction","cameras":[{"id","role","rtsp_url","roi","capture_line","in_vector"}]}],"settings":{...}}`.
It is fetched **at start-up, in live `run` and in `replay`** whenever `backend.url` is set, and then
every `config_refresh_s`. Precedence:

* **Gate `direction` always comes from the backend** when it answers, because the backend resolves
  time-of-day schedules. The YAML value is only a fallback when the backend is unreachable.
* The backend also supplies `merge_window_s`, `dedupe_window_s`, `min_confidence` and `state_codes`,
  plus the camera `rtsp_url`, `role`, `side` and `enabled` fields.
* **Camera geometry** (`roi`, `capture_line`, `in_vector`, taken as one unit) follows
  `backend.geometry`:
  * `prefer_local` (the default): the YAML wins whenever the YAML camera defines a `capture_line`. The
    backend's geometry is used only for cameras that have none locally. This keeps synthetic/replay
    configs and on-site calibrated YAML correct even though the seeded backend cameras carry
    placeholder pixel geometry.
  * `prefer_backend`: non-empty backend geometry wins, for geometry edited in the admin UI.
* Local-only fields are always kept: `gate_span`, `gstreamer_pipeline` and `replay_file`.
* During `replay`, camera changes from the backend never restart a worker, because that would rewind
  its video.

## 4. Configuration reference (`anpr/config/site.example.yaml`)

`${VAR}` and `${VAR:-default}` are expanded from the environment. The
environment variables `BACKEND_URL`, `ANPR_API_KEY`, `IMAGE_ROOT` and
`ANPR_OUTBOX` override the file.

| Key | Default | Meaning |
|---|---|---|
| `image_root` | `/data/images` | Shared image volume |
| `outbox_path` | `/data/anpr/outbox.sqlite` | Durable outbox (WAL) |
| `backend.url` / `api_key` | – | Empty URL means no posting (use `emitter.events_jsonl`) |
| `backend.config_refresh_s` | 30 | Config poll period |
| `backend.geometry` | `prefer_local` | Owner of camera roi/capture_line/in_vector (see §3) |
| `recognizer.kind` | `classical` | `classical` / `onnx` / `commercial` |
| `recognizer.onnx.*` | see file | model paths, `detector_format`, `detector_classes`, OCR input size, alphabet, blank index, providers |
| `recognizer.commercial.*` | see file | `api_url`, `api_key_env`, `regions: [in]`, `min_interval_s` |
| `plates.state_codes` | all states/UTs | Valid first two letters |
| `plates.allow_bh` / `require_state_code` | true / true | Format rules |
| `settings.merge_window_s` | 10 | Cross-camera merge window |
| `settings.dedupe_window_s` | 60 | Same plate, same gate drop window |
| `settings.min_confidence` | 0.6 | Below this, or an invalid format, gives UNREAD |
| `settings.merge_hold_s` | 0.5 | Wait for the other camera (latency budget) |
| `settings.unread_merge_s` / `unread_merge_dx` | 1.5 / 0.15 | UNREAD merge tolerances (time, gate-width fraction) |
| `settings.heartbeat_s` / `camera_stale_s` | 10 / 3 | Heartbeat period; silent-camera timeout for merging |
| `pipeline.process_every_n` | 1 | Analyse every n-th frame |
| `pipeline.track_anchor` | `bottom` | Point tested against the capture line |
| `pipeline.min_plate_width_px` / `min_sharpness` / `max_plate_occlusion` | 36 / 12 / 0.2 | Voting quality gate |
| `pipeline.min_votes` / `post_cross_s` | 3 / 0.25 | Early finalisation / extra voting time |
| `pipeline.tracker.*` | 0.2 / 0.6 / 12 / 2 | IoU threshold, centre-distance gate, max coast frames, min hits |
| `ingest.*` | ffmpeg, tcp, 1→30 s | Capture backend, RTSP transport, reconnect backoff, read timeout, queue size |
| `emitter.retry_initial_s` / `retry_max_s` / `events_jsonl` | 1 / 30 / null | Delivery backoff; optional audit copy |
| `gates[].direction` | BOTH | IN / OUT / BOTH (the backend applies schedules) |
| `gates[].cameras[]` | – | `id`, `role` (ANPR/OVERVIEW), `side`, `rtsp_url` or `gstreamer_pipeline` or `replay_file`, `roi` polygon, `capture_line` (2 points), `in_vector` (travel direction meaning IN, in image coordinates), `gate_span` (share of the gate width this camera covers at the capture line; LEFT defaults to 0–0.65, RIGHT to 0.35–1) |

Coordinates can be pixels, or fractions of the frame when every value is at
most 1. Rear-facing cameras at an **entry** gate see entering bikes ride away
(upwards), so `in_vector: [0, -1]`. At an **exit** gate the same mounting sees
leaving bikes ride away, so `in_vector: [0, 1]`.

## 5. Evaluation (spec Section 15)

`python -m anpr_service evaluate --labels <dir> [--config site.yaml] [--out report.json]`
runs the pipeline over every `ground_truth.json` label set under `<dir>`. The
format is the one written by `synth`. For real clips, list the plate,
`cross_ms` (offset from the clip start), `visible_in` cameras and
`side_by_side` for each vehicle. The report gives, per camera and per gate:
exact-match accuracy, approximate-match rate (confusion-aware, Levenshtein
≤ 1), read rate, the side-by-side subset, missed vehicles, extra
(duplicate/false) events, and UNREAD handling of unreadable plates.

`synth` renders a coherent site. The first gate (G1) is the entry gate: direction IN,
`in_vector [0,-1]`. The other gates (G2) are exit gates: direction OUT, `in_vector [0,1]`, because
the same rear view now shows leaving bikes riding away. **The same plates leave through G2
`--exit-delay` seconds (default 8) after entering through G1**, so the backend opens and closes a
session for each vehicle. `--wrong-way` adds one rider going the wrong way through the entry gate.
Against the real backend (seeded G1 = IN, G2 = OUT), the demo gives 8 sessions opened and closed,
16 MATCHED events, 2 UNREAD and 1 WRONG_WAY.

Result on the bundled synthetic scenes (2 gates, 9 vehicles each: a
side-by-side pair, a middle bike seen by both cameras, a staggered partly
occluded pair, a BH plate, a single-line plus two-line pair and an
unreadable plate): 100% exact at camera and gate level, one event per
vehicle, and the unreadable plate emitted as UNREAD. **This measures the
plumbing, not real-world accuracy.**

## 6. Camera tuning summary

The full procedure belongs in `docs/CAMERA_SETUP.md`. The key numbers:

* Plate width at least **130 px** (150 px preferred) at the capture line, at
  4 MP (2560×1440), with each camera covering half the gate and about 30%
  overlap.
* Shutter **1/1000 s or faster** (1/2000 s at night with IR) to freeze
  15–20 km/h. Fix gain and shutter; do not let auto-exposure lengthen the
  shutter at dusk.
* Mount 2.5–3 m high, 5–7 m behind the capture line, with vertical angle
  under 30° and horizontal angle under 20° to the plate. WDR on, 850 nm IR.
* Put the capture line where plates are largest and still fully in frame.
  Draw the ROI to exclude the far background. Set `gate_span` for each camera
  to the part of the gate width it covers at the line; UNREAD merging needs
  it.
* Validate with `evaluate` on recorded pilot clips before copying settings
  to the second gate. Target read rate is at least 95% by day and 90% at
  night.

## 7. Licence table (models and libraries)

| Component | Licence | Used for | Closed-deployment note |
|---|---|---|---|
| This service's code (tracker, Hungarian, voting, classical engine) | project licence | everything | Original code; no AGPL/GPL code copied |
| OpenCV (`opencv-python-headless`) | Apache-2.0 | video I/O, image ops, MOG2, Hershey fonts | OK |
| FFmpeg (bundled in the OpenCV wheel) | LGPL-2.1+ | RTSP/H.264/H.265 decoding | LGPL build: OK with dynamic linking and notices. A custom FFmpeg built with `--enable-gpl` (x264/x265) becomes **GPL**. H.265 decoding may carry patent-pool obligations. |
| GStreamer (optional, OpenCV built with it) | LGPL-2.1 | hardware-decode pipelines | Core and "good" plugins are OK. Check "bad"/"ugly" plugins (patents, some GPL). NVIDIA DeepStream / `nvv4l2decoder` fall under the NVIDIA licence. |
| NumPy, SciPy (optional) | BSD-3-Clause | arrays, assignment | OK |
| httpx | BSD-3-Clause | backend client | OK |
| pydantic | MIT | config | OK |
| PyYAML | MIT | config | OK |
| ONNX Runtime / onnxruntime-gpu | MIT | inference | OK. CUDA, cuDNN and TensorRT libraries are under the **NVIDIA EULA**: redistribution is allowed only as the EULA permits, which is fine inside the `nvidia/cuda` base image. |
| `nvidia/cuda` base image | NVIDIA Deep Learning Container licence | Docker base | OK for deployment on NVIDIA GPUs; review before redistributing images |
| PyTorch (training only) | BSD-3-Clause | `training/`, `tools/train_ocr.py` | OK |
| Our plate models (`models/plate_*.onnx`) | project licence | `trained` engine | Trained only on images we generate; no third-party dataset or pre-trained weights |
| Fonts used to render training plates (DejaVu, Liberation, GNU FreeFont) | Bitstream Vera / OFL / GPL-with-font-exception | training images only | Not shipped; rendered glyphs in a model are not a copy of the font |
| **Ultralytics YOLOv5 / YOLOv8 / YOLO11** | **AGPL-3.0** | (alternative detector) | **Needs an Ultralytics Enterprise licence** for a closed or commercial deployment. Otherwise the whole service must be released under AGPL. Not used by default. |
| YOLOX (Megvii) | Apache-2.0 | recommended detector | OK |
| RT-DETR (lyuwenyu / PaddleDetection) | Apache-2.0 | alternative detector | OK |
| PaddleOCR (PP-OCR models) | Apache-2.0 | alternative OCR | OK |
| fast-plate-ocr / fast-alpr | MIT | alternative plate OCR models | OK. Check the licence of each pre-trained model's dataset. |
| EasyOCR, Tesseract | Apache-2.0 | generic OCR (weak on two-line plates) | OK |
| OpenALPR (open-source edition) | AGPL-3.0 | – | Avoid, or buy a commercial licence |
| Plate Recognizer (Snapshot API / SDK) | Commercial subscription | `CommercialApiRecognizer` | Paid per lookup or per camera. The cloud API sends images off-site (DPDP Act consent and notice); the on-prem SDK keeps them local. |
| NVIDIA TAO LPDNet / LPRNet | NVIDIA model licence | alternative models | Allowed on NVIDIA hardware; read the terms |
| CVAT / Label Studio | MIT / Apache-2.0 | dataset labelling | OK |

## 8. Assumptions and limitations

* **The trained engine has only seen computer-generated plates** until the
  site week (section 9). Expect it to be noticeably weaker than the 95% / 90%
  target on real footage at first; the week of site crops closes the gap.
* **The classical engine is a demo engine.** It reads the synthetic videos,
  which use flat colours, a static background and Hershey-font plates, at
  100%. On real footage it will fail: headlights, shadows and real fonts
  break background subtraction and template OCR. Production needs the `onnx`
  engine with models trained on site data, or the commercial engine. No
  trained model weights are shipped.
* Occlusion handling uses box geometry: a nearer box covering the plate
  means the frame is skipped. With a detector trained on real data, boxes are
  tighter and fewer frames are skipped.
* The lateral position used for UNREAD merging depends on each camera's
  `gate_span`. Set it during camera commissioning.
* `latency_ms` measures the pipeline (crossing to event queued). Time spent
  in the outbox while the backend is down is not included.
* Event dates in image paths use the container's local time zone
  (`TZ=Asia/Kolkata`).
* Late reads within the merge window after an event has been sent are folded
  in (logged and counted) but not re-posted. The backend contract has no
  "update event" call, so the late camera's images are not attached.

## 9. Our own plate models (trained on computer-generated images)

No commercial recogniser and no downloaded dataset: `anpr/training/` draws
Indian number plates itself and trains two small networks on them.

**What the generator draws** (`training/platesynth.py`): standard
`SS 00 X(X) 0000` and BH-series plates weighted towards Maharashtra; HSRP
(IND strip, hologram, border), old-style, commercial yellow, EV green and
rental black plates; one-row and two-row (bike) layouts; several typefaces;
rivets, frames, dealer stickers. Then it damages them the way a gate camera
does: perspective and tilt, loose or clipped crops, dirt and mud, faded paint,
low/high exposure, headlight glare, IR night (grey, noisy), motion and focus
blur, sensor noise, JPEG artefacts, part of the plate hidden. The finder's
scenes add mudguards, tail lights and decoy text (brand names, stickers)
that are *not* plates.

| Model | File | What it does | Size |
|---|---|---|---|
| Plate finder | `models/plate_finder.onnx` | CenterNet-style heatmap over the whole frame (scaled to `finder_width`, default 960 px): plate centre, width, height | ~0.2 M params |
| Plate reader | `models/plate_reader.onnx` | 64×128 grey crop → 11 character slots × (0–9, A–Z, empty). Reads one- and two-row plates in one pass; per-character confidence and top-3 alternatives feed the pipeline's multi-frame voting | ~1.25 M params |

Each `.onnx` has a `.json` next to it with the alphabet and the validation
scores it reached on held-out generated images.

**Select it**: `recognizer.kind: trained` (or `ANPR_RECOGNIZER=trained`).
Options under `recognizer.trained`: `finder_model`, `reader_model` (relative
paths resolve against `anpr/`), `finder_width`, `finder_threshold`,
`whole_frame_plates`, `threads`.

### 9.1 Re-training from scratch (any Linux PC, no GPU needed)

```bash
cd anpr
sudo apt install fonts-dejavu-core fonts-liberation fonts-freefont-ttf
pip install -r requirements-train.txt
python -m training.platesynth                       # writes preview sheets to look at
python -m training.train_reader gen   --out training/data --train 240000 --val 6000
python -m training.train_reader train --data training/data --epochs 10 --out training/runs/reader
python -m training.train_reader export --ckpt training/runs/reader/best.pt --out models/plate_reader.onnx
python -m training.train_finder gen   --out training/data_finder --train 40000 --val 1500
python -m training.train_finder train --data training/data_finder --epochs 10 --out training/runs/finder
python -m training.train_finder export --ckpt training/runs/finder/best.pt --out models/plate_finder.onnx
python -m training.train_reader eval  --model models/plate_reader.onnx --data training/data
```

On a 4-core CPU: data ~25 min, reader ~3 h, finder ~2 h.

### 9.2 The site week: making it accurate on your cameras

The models are good at *generated* plates. Real plates differ in ways a
generator never fully captures (your cameras' exact blur, your lighting, the
local mix of fonts and dirt). One week of real footage fixes most of it:

1. **Install and run.** Cameras mounted and aimed per `CAMERA_SETUP.md`,
   `recognizer.kind: trained`. The system runs normally; workers correct
   misreads in the app as they happen.
2. **Collect crops.** Every event saves a plate crop under the image root
   (`/srv/images/<date>/<gate>/<event>_plate_crop.jpg`). Copy a week's worth:
   ```bash
   mkdir -p ~/crops && find /srv/images -name '*plate_crop.jpg' -newermt '-7 days' -exec cp {} ~/crops/ \;
   ```
3. **Label.** The model pre-labels every crop; a person only fixes the wrong
   ones (about 2–3 seconds per crop; 3,000 crops ≈ 2 hours):
   ```bash
   python tools/label_crops.py ~/crops --suggest --config /srv/anpr/site.yaml   # needs a desktop; Delete = unusable
   ```
4. **Fine-tune the reader** (about 30–60 minutes on the edge server's CPU):
   ```bash
   python -m training.train_reader train --data training/data --real ~/crops/labels.csv --real-dir ~/crops \
       --init training/runs/reader/best.pt --epochs 6 --lr 5e-4 --out training/runs/reader_site
   python -m training.train_reader export --ckpt training/runs/reader_site/best.pt --out models/plate_reader.onnx
   ```
   Keep ~300 labelled crops aside and check them with
   `python -m anpr_service evaluate` (section 5) before and after.
5. **Deploy**: copy the new `plate_reader.onnx` + `.json` into `anpr/models/`
   and `docker compose up -d --build anpr`. Keep the previous file to roll back.

Repeat step 2–5 after a month (and after the first monsoon week) with the
crops the reader was least sure about.
