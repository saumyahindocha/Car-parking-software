#!/usr/bin/env bash
# Tested restore: restores the latest backup into a scratch database and checks it is usable
# (row counts, ledger balances match the cached vehicle balances, alembic version present).
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] && { set -a; source .env; set +a; }
latest=$(ls -1t "$BACKUP_DIR"/parking-*.dump | head -1)
FORCE=1 ./restore.sh "$latest" parking_restore_test
pgx() { docker compose exec -T -e PGPASSWORD="$DB_PASSWORD" db "$@"; }  # tools inside the db container
q() { pgx psql -U parking -d parking_restore_test -tAc "$1"; }
ver=$(q "select version_num from alembic_version")
sessions=$(q "select count(*) from parking_sessions")
ledger=$(q "select count(*) from ledger_entries")
bad=$(q "select count(*) from vehicles v left join (select vehicle_id, sum(amount_paise) s from ledger_entries group by vehicle_id) l on l.vehicle_id=v.id where coalesce(l.s,0) <> v.balance_paise")
echo "$(date -Is) restore test of $latest: schema=$ver sessions=$sessions ledger_entries=$ledger balance_mismatches=$bad"
[[ -n "$ver" && "$bad" == 0 ]] || { echo "RESTORE TEST FAILED"; exit 1; }
pgx psql -U parking -d postgres -c "DROP DATABASE parking_restore_test WITH (FORCE);" >/dev/null
echo "restore test OK"
