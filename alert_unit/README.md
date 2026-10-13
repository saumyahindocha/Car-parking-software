# Exit alert unit (Raspberry Pi 5)

One unit per exit gate: a Raspberry Pi 5 drives a 24–32" high-brightness display and a 24 V
red/green tower light with buzzer through a relay board. It subscribes to the edge backend's
`exit` events for its gate and shows (spec Section 10):

| Exit state (from backend) | Tower light | Screen |
|---|---|---|
| `GREEN` | green | "Thank you" + plate. Pass holders: "Pass valid till 29 Sep 2026", and "Pass expires in N days" when N ≤ `pass_expiry_warn_days` (5) |
| `RED` (unpaid / balance above threshold) | **red** for `red_hold_s` (5 s) + short beep | "Payment due", plate, amount due, plate image |
| `NEUTRAL` (unread / unknown plate) | green (never red) | the plate that was read or "—", plus plate image |
| two vehicles at once | red if either is red | both cards side by side (`max_slots`: 2, up to 3) |
| edge link lost | **stays green**, traffic is never stopped | small amber "OFFLINE - reconnecting" in the corner; reconnects with exponential backoff (1 s → 30 s) |

When the slots are full, a new exit replaces the oldest *green* card first, so a red alert is not
pushed off the screen before the guard has seen it. The backend already creates the alert row and
notifies guard phones; this unit only displays.

## Layout

```
exit_alert/
  config.py          defaults < YAML < ALERT_* env vars < CLI flags
  display_state.py   pure logic: message -> card, slots, hold/red timeout, pass text, light colour
  relay.py           LightController (colour -> relay channels, timed buzzer, fail-safe green),
                     gpiozero backend, mock backend, Pi auto-detection
  net.py             WsLink (websocket-client, ping, dead-link detection, backoff), ImageCache
                     (GET /api/images/... with X-Device-Key), heartbeat
  ui.py              pygame renderer (fullscreen, big high-contrast fonts, ₹ glyph via DejaVu)
  demo.py            fake exits for --demo
  app.py             threads + UI loop; CLI
deploy/alert-unit.service   systemd unit (console kiosk via KMS/DRM)
install.sh                  one-shot installer for the Pi
config.example.yaml
```

## Backend contract used

* `ws://<edge>:8000/ws/device?key=<DEVICE_KEY>&gate_id=G2` – frames
  `{"topic":"exit","data":{gate_id,event_id,ts,state,plate,display_plate,vehicle_class,session_id,plate_image,frame_image,amount_due_paise,pass_valid_till,pass_days_left},"ts":...}`.
  The unit sends `"ping"` every `ping_interval_s`; the server answers `{"topic":"pong"}`. If nothing
  arrives for 3 ping intervals the link is declared dead and re-opened.
* `GET <edge><plate_image>` with header `X-Device-Key` (only for RED/NEUTRAL cards; LRU-cached).
* `POST /api/devices/heartbeat` (header `X-Device-Key`) every 15 s:
  `{"device_id":"AU-G2","kind":"ALERT_UNIT","gate_id":"G2","metrics":{"ws_connected":true,"display_ok":true,"uptime_s":..,"cpu_temp_c":..,"exits_seen":..,"reds_seen":..,"light":"GREEN"}}`.
  The dashboard marks the unit offline after 60 s without a heartbeat.

## Try it on a laptop

```bash
cd alert_unit
pip install -r requirements-dev.txt
python -m exit_alert --demo --windowed          # fake exits every 4 s: green, pass, red, pairs, unread
python -m exit_alert --demo --screenshot shot.png --run-seconds 1   # off-screen render
python -m exit_alert --windowed --mock-gpio --edge-url http://localhost:8000 --device-key dev-device-key --gate G2
```

With the edge backend running in demo mode (`PARK_DEMO_MODE=true`), drive real exits with
`POST /api/demo/event {"gate_id":"G2","direction":"OUT","plate":"MH12AB1234"}`.
Esc or Q quits the windowed UI.

## Tests

```bash
cd alert_unit && python -m pytest -q     # headless (SDL_VIDEODRIVER=dummy), no Pi needed
```

Covers slot management and eviction, red timeout, pass text, relay controller (mock backend and
gpiozero's `MockFactory`, active-low, NC wiring, buzzer), WebSocket reconnect/backoff with a fake
socket, dead-link detection, image fetch, heartbeat, config precedence, the renderer and the demo CLI.

## Hardware and wiring

Hardware doc: 3-colour 24 V LED tower light with buzzer, 2-channel relay module, 24 V PSU.

```
Pi 5 GPIO (BCM)            relay board                 24 V side
  5V  (pin 2) ------------ VCC
  GND (pin 6) ------------ GND
  GPIO17 (pin 11) -------- IN1  -> CH1 COM/NO ---- red lamp    (+24 V via COM)
  GPIO27 (pin 13) -------- IN2  -> CH2 COM/NO ---- green lamp
  GPIO22 (pin 15) -------- IN3  -> CH3 COM/NO ---- buzzer      (optional 3rd channel)
```

* With the specified **2-channel** board, set `pins.buzzer: null` (no beep), or use a 4-channel board
  (recommended, same price class) and wire the buzzer to CH3. Do **not** wire the buzzer in parallel
  with red: it would sound for the whole 5 s.
* Most opto-isolated boards are active-low (`active_low: true`). If yours switches on a HIGH input,
  set it to `false`.
* **Fail-safe green (recommended):** wire the green lamp through CH2's **NC** contact and set
  `green_on_nc: true`. Then a crashed, rebooting or unpowered Pi leaves the lamp green, and red can
  only appear while the software is running and actively showing a RED card.
* On `SIGTERM`/shutdown the software switches red and buzzer off and green on before releasing the pins.

## Install on the Pi (Raspberry Pi OS Bookworm 64-bit)

Use **Raspberry Pi OS Lite** for a console kiosk (no desktop to crash or show pop-ups):

```bash
scp -r alert_unit pi@alert-g2.local:~
ssh pi@alert-g2.local
cd alert_unit
sudo ./install.sh --edge-url http://192.168.10.10:8000 --key <PARK_DEVICE_API_KEY> --gate G2 --ntp 192.168.10.10
journalctl -u alert-unit -f
```

`install.sh` installs the apt packages (`python3-pygame python3-gpiozero python3-lgpio python3-yaml
python3-websocket fonts-dejavu-core`), creates the `alertunit` system user (groups gpio, video,
render, input), copies the app to `/opt/alert-unit` with a venv (`--system-site-packages`), writes
`/etc/alert-unit/config.yaml`, disables console blanking, points NTP at the edge server and enables
`alert-unit.service` (`Restart=always`). The service draws straight to HDMI with
`SDL_VIDEODRIVER=kmsdrm`.

Kiosk notes:

* Set the HDMI mode for the panel in `/boot/firmware/config.txt` if it is not auto-detected
  (`hdmi_force_hotplug=1`). Turn off the panel's own power saving / input auto-search.
* Give the Pi a static DHCP lease; the edge URL in the config is an IP address so DNS is not needed.
* **Desktop image instead of Lite:** disable the service (`sudo systemctl disable --now alert-unit`)
  and autostart it in the Wayland session instead: add
  `/opt/alert-unit/venv/bin/python -m exit_alert -c /etc/alert-unit/config.yaml &` to
  `~/.config/labwc/autostart`, enable desktop auto-login in `raspi-config`, and turn off screen
  blanking there.
* Read-only root filesystem (overlay FS in `raspi-config`) is a good idea once configured; the app
  writes nothing to disk.
* Logs never contain full plates beyond what is on screen; the journal is kept on the SD card with
  the default size limits.

## Configuration

See `config.example.yaml`. Environment overrides: `ALERT_EDGE_URL`, `ALERT_DEVICE_KEY`,
`ALERT_GATE_ID`, `ALERT_DEVICE_ID`, `ALERT_MOCK_GPIO`, nested `ALERT_PINS__RED`,
`ALERT_TIMING__RED_HOLD_S`, `ALERT_DISPLAY__MODE`, ... (a systemd `EnvironmentFile=/etc/alert-unit/env`
is honoured). CLI flags: `--edge-url --device-key --gate --device-id --demo --mock-gpio --windowed
--headless --screenshot PNG --run-seconds N -v`.

`timing.pass_expiry_warn_days` must match the edge setting of the same name (the backend sends
`pass_days_left` but not the threshold).
