"""Frame sources: RTSP (FFmpeg or GStreamer via OpenCV) with reconnect, and file replay."""

from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

from .config import IngestConfig
from .metrics import RateMeter, now_ms

log = logging.getLogger(__name__)


@dataclass
class FramePacket:
    frame: np.ndarray
    ts_ms: int  # event time of the frame (epoch ms)
    grab_wall_ms: int  # wall clock when the frame was obtained (latency base)
    index: int


class FrameSource(ABC):
    fps: float = 25.0

    @abstractmethod
    def read(self, timeout_s: float = 1.0) -> FramePacket | None:
        """Next frame, or None on timeout / end of stream (check ``eos``)."""

    @property
    def eos(self) -> bool:
        return False

    @property
    def stream_ok(self) -> bool:
        return True

    @property
    def queue_depth(self) -> int:
        return 0

    @property
    def measured_fps(self) -> float:
        return self.fps

    @property
    def last_frame_ts_ms(self) -> int:
        return 0

    def close(self) -> None:  # noqa: B027
        pass


class ReplaySource(FrameSource):
    """Reads a video file.  Timestamps are ``start_epoch_ms + index / fps``.

    ``realtime=True`` paces frames at the file's frame rate (like a camera);
    otherwise frames are delivered as fast as they can be processed.  All
    cameras of a replay share ``start_epoch_ms`` so their timelines line up.
    """

    def __init__(self, path: str, start_epoch_ms: int | None = None, loop: bool = False,
                 realtime: bool = False, fps_override: float | None = None) -> None:
        self.path = path
        self.loop = loop
        self.realtime = realtime
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise FileNotFoundError(f"cannot open replay file {path}")
        fps = fps_override or self._cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.fps = float(fps if fps > 0 else 25.0)
        self.start_epoch_ms = start_epoch_ms if start_epoch_ms is not None else now_ms()
        self._index = 0
        self._eos = False
        self._t0 = time.monotonic()
        self._last_ts = 0

    @property
    def eos(self) -> bool:
        return self._eos

    @property
    def last_frame_ts_ms(self) -> int:
        return self._last_ts

    def read(self, timeout_s: float = 1.0) -> FramePacket | None:
        if self._eos:
            return None
        ok, frame = self._cap.read()
        if not ok:
            if self.loop:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self._cap.read()
            if not ok:
                self._eos = True
                return None
        ts = self.start_epoch_ms + int(round(self._index * 1000.0 / self.fps))
        if self.realtime:
            due = self._t0 + self._index / self.fps
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        pkt = FramePacket(frame=frame, ts_ms=ts, grab_wall_ms=now_ms(), index=self._index)
        self._index += 1
        self._last_ts = ts
        return pkt

    def close(self) -> None:
        self._cap.release()


class RtspSource(FrameSource):
    """Live camera via OpenCV (FFmpeg backend by default, or a GStreamer pipeline).

    A grabber thread reads continuously into a bounded queue (oldest frames
    are dropped if processing falls behind, and counted), reconnecting with
    exponential backoff when the stream stalls or errors.
    """

    def __init__(self, url: str | None, cfg: IngestConfig, gstreamer_pipeline: str | None = None,
                 name: str = "camera") -> None:
        if not url and not gstreamer_pipeline:
            raise ValueError("RtspSource needs rtsp_url or gstreamer_pipeline")
        self.url = url
        self.pipeline = gstreamer_pipeline
        self.cfg = cfg
        self.name = name
        self.fps = 25.0
        self._q: deque[FramePacket] = deque(maxlen=max(2, cfg.queue_size))
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._ok = False
        self._index = 0
        self._last_ts = 0
        self.dropped = 0
        self.reconnects = 0
        self._rate = RateMeter(10.0)
        self._thread = threading.Thread(target=self._run, name=f"grab-{name}", daemon=True)
        self._thread.start()

    @property
    def stream_ok(self) -> bool:
        return self._ok

    @property
    def queue_depth(self) -> int:
        return len(self._q)

    @property
    def measured_fps(self) -> float:
        return self._rate.rate()

    @property
    def last_frame_ts_ms(self) -> int:
        return self._last_ts

    def _open(self) -> cv2.VideoCapture:
        if self.pipeline:
            return cv2.VideoCapture(self.pipeline, cv2.CAP_GSTREAMER)
        if self.cfg.api_preference == "ffmpeg":
            os.environ.setdefault(
                "OPENCV_FFMPEG_CAPTURE_OPTIONS",
                f"rtsp_transport;{self.cfg.rtsp_transport}|stimeout;{int(self.cfg.read_timeout_s * 1e6)}",
            )
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        elif self.cfg.api_preference == "gstreamer":
            cap = cv2.VideoCapture(self.url, cv2.CAP_GSTREAMER)
        else:
            cap = cv2.VideoCapture(self.url)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        except cv2.error:
            pass
        return cap

    def _run(self) -> None:
        backoff = self.cfg.reconnect_initial_s
        while not self._stop.is_set():
            cap = self._open()
            if not cap.isOpened():
                self._ok = False
                log.warning("camera %s: cannot open stream; retry in %.1fs", self.name, backoff)
                self._stop.wait(backoff)
                backoff = min(self.cfg.reconnect_max_s, backoff * 2)
                self.reconnects += 1
                continue
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps and 1 < fps < 121:
                self.fps = float(fps)
            log.info("camera %s: stream opened (%.1f fps)", self.name, self.fps)
            backoff = self.cfg.reconnect_initial_s
            last_ok = time.monotonic()
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    if time.monotonic() - last_ok > self.cfg.read_timeout_s:
                        log.warning("camera %s: no frames for %.1fs; reconnecting", self.name, self.cfg.read_timeout_s)
                        break
                    time.sleep(0.01)
                    continue
                last_ok = time.monotonic()
                self._ok = True
                ts = now_ms()
                pkt = FramePacket(frame=frame, ts_ms=ts, grab_wall_ms=ts, index=self._index)
                self._index += 1
                self._last_ts = ts
                self._rate.tick()
                with self._cond:
                    if len(self._q) == self._q.maxlen:
                        self.dropped += 1
                    self._q.append(pkt)
                    self._cond.notify()
            self._ok = False
            cap.release()
            self.reconnects += 1
            self._stop.wait(backoff)
            backoff = min(self.cfg.reconnect_max_s, backoff * 2)

    def read(self, timeout_s: float = 1.0) -> FramePacket | None:
        with self._cond:
            if not self._q:
                self._cond.wait(timeout_s)
            if not self._q:
                return None
            return self._q.popleft()

    def close(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self._thread.join(timeout=5)
