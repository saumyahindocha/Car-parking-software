"""One-time passwords: 6 digits, 5 minute expiry, max attempts, rate limited, HMAC-hashed at rest."""
from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .config import Settings
from .db import utcnow
from .models import OtpCode
from .security import keyed_hash, rate_hit
from .sms import SmsSender


class OtpError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Issued:
    otp_id: int
    expires_in_s: int


def phone_key(settings: Settings, phone: str) -> str:
    return keyed_hash(settings.secret_key, "phone", phone)


def _code_hash(settings: Settings, otp_id: int, phone: str, code: str) -> str:
    return keyed_hash(settings.secret_key, "otp", str(otp_id), phone, code)


def issue(db: Session, settings: Settings, sms: SmsSender, phone: str, ip: str) -> Issued:
    if not rate_hit(db, settings.secret_key, "otp-ip", ip, settings.otp_per_ip, settings.otp_per_ip_window_s):
        raise OtpError("rate_ip", "Too many OTP requests from this network. Please try again later.")
    if not rate_hit(db, settings.secret_key, "otp-phone", phone, settings.otp_per_phone,
                    settings.otp_per_phone_window_s):
        raise OtpError("rate_phone", "Too many OTPs sent to this number. Please wait 15 minutes and try again.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = utcnow()
    row = OtpCode(phone_key=phone_key(settings, phone), code_hash="-", created_at=now,
                  expires_at=now + timedelta(seconds=settings.otp_ttl_s))
    db.add(row)
    db.flush()
    row.code_hash = _code_hash(settings, row.id, phone, code)
    if not sms.send_otp(phone, code):
        raise OtpError("sms_failed", "We could not send the SMS. Please try again in a minute.")
    db.flush()
    return Issued(row.id, settings.otp_ttl_s)


def verify(db: Session, settings: Settings, otp_id: int, phone: str, code: str) -> None:
    row: Optional[OtpCode] = db.get(OtpCode, otp_id)
    now = utcnow()
    if row is None or row.phone_key != phone_key(settings, phone) or row.used_at is not None:
        raise OtpError("invalid", "This OTP is no longer valid. Please request a new one.")
    if row.expires_at < now:
        raise OtpError("expired", "The OTP has expired. Please request a new one.")
    if row.attempts >= settings.otp_max_attempts:
        raise OtpError("attempts", "Too many wrong attempts. Please request a new OTP.")
    code = "".join(c for c in (code or "") if c.isdigit())
    if not hmac.compare_digest(row.code_hash, _code_hash(settings, row.id, phone, code)):
        row.attempts += 1
        raise OtpError("wrong", "Incorrect OTP. Please check the SMS and try again.")
    row.used_at = now


def purge(db: Session) -> None:
    db.execute(delete(OtpCode).where(OtpCode.expires_at < utcnow() - timedelta(days=1)))
