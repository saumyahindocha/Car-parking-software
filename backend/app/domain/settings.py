"""Runtime site settings stored in the `settings` table, editable from the admin panel."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import Setting
from .plates import DEFAULT_STATE_CODES

DEFAULTS: dict[str, Any] = {
    "lot_name": "Station Parking",
    "lot_address": "",
    "gstin": "",
    "gst_rate_percent": 0,
    "receipt_footer": "Final charge is calculated on actual time; any difference is adjusted on your next visit.",
    "upi_vpa": "parking@upi",
    "upi_payee_name": "Station Parking",
    # matching
    "approx_tolerance": 1,
    "dedupe_seconds": 60,
    "merge_window_seconds": 10,
    "min_confidence": 0.6,
    # regular-vehicle approx match on an otherwise-unknown plate: confusion-only always; one real
    # edit only when the read is invalid or its confidence is below this (neighbouring plates exist)
    "approx_regular_max_confidence": 0.9,
    "state_codes": list(DEFAULT_STATE_CODES),
    # cash
    "cash_enabled": True,
    "cash_desk_only": False,
    "cash_desk_user_ids": [],
    "cash_limit_paise": 200000,
    "cash_warn_ratio": 0.8,
    # alerts / revenue protection
    "alert_balance_threshold_paise": 2000,
    "unpaid_flag_minutes": 30,
    "to_collect_hours": 6,
    "offline_claim_review_hours": 24,
    # passes
    "pass_one_open_session": False,
    "pass_candidate_visits": 8,
    "pass_reminder_days": [5, 1],
    "pass_expiry_warn_days": 5,
    # durations offered in the worker app (minutes; 1440 = full day)
    "duration_buttons": [120, 240, 480, 720, 1440],
    # retention (days)
    "retention_plate_images_days": 90,
    "retention_full_frames_days": 30,
    "retention_events_days": 365,
    # watchdog
    "camera_stall_seconds": 60,
    "internet_down_alert_seconds": 300,
}


def get_setting(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is not None:
        return row.value
    return DEFAULTS.get(key)


def all_settings(db: Session) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for row in db.query(Setting).all():
        out[row.key] = row.value
    return out


def set_setting(db: Session, key: str, value: Any) -> None:
    if key not in DEFAULTS and not key.startswith("_"):
        raise KeyError(f"unknown setting {key}")
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.flush()
