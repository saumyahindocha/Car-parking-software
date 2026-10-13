import io
from datetime import timedelta

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.db import utcnow
from app.domain import importer, payments, reports
from app.domain.lookup import local_date, site_tz
from app.models import LedgerEntry, Pass, SessionStatus, User, Vehicle

from .helpers import event


def admin(db):
    return db.scalars(select(User).where(User.username == "admin")).one()


def csv_bytes(lines):
    return ("\n".join(lines) + "\n").encode()


def today():
    return utcnow().astimezone(site_tz()).date()


def test_template_round_trips_and_has_current_dates():
    rows = importer.read_rows(importer.template_csv().encode(), "t.csv")
    assert rows[0]["plate"] == "MH43AB1234" and rows[0]["pass_start"] <= today().isoformat() <= rows[0]["pass_end"]


def test_header_aliases_and_formats(db):
    content = csv_bytes([
        "Vehicle Number,Mobile,Customer Name,Pass,Valid From,Valid Till,Amount Paid,Dues",
        f"mh 43 ab 1234,+91 98765 43210,Ravi,monthly,{today():%d-%m-%Y},{today() + timedelta(days=20):%d/%m/%Y},\"₹500\",",
        "MH12CD5678,,,,,,,40",
    ])
    rows = importer.read_rows(content, "old.csv")
    plans = importer.plan(db, rows)
    assert [p.status for p in plans] == ["ok", "ok"]
    assert plans[0].plate == "MH43AB1234" and plans[0].data["phone"] == "9876543210"
    assert plans[0].data["pass_amount"] == 50000 and plans[1].data["opening_paise"] == 4000


def test_validation_errors(db):
    rows = importer.read_rows(csv_bytes([
        "plate,phone,pass_type,pass_start,pass_end,opening_balance",
        "XX99ZZ,,,,,",                                      # invalid plate
        "MH43AB1234,12345,,,,",                             # bad phone
        "MH43AB1235,,Weekly,,,",                            # unknown pass type
        "MH43AB1236,,Monthly,2026-09-10,2026-09-01,",       # end before start
        "MH43AB1237,,,,,abc",                               # bad amount
        "MH43AB1238,,,,,10",
        "MH 43 AB 1238,,,,,10",                             # duplicate plate
        "MH43AB1239,,Monthly,2020-01-01,2020-01-31,",       # expired pass -> warning, not error
    ]), "x.csv")
    st = [p.status for p in importer.plan(db, rows)]
    assert st == ["error", "error", "error", "error", "error", "ok", "error", "warning"]


def test_dry_run_changes_nothing_and_commit_imports(db, gateway, messenger):
    end = today() + timedelta(days=25)
    rows = importer.read_rows(csv_bytes([
        "plate,name,phone,pass_type,pass_start,pass_end,pass_amount,opening_balance",
        f"MH43PQ4321,Asha,9876500000,Monthly,{today()},{end},500,",
        "MH12CD5678,,9123400000,,,,,40",
        "MH12CD9999,,,,,,,-15",
    ]), "c.csv")
    plans = importer.plan(db, rows)
    assert db.scalar(select(Vehicle).where(Vehicle.plate == "MH43PQ4321")) is None  # dry run
    s = importer.summary(plans)
    assert (s["new_vehicles"], s["passes"], s["opening_balances"], s["opening_dues_paise"], s["opening_credit_paise"]) == (3, 1, 2, 4000, 1500)
    res = importer.apply(db, plans, admin(db))
    assert res["imported_rows"] == 3
    v = db.scalars(select(Vehicle).where(Vehicle.plate == "MH43PQ4321")).one()
    p = db.scalars(select(Pass).where(Pass.vehicle_id == v.id)).one()
    assert p.status == "ACTIVE" and p.channel == "IMPORT" and v.phone == "9876500000" and v.balance_paise == 0
    assert db.scalar(select(LedgerEntry).where(LedgerEntry.vehicle_id == v.id)) is None  # paid in the old system
    from app.domain.passes import pass_dict

    assert pass_dict(db, p)["ends_on"] == end.isoformat()      # last valid day, as typed
    # the pass holder now enters with zero interaction; dues are added to the next quote
    r = event(db, "IN", "MH43PQ4321", utcnow())
    assert r.session.status == SessionStatus.PASS
    r2 = event(db, "IN", "MH12CD5678", utcnow())
    q = payments.quote_session(db, r2.session.id, 120)
    assert (q.base_paise, q.dues_paise, q.amount_paise) == (1000, 4000, 5000)
    assert db.scalars(select(Vehicle).where(Vehicle.plate == "MH12CD9999")).one().balance_paise == -1500
    # imported passes are not "sold" today
    assert reports.pass_report(db, local_date(utcnow()))["sold_count"] == 0


def test_reimport_is_idempotent(db):
    lines = ["plate,phone,pass_type,pass_start,pass_end,opening_balance",
             f"MH43PQ4321,9876500000,Monthly,{today()},{today() + timedelta(days=10)},30"]
    importer.apply(db, importer.plan(db, importer.read_rows(csv_bytes(lines), "a.csv")), admin(db))
    again = importer.plan(db, importer.read_rows(csv_bytes(lines), "a.csv"))
    assert again[0].status == "warning" and not again[0].actions
    importer.apply(db, again, admin(db))
    v = db.scalars(select(Vehicle).where(Vehicle.plate == "MH43PQ4321")).one()
    assert v.balance_paise == 3000
    assert len(db.scalars(select(Pass).where(Pass.vehicle_id == v.id)).all()) == 1


def test_excel_file(db):
    wb = Workbook()
    ws = wb.active
    ws.append(["Plate", "Mobile", "Pass", "Valid Till", "Dues"])
    from datetime import datetime

    ws.append(["KA05MN2468", 9876543210, "Monthly", datetime.combine(today() + timedelta(days=5), datetime.min.time()), 25.5])
    buf = io.BytesIO()
    wb.save(buf)
    plans = importer.plan(db, importer.read_rows(buf.getvalue(), "old.xlsx"))
    assert plans[0].status == "ok" and plans[0].data["phone"] == "9876543210" and plans[0].data["opening_paise"] == 2550


def test_bad_files():
    with pytest.raises(importer.ImportFileError):
        importer.read_rows(b"name,phone\nx,1\n", "a.csv")
    with pytest.raises(importer.ImportFileError):
        importer.read_rows(b"%PDF", "a.pdf")
