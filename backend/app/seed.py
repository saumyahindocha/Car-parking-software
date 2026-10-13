"""Seed data so the system runs out of the box (python -m app.seed [--demo])."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, get_engine, session_scope
from .models import Camera, Gate, PassType, Role, Tariff, User, VehicleClass, Zone
from .security import hash_secret

EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)

DEMO_USERS = [
    # username, name, role, pin, password
    ("admin", "Site Admin", Role.ADMIN, "0000", "admin123"),
    ("sup1", "Supervisor Meena", Role.SUPERVISOR, "1234", "super123"),
    ("w1", "Worker Ravi", Role.WORKER, "1111", None),
    ("w2", "Worker Sunil", Role.WORKER, "2222", None),
    ("w3", "Worker Anita", Role.WORKER, "3333", None),
    ("w4", "Worker Prakash", Role.WORKER, "4444", None),
    ("guard1", "Guard Ramesh", Role.GUARD, "5555", None),
    ("guard2", "Guard Salim", Role.GUARD, "6666", None),
]


def seed_reference(db: Session) -> None:
    """Idempotent: vehicle classes, tariffs, pass types, gates, cameras, zones."""
    if db.get(VehicleClass, "BIKE") is None:
        db.add_all([VehicleClass(code="BIKE", name="Two-wheeler", enabled=True),
                    VehicleClass(code="CAR", name="Car", enabled=False)])
        db.flush()
    if not db.scalars(select(Tariff)).first():
        db.add(Tariff(vehicle_class="BIKE", version=1, effective_from=EPOCH, first_slab_minutes=120, first_slab_paise=1000,
                      per_hour_paise=500, grace_minutes=10, block_minutes=720, block_cap_paise=3000,
                      notes="Default: ₹10 up to 2 h, ₹5 per extra hour, ₹30 cap per 12 h, 10 min grace"))
        db.add(Tariff(vehicle_class="CAR", version=1, effective_from=EPOCH, first_slab_minutes=120, first_slab_paise=3000,
                      per_hour_paise=1500, grace_minutes=10, block_minutes=720, block_cap_paise=10000,
                      notes="Default car tariff (class disabled until switched on)"))
    if not db.scalars(select(PassType)).first():
        db.add_all([
            PassType(vehicle_class="BIKE", name="Monthly", period_unit="MONTH", period_value=1, price_paise=50000, is_default=True),
            PassType(vehicle_class="BIKE", name="Quarterly", period_unit="MONTH", period_value=3, price_paise=140000, active=False),
            PassType(vehicle_class="CAR", name="Monthly", period_unit="MONTH", period_value=1, price_paise=150000, is_default=True),
        ])
    if db.get(Gate, "G1") is None:
        for gid, name, direction in (("G1", "Gate 1 (Station side)", "IN"), ("G2", "Gate 2 (Road side)", "OUT")):
            db.add(Gate(id=gid, name=name, direction=direction, schedule=[]))
            db.flush()
            for side, x0 in (("LEFT", 0), ("RIGHT", 1)):
                db.add(Camera(id=f"{gid}-{side[0]}", gate_id=gid, role="ANPR", side=side,
                              rtsp_url=f"rtsp://192.168.10.{11 + (0 if gid == 'G1' else 10) + x0}:554/Streaming/Channels/101",
                              roi=[[0, 300], [1280, 300], [1280, 720], [0, 720]], capture_line=[[0, 520], [1280, 520]],
                              in_vector=[0, 1]))
            db.add(Camera(id=f"{gid}-O", gate_id=gid, role="OVERVIEW", side="CENTER",
                          rtsp_url=f"rtsp://192.168.10.{13 + (0 if gid == 'G1' else 10)}:554/Streaming/Channels/101"))
        db.flush()
        db.add_all([Zone(name="Zone A", gate_id="G1", description="Rows 1-20 near Gate 1"),
                     Zone(name="Zone B", gate_id="G2", description="Rows 21-40 near Gate 2")])
    db.flush()


def seed_users(db: Session) -> None:
    for username, name, role, pin, pw in DEMO_USERS:
        if db.scalars(select(User).where(User.username == username)).first() is None:
            db.add(User(username=username, name=name, role=role, pin_hash=hash_secret(pin),
                        password_hash=hash_secret(pw) if pw else None))
    db.flush()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create-all", action="store_true", help="create tables without alembic (dev only)")
    ap.add_argument("--users", action="store_true", help="also create demo users")
    args = ap.parse_args()
    if args.create_all:
        Base.metadata.create_all(get_engine())
    with session_scope() as db:
        seed_reference(db)
        if args.users:
            seed_users(db)
    print("seed complete")


if __name__ == "__main__":
    main()
