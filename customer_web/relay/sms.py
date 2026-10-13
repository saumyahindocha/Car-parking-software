"""OTP SMS adapters: MSG91 Flow API (DLT template) and a no-op logger for development/tests."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

import httpx

log = logging.getLogger(__name__)


def mask_phone(phone: str) -> str:
    return "******" + phone[-4:] if len(phone) >= 4 else "****"


class SmsSender(ABC):
    name = "abstract"

    @abstractmethod
    def send_otp(self, phone: str, code: str) -> bool: ...


class NoOpSms(SmsSender):
    """Keeps sent messages in memory (tests / demo); logs only a masked number, never the code."""

    name = "noop"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_otp(self, phone: str, code: str) -> bool:
        self.sent.append((phone, code))
        log.info("[sms noop] OTP to %s", mask_phone(phone))
        return True

    def last_code(self, phone: Optional[str] = None) -> Optional[str]:
        for p, c in reversed(self.sent):
            if phone is None or p == phone:
                return c
        return None


class Msg91Sms(SmsSender):
    """MSG91 Flow API. The template must be DLT-registered, e.g.
    'Your OTP for Station Parking is ##otp##. Valid for 5 minutes. Do not share it. -PARKNG'."""

    name = "msg91"
    url = "https://control.msg91.com/api/v5/flow/"

    def __init__(self, auth_key: str, template_id: str, timeout: float = 8.0,
                 client: Optional[httpx.Client] = None):
        if not auth_key or not template_id:
            raise ValueError("MSG91 needs RELAY_MSG91_AUTH_KEY and RELAY_MSG91_TEMPLATE_OTP")
        self.auth_key, self.template_id = auth_key, template_id
        self.client = client or httpx.Client(timeout=timeout)

    def send_otp(self, phone: str, code: str) -> bool:
        try:
            r = self.client.post(self.url, headers={"authkey": self.auth_key, "content-type": "application/json"},
                                 json={"template_id": self.template_id, "short_url": "0",
                                       "recipients": [{"mobiles": "91" + phone[-10:], "otp": code}]})
        except httpx.HTTPError as exc:
            log.warning("msg91 send failed: %s", type(exc).__name__)
            return False
        if r.status_code >= 400:
            log.warning("msg91 rejected OTP (%s)", r.status_code)
            return False
        return True


def make_sms(provider: str, auth_key: str = "", template_id: str = "") -> SmsSender:
    if provider == "msg91":
        return Msg91Sms(auth_key, template_id)
    return NoOpSms()
