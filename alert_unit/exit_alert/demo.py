"""`--demo`: feeds fake exit events locally (no edge server needed)."""
from __future__ import annotations

import itertools
import random
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterator, Optional

PLATES = ["MH12AB1234", "MH14CD5678", "MH12EF9012", "MH43GH3456", "KA01JK7890", "MH12LM2345", "GJ05NP6789",
          "MH14QR0123", "MH12ST4567", "DL3CUV8901"]


def _display(p: str) -> str:
    import re

    m = re.match(r"^([A-Z]{2})([0-9]{1,2})([A-Z]{0,3})([0-9]{4})$", p)
    return " ".join(g for g in m.groups() if g) if m else p


def scenarios(gate_id: str = "G2", today: Optional[date] = None, rng: Optional[random.Random] = None) -> Iterator[list[dict]]:
    """Endless batches of exit messages; a batch with two entries is a side-by-side pair."""
    rng = rng or random.Random(7)
    today = today or date.today()
    counter = itertools.count(1)

    def msg(state: str, plate: Optional[str], **kw) -> dict:
        n = next(counter)
        d = {"gate_id": gate_id, "event_id": 900000 + n, "ts": datetime.now(timezone.utc).isoformat(), "state": state,
             "plate": plate, "display_plate": _display(plate) if plate else "—", "vehicle_class": "BIKE",
             "session_id": 5000 + n if plate else None, "plate_image": f"demo:{plate}" if plate else None,
             "frame_image": None, "amount_due_paise": 0, "pass_valid_till": None, "pass_days_left": None}
        d.update(kw)
        return d

    while True:
        a, b, c, d, e = rng.sample(PLATES, 5)
        yield [msg("GREEN", a)]
        yield [msg("GREEN", b, pass_valid_till=(today + timedelta(days=21)).isoformat(), pass_days_left=21)]
        yield [msg("RED", c, amount_due_paise=rng.choice([2000, 3000, 4500, 12000]))]
        yield [msg("GREEN", d), msg("GREEN", e, pass_valid_till=(today + timedelta(days=3)).isoformat(),
                                    pass_days_left=3)]
        yield [msg("NEUTRAL", None)]
        yield [msg("GREEN", a), msg("RED", b, amount_due_paise=2000)]
        yield [msg("NEUTRAL", "MH12XX0000", display_plate="MH 12 XX 0000")]


class DemoFeeder:
    def __init__(self, emit: Callable[[dict], None], gate_id: str = "G2", interval: float = 4.0,
                 pair_gap: float = 0.3):
        self.emit, self.gate_id, self.interval, self.pair_gap = emit, gate_id, interval, pair_gap
        self.stop_event = threading.Event()

    def run(self) -> None:
        for batch in scenarios(self.gate_id):
            for i, m in enumerate(batch):
                if i:
                    self.stop_event.wait(self.pair_gap)
                self.emit(m)
            if self.stop_event.wait(self.interval):
                return

    def stop(self) -> None:
        self.stop_event.set()
