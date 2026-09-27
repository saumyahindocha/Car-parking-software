#!/usr/bin/env bash
# Restore a backup into a database.
#   ./restore.sh /mnt/backup/parking/parking-20260311-023000.dump            # into 'parking' (asks first)
#   ./restore.sh <dump> parking_restore_test                                 # into a scratch DB
# For the live database stop the backend first:  docker compose stop backend anpr
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] && { set -a; source .env; set +a; }
dump=${1:?usage: restore.sh <dump> [target_db]}
target=${2:-parking}
pgx() { docker compose exec -T -e PGPASSWORD="$DB_PASSWORD" db "$@"; }  # tools inside the db container
if [[ -f "$dump.sha256" ]]; then sha256sum -c "$dump.sha256"; fi
if [[ "$target" == "parking" && "${FORCE:-0}" != 1 ]]; then
  read -rp "Overwrite the LIVE database 'parking'? type YES: " ok; [[ "$ok" == YES ]] || exit 1
fi
pgx psql -U parking -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$target\" WITH (FORCE);" -c "CREATE DATABASE \"$target\";"
pgx pg_restore -U parking --no-owner --exit-on-error -d "$target" < "$dump"
echo "restored $dump into $target"
