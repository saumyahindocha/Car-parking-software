"""Import existing customers from a CSV/Excel file (same rules as Dashboard → Import customers).

    python -m app.import_customers customers.xlsx            # dry run: shows what would happen
    python -m app.import_customers customers.xlsx --commit   # import (refuses if any row has errors)
    python -m app.import_customers --template > customer-import-template.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

from . import domain  # noqa: F401  (audit hooks)
from .db import session_scope
from .domain import importer
from .models import Role, User


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--skip-errors", action="store_true", help="import the valid rows even if some have errors")
    ap.add_argument("--as-user", default="admin", help="admin username recorded as the importer")
    ap.add_argument("--template", action="store_true", help="print a CSV template and exit")
    a = ap.parse_args()
    if a.template:
        sys.stdout.write(importer.template_csv())
        return
    if not a.file:
        ap.error("give a .csv or .xlsx file")
    p = Path(a.file)
    rows = importer.read_rows(p.read_bytes(), p.name)
    with session_scope() as db:
        user = db.scalars(select(User).where(User.username == a.as_user, User.role == Role.ADMIN)).first()
        if user is None:
            sys.exit(f"no admin user '{a.as_user}'")
        db.info["user_id"] = user.id
        plans = importer.plan(db, rows)
        for pl in plans:
            if pl.status != "ok" or pl.actions:
                print(f"row {pl.row:>5}  {pl.display_plate or '-':<16} {pl.status:<8} "
                      f"{'; '.join(pl.actions) or '-'}{'  | ' + '; '.join(pl.messages) if pl.messages else ''}")
        s = importer.summary(plans)
        print("\n" + ", ".join(f"{k}={v}" for k, v in s.items()))
        if not a.commit:
            print("\ndry run: nothing was changed (add --commit to import)")
            db.rollback()
            return
        if s["errors"] and not a.skip_errors:
            db.rollback()
            sys.exit(f"{s['errors']} row(s) have errors; fix them or use --skip-errors")
        res = importer.apply(db, plans, user)
        print(f"imported {res['imported_rows']} rows (batch {res['batch']})")


if __name__ == "__main__":
    main()
