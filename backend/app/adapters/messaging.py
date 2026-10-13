"""SMS and WhatsApp adapters with a no-op fallback.

* SMS: MSG91 Flow API (DLT-registered templates; template ids via PARK_MSG91_TEMPLATE_*).
* WhatsApp: Meta WhatsApp Business Cloud API, pre-approved template messages.
* NoOp: logs only (used in demo/tests, or when no provider is configured).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)


@dataclass
class SendResult:
    status: str  # SENT | FAILED | SKIPPED
    provider_ref: Optional[str] = None
    error: Optional[str] = None


class MessageSender(ABC):
    channel = "SMS"

    @abstractmethod
    def send(self, to: str, template: str, variables: dict[str, str], text: str) -> SendResult: ...


def _e164_in(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return digits


class NoOpSender(MessageSender):
    def __init__(self, channel: str = "SMS"):
        self.channel = channel
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to, template, variables, text):
        self.sent.append((to, template, text))
        log.info("[%s noop] to=%s template=%s: %s", self.channel, to, template, text)
        return SendResult("SENT", "noop")


class Msg91Sms(MessageSender):
    channel = "SMS"
    url = "https://control.msg91.com/api/v5/flow/"

    def __init__(self, auth_key: str, templates: dict[str, str], timeout: float = 8.0):
        self.auth_key, self.templates = auth_key, templates
        self.client = httpx.Client(timeout=timeout)

    def send(self, to, template, variables, text):
        template_id = self.templates.get(template)
        if not template_id:
            return SendResult("SKIPPED", error=f"no DLT template configured for {template}")
        try:
            r = self.client.post(self.url, headers={"authkey": self.auth_key, "content-type": "application/json"},
                                 json={"template_id": template_id, "short_url": "0",
                                       "recipients": [{"mobiles": _e164_in(to), **variables}]})
            if r.status_code >= 400:
                return SendResult("FAILED", error=r.text[:200])
            return SendResult("SENT", (r.json() or {}).get("message"))
        except httpx.HTTPError as e:
            return SendResult("FAILED", error=str(e))


class MetaWhatsApp(MessageSender):
    channel = "WHATSAPP"

    def __init__(self, token: str, phone_number_id: str, timeout: float = 8.0, language: str = "en"):
        self.url = f"https://graph.facebook.com/v20.0/{phone_number_id}/messages"
        self.token, self.language = token, language
        self.client = httpx.Client(timeout=timeout)

    def send(self, to, template, variables, text):
        params = [{"type": "text", "text": str(v)} for v in variables.values()]
        body = {"messaging_product": "whatsapp", "to": _e164_in(to), "type": "template",
                "template": {"name": f"parking_{template}", "language": {"code": self.language},
                             "components": [{"type": "body", "parameters": params}]}}
        try:
            r = self.client.post(self.url, headers={"Authorization": f"Bearer {self.token}"}, json=body)
            if r.status_code >= 400:
                return SendResult("FAILED", error=r.text[:200])
            msgs = (r.json() or {}).get("messages") or [{}]
            return SendResult("SENT", msgs[0].get("id"))
        except httpx.HTTPError as e:
            return SendResult("FAILED", error=str(e))


@dataclass
class Messenger:
    sms: MessageSender
    whatsapp: MessageSender
    prefer_whatsapp: bool = False
    extra: dict = field(default_factory=dict)


_messenger: Optional[Messenger] = None


def get_messenger() -> Messenger:
    global _messenger
    if _messenger is None:
        s = get_settings()
        sms: MessageSender = NoOpSender("SMS")
        if s.sms_provider == "msg91" and s.msg91_auth_key:
            sms = Msg91Sms(s.msg91_auth_key, {"receipt": s.msg91_template_receipt, "settlement": s.msg91_template_settlement,
                                              "pass_reminder": s.msg91_template_pass_reminder, "otp": s.msg91_template_otp})
        wa: MessageSender = NoOpSender("WHATSAPP")
        if s.whatsapp_provider == "meta" and s.whatsapp_token:
            wa = MetaWhatsApp(s.whatsapp_token, s.whatsapp_phone_number_id)
        _messenger = Messenger(sms=sms, whatsapp=wa, prefer_whatsapp=s.whatsapp_provider == "meta")
    return _messenger


def set_messenger(m: Messenger) -> None:
    global _messenger
    _messenger = m
