# Camera setup and tuning

Applies to the 2 gates × (2 rear-facing ANPR cameras + 1 overview camera) layout. Read with
`docs/ANPR.md` (pipeline and configuration keys) and the hardware requirements document.

## 1. Why rear plates
On Indian two-wheelers the rear plate is the one reliably present and at a consistent height; a
rear-facing camera also avoids headlight glare at night. The same rear plate works for cars.

## 2. Mounting geometry (per ANPR camera)

| Parameter | Target | Why |
|---|---|---|
| Height | 2.5–3.0 m on a galvanised pole beside the gate (never in the carriageway) | A car later does not hide the bike behind it; pole does not block cars |
| Distance behind the capture line | 5–7 m | Plate large enough, several frames per vehicle |
| Vertical angle to the plate | < 30° | Characters not foreshortened |
| Horizontal angle to the plate | < 20° | Two-line plates stay legible |
| Coverage | Left camera: left half of the gate; right camera: right half | Two bikes side by side are normal |
| Overlap | ≈ 30 % of the gate width in the middle | A bike riding the centre line is seen by both; the aggregator merges the two reads |
| Target plate width on screen | ≥ 130–150 px (4 MP, 2560×1440) | OCR accuracy on two-line plates |
| Shutter | 1/1000 s or faster (1/2000 s preferred) | Freeze bikes at 10–20 km/h |
| IR | 850 nm on at night; white LED flood over the capture zone | Plate readable, overview usable |
| WDR | On (≥ 120 dB) | Sun behind riders at the evening peak |
| Stream | H.264/H.265 main stream, 25 fps, CBR 6–8 Mbps | Stable decode latency |

Overview camera: 2.8 mm wide angle, whole gate in view, for context and dispute evidence.

## 3. Capture line and region of interest
* Draw the **capture line** across the lane where plates are sharpest and largest — normally
  1–2 m past the rumble strip, where bikes have slowed to 10–15 km/h.
* The **ROI** polygon covers the lane half the camera is responsible for plus the overlap band;
  exclude footpaths and the opposite lane so pedestrians and parked bikes do not create tracks.
* The **IN vector** is the image-space direction of travel for an entering vehicle (e.g. `[0, 1]`
  when entering bikes move down the image). Crossing the line against it is `OUT`; on a one-way
  gate that becomes a `WRONG_WAY` event.
* Edit per camera in Dashboard → Configuration → Gates & cameras (or `deploy/config/site.yaml`);
  the ANPR service reloads configuration from the backend.

## 4. Tuning procedure (pilot gate first)
1. Mount, focus and set shutter/IR/WDR per the table. Record 30 min of daytime and 30 min of night
   traffic, including two bikes side by side and two-line plates.
2. Run the evaluator on the labelled clips: `python -m anpr_service evaluate --labels <dir>`
   (per-camera exact match, approximate-match rate, read rate, side-by-side subset).
3. Adjust one thing at a time: focal length for plate width, angle for skew, shutter for blur,
   capture-line position for the sharpest frames, ROI for spurious tracks.
4. Targets before approximate matching: **≥ 95 % read rate in daylight, ≥ 90 % at night**.
   The dashboard's ANPR accuracy report (per camera: read rate, approximate-match rate, manual
   correction rate) tracks it continuously after go-live.
5. Run the pilot gate for two weeks in parallel with the current system; then copy settings to
   the second gate and re-verify.

## 5. When cars are enabled
* Car rear plates sit ~0.5–0.8 m higher than bike plates and are single-line: re-check that the
  capture line is still in the sharpest band for both, and that the vertical angle stays < 30°.
* Re-check focal length: a car plate is wider (500 mm vs ~200 mm bike plates) — make sure it is not
  clipped at the frame edge in the overlap band.
* Review the rumble strip height; optionally add one front-facing camera per gate for a second
  (front-plate) read of cars.
* Switch on the CAR class and review its tariff/pass prices in the dashboard. No code change.
