"""Pure display logic: exit messages -> cards in slots -> what the screen and the tower light show.

No I/O here, and time is always passed in (monotonic seconds), so everything is unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

GREEN, RED, NEUTRAL = "GREEN", "RED", "NEUTRAL"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def rupees(paise: Optional[int]) -> str:
    """12345 -> '₹123' ; 12350 -> '₹123.50' (Indian digit grouping for large amounts)."""
    p = max(0, int(paise or 0))
    whole, frac = divmod(p, 100)
    s = str(whole)
    if len(s) > 3:  # 1,23,456 grouping
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"₹{s}" if frac == 0 else f"₹{s}.{frac:02d}"


def pretty_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso[:10])
    except (TypeError, ValueError):
        return str(iso)
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def pass_lines(valid_till: Optional[str], days_left: Optional[int], warn_days: int) -> list[str]:
    """Section 10: 'Pass valid till <date>' and, when close to expiry, 'Pass expires in N days'."""
    if not valid_till:
        return []
    lines = [f"Pass valid till {pretty_date(valid_till)}"]
    if days_left is not None and days_left <= warn_days:
        if days_left <= 0:
            lines.append("Pass expires today - please renew")
        elif days_left == 1:
            lines.append("Pass expires in 1 day - please renew")
        else:
            lines.append(f"Pass expires in {days_left} days")
    return lines


@dataclass
class Card:
    """One exiting vehicle on screen."""

    key: str
    state: str
    plate: str
    display_plate: str
    vehicle_class: str
    amount_due_paise: int
    plate_image: Optional[str]
    frame_image: Optional[str]
    pass_valid_till: Optional[str]
    pass_days_left: Optional[int]
    shown_at: float
    expires_at: float

    @property
    def is_red(self) -> bool:
        return self.state == RED

    def headline(self) -> str:
        if self.state == RED:
            return "Payment due"
        return "Thank you"

    def lines(self, warn_days: int) -> list[str]:
        if self.state == RED:
            return [f"Amount due {rupees(self.amount_due_paise)}", "Please pay at the counter"]
        if self.state == NEUTRAL:
            return ["Drive safely"]
        pl = pass_lines(self.pass_valid_till, self.pass_days_left, warn_days)
        return pl or ["Drive safely"]


def card_from_message(data: dict[str, Any], now: float, *, red_hold: float, green_hold: float,
                      neutral_hold: float) -> Card:
    state = str(data.get("state") or NEUTRAL).upper()
    if state not in (GREEN, RED, NEUTRAL):
        state = NEUTRAL
    plate = data.get("plate") or ""
    disp = data.get("display_plate") or plate or "—"
    hold = {RED: red_hold, GREEN: green_hold, NEUTRAL: neutral_hold}[state]
    key = str(data.get("event_id") if data.get("event_id") is not None else f"{plate}@{data.get('ts')}")
    days = data.get("pass_days_left")
    return Card(key=key, state=state, plate=plate, display_plate=disp, vehicle_class=data.get("vehicle_class") or "",
                amount_due_paise=int(data.get("amount_due_paise") or 0), plate_image=data.get("plate_image"),
                frame_image=data.get("frame_image"), pass_valid_till=data.get("pass_valid_till"),
                pass_days_left=int(days) if days is not None else None, shown_at=now, expires_at=now + hold)


@dataclass
class Effects:
    """Side effects the caller should perform after a model update."""

    buzz: bool = False
    fetch_images: list[str] = field(default_factory=list)


@dataclass
class DisplayModel:
    """Keeps up to `max_slots` recent exits on screen (side by side), with per-state hold times.

    * RED cards are held `red_hold` seconds; while any RED card is on screen the light is RED.
    * When the slots are full a new exit replaces the oldest non-RED card first (a RED alert is
      never pushed off the screen by a green one before it has been seen), else the oldest card.
    * Duplicate deliveries of the same event update the card in place.
    """

    max_slots: int = 2
    red_hold: float = 5.0
    green_hold: float = 6.0
    neutral_hold: float = 6.0
    warn_days: int = 5
    cards: list[Card] = field(default_factory=list)
    connected: bool = False
    last_message_at: Optional[float] = None

    def on_exit(self, data: dict[str, Any], now: float) -> Effects:
        self.expire(now)
        card = card_from_message(data, now, red_hold=self.red_hold, green_hold=self.green_hold,
                                 neutral_hold=self.neutral_hold)
        self.last_message_at = now
        eff = Effects()
        for i, c in enumerate(self.cards):
            if c.key == card.key:
                if card.is_red and not c.is_red:
                    eff.buzz = True
                self.cards[i] = card
                eff.fetch_images = self._images(card)
                return eff
        if len(self.cards) >= self.max_slots:
            victims = [c for c in self.cards if not c.is_red] or list(self.cards)
            oldest = min(victims, key=lambda c: c.shown_at)
            self.cards.remove(oldest)
        self.cards.append(card)
        eff.buzz = card.is_red
        eff.fetch_images = self._images(card)
        return eff

    @staticmethod
    def _images(card: Card) -> list[str]:
        # the plate crop is what the guard needs; only fetched for RED / NEUTRAL cards
        if card.state in (RED, NEUTRAL) and card.plate_image:
            return [card.plate_image]
        return []

    def expire(self, now: float) -> None:
        self.cards = [c for c in self.cards if c.expires_at > now]

    def visible(self, now: float) -> list[Card]:
        """Cards to draw, left to right in arrival order."""
        self.expire(now)
        return sorted(self.cards, key=lambda c: c.shown_at)

    def light(self, now: float) -> str:
        """Tower light colour. Never RED unless a RED card is showing; a lost connection stays GREEN."""
        self.expire(now)
        return RED if any(c.is_red for c in self.cards) else GREEN

    def red_remaining(self, now: float) -> float:
        reds = [c.expires_at - now for c in self.cards if c.is_red and c.expires_at > now]
        return max(reds) if reds else 0.0

    def status_text(self) -> Optional[str]:
        """Small status line for the corner of the screen (None when all is well)."""
        if not self.connected:
            return "OFFLINE - reconnecting"
        return None
