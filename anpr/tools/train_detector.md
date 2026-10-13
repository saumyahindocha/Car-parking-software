# Training the vehicle + plate detector

`LocalOnnxRecognizer` needs one ONNX detector that outputs vehicles (BIKE,
CAR, OTHER) and number plates, or a vehicle detector plus a second-stage
plate detector (`recognizer.onnx.plate_detector_model`, run on vehicle
crops). OCR is a separate model (`tools/train_ocr.py`).

## 1. Choose an architecture with a licence you can ship

| Option | Licence | Notes |
|---|---|---|
| **YOLOX** (Megvii) | Apache-2.0 | Recommended default. Export with `--decode_in_inference`; output `(1, N, 5+nc)` = `detector_format: yolov5`. |
| **RT-DETR** (lyuwenyu/RT-DETR, PaddleDetection) | Apache-2.0 | Higher accuracy, no NMS needed (keep `nms_iou` at 0.5, harmless). Export postprocessed boxes as `(1, 4+nc, N)` or adapt `yolo.decode`. |
| YOLOv5 / YOLOv8 / YOLO11 (Ultralytics) | **AGPL-3.0** | Using or distributing it in a closed deployment requires an **Ultralytics Enterprise licence**. Its exports (`(1, 4+nc, 8400)`) work as-is with `detector_format: yolov8`. |
| NVIDIA TAO LPDNet / TrafficCamNet | NVIDIA model licence | Free for use on NVIDIA GPUs; check the EULA before redistributing. |

## 2. Collect data from the site cameras

* Record 2–3 weeks from the pilot gate: day, dusk, night (IR), rain, and the
  morning and evening peaks. Side-by-side pairs, partial occlusion,
  two-line plates, HSRP and older fonts, yellow commercial plates, and
  (later) cars.
* Sample frames at 2–5 fps around capture-line crossings. The
  `full_frame` images the service already stores make a good first pool.
* Aim for about 5k frames at first, and more for night.

## 3. Label

* Classes: `0 BIKE`, `1 CAR`, `2 OTHER`, `3 PLATE`. This is the default
  `detector_classes` map; change the config if your order differs.
* Box the **whole vehicle including the rider**, and the plate tightly,
  border included.
* Label plates even when they are unreadable (dirty, far away). The
  detector should find them, and the pipeline's quality gate and voting
  decide whether to read them.
* Tools: CVAT (MIT) or Label Studio (Apache-2.0). Export to YOLO txt or
  COCO JSON.

## 4. Train (YOLOX example)

```bash
git clone https://github.com/Megvii-BaseDetection/YOLOX && cd YOLOX && pip install -v -e .
# exps/anpr_yolox_s.py: num_classes=4, input_size=(640, 640), data_dir -> COCO export
python tools/train.py -f exps/anpr_yolox_s.py -d 1 -b 32 --fp16 -o -c yolox_s.pth
python tools/export_onnx.py -f exps/anpr_yolox_s.py -c YOLOX_outputs/anpr_yolox_s/best_ckpt.pth \
       --output-name vehicle_plate_detector.onnx --decode_in_inference
```

Augmentation must keep plates legible. Use mosaic, HSV jitter, scale 0.5–1.5
and motion blur. Avoid heavy rotation. Rear plates are near-frontal.

## 5. Configure and evaluate

```yaml
recognizer:
  kind: onnx
  onnx:
    detector_model: /models/vehicle_plate_detector.onnx
    detector_format: yolov5          # YOLOX with decode_in_inference
    detector_input_size: 640
    detector_classes: {0: BIKE, 1: CAR, 2: OTHER, 3: PLATE}
    ocr_model: /models/plate_ocr_crnn.onnx
```

Run `python -m anpr_service evaluate --labels <labelled clips> --config site.yaml`
and compare per-camera exact / approximate accuracy and read rate, including
the side-by-side subset, against the rollout target (≥95% day, ≥90% night).

## 6. Deploy on the edge GPU

* `onnxruntime-gpu` picks `TensorrtExecutionProvider`, then
  `CUDAExecutionProvider`, then CPU (see `providers`). The first start with
  TensorRT builds engines and takes minutes. Set
  `ORT_TENSORRT_ENGINE_CACHE_ENABLE=1` and `ORT_TENSORRT_CACHE_PATH=/models/trt-cache`
  to cache them. FP16: `ORT_TENSORRT_FP16_ENABLE=1`.
* Budget on an RTX 3060-class card: YOLOX-s at 640 is about 4–6 ms per frame and
  CRNN is under 1 ms per row. Six streams at 25 fps fit comfortably. If the GPU
  saturates, raise `pipeline.process_every_n` to 2 for the overview-free ANPR
  streams.
