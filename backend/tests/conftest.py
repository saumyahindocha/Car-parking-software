import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PARK_RUN_BACKGROUND_JOBS", "false")
os.environ.setdefault("PARK_PBKDF2_ITERATIONS", "1000")
os.environ.setdefault("PARK_IMAGE_ROOT", "/tmp/park-test-images")
os.environ.setdefault("PARK_UPLOAD_ROOT", "/tmp/park-test-uploads")

from app import domain  # noqa: E402,F401  (registers hooks)
from app.adapters.gateway import MockGateway, set_gateway  # noqa: E402
from app.adapters.messaging import Messenger, NoOpSender, set_messenger  # noqa: E402
from app.db import Base, SessionLocal, set_engine  # noqa: E402
from app.seed import seed_reference, seed_users  # noqa: E402


TEST_DB = os.environ.get("TEST_DATABASE_URL")  # e.g. postgresql+psycopg://parking:parking@localhost/parking_test


@pytest.fixture()
def engine():
    if TEST_DB:
        eng = create_engine(TEST_DB)
        Base.metadata.drop_all(eng)
    else:
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    set_engine(eng)
    db = SessionLocal()
    seed_reference(db)
    seed_users(db)
    db.commit()
    db.close()
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


@pytest.fixture()
def gateway():
    gw = MockGateway()
    set_gateway(gw)
    return gw


@pytest.fixture()
def messenger():
    m = Messenger(sms=NoOpSender("SMS"), whatsapp=NoOpSender("WHATSAPP"))
    set_messenger(m)
    return m


IST = timezone(timedelta(hours=5, minutes=30))


def ist(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=IST)
