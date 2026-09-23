#!/usr/bin/env bash
# restore_drill.sh — prove the latest backup actually restores. Runs ON the server as `ubuntu`,
# weekly from cron (backup_db.sh --install-cron adds it), or by hand any time:
#
#   bash paisamap-etl/db/restore_drill.sh            # drill the newest backup (LATEST)
#   bash paisamap-etl/db/restore_drill.sh DUMP       # drill a specific dump
#
# Steps: restore into a throwaway `paisamap_drill` database (never the live one) with
# restore_db.sh — sha256 + exact per-table row counts against the manifest — then check the
# live encryption keys still decrypt the restored customer data and OAuth tokens
# (restore_drill_keys.py), then drop the drill database. KEEP_DRILL=1 leaves it for a look.
#
# Result goes to the log and to $BACKUP_DIR/last_drill ("<utc time> PASS|FAIL <dump> <detail>").
# A backup older than MAX_AGE_HOURS (default 36) also FAILS the drill, so a nightly cron that
# silently stopped running shows up here too.
set -euo pipefail
umask 077

LIVE_DB="${PAISAMAP_DB:-paisamap}"
DIR="${BACKUP_DIR:-/home/ubuntu/backups/paisamap}"
DRILL_DB="${LIVE_DB}_drill"
ENV_FILE="${ENV_FILE:-/etc/paisamap/db.env}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-36}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${ETL_PYTHON:-$HERE/../venv/bin/python3}"
DUMP="$(readlink -f "${1:-$DIR/LATEST}" 2>/dev/null || true)"

pg() { if [ "$(id -un)" = postgres ]; then "$@"; else sudo -n -u postgres "$@"; fi; }
drop_drill() { pg psql -X -q -d postgres -c 'SET client_min_messages = warning' -c "DROP DATABASE IF EXISTS \"$DRILL_DB\" WITH (FORCE)" >/dev/null; }
read_env() { if [ -r "$ENV_FILE" ]; then cat "$ENV_FILE"; else sudo -n cat "$ENV_FILE"; fi; }

RESULT="FAIL"; DETAIL="drill did not finish"
finish() {
  [ "${KEEP_DRILL:-}" = 1 ] || drop_drill || true
  echo "$(date -u +%FT%TZ) $RESULT $(basename "${DUMP:-none}") $DETAIL" > "$DIR/last_drill" 2>/dev/null || true
  echo "[drill] $RESULT — $DETAIL"
}
trap finish EXIT

echo "[drill] $(date -u +%FT%TZ) — $DUMP"
[ -n "$DUMP" ] && [ -f "$DUMP" ] || { DETAIL="no backup found at ${1:-$DIR/LATEST}"; exit 1; }

age_h=$(( ( $(date +%s) - $(stat -c %Y "$DUMP") ) / 3600 ))
if [ -z "${1:-}" ] && [ "$age_h" -ge "$MAX_AGE_HOURS" ]; then
  DETAIL="newest backup is ${age_h}h old (limit ${MAX_AGE_HOURS}h) — is the nightly backup cron running?"
  exit 1
fi

drop_drill
if ! bash "$HERE/restore_db.sh" "$DUMP" "$DRILL_DB"; then
  DETAIL="restore or row-count check failed (see above)"; exit 1
fi

if ! keys_out="$(read_env | "$PYTHON" "$HERE/restore_drill_keys.py" "$DRILL_DB")"; then
  echo "$keys_out"
  DETAIL="encryption keys cannot read the restored data (see above)"; exit 1
fi
echo "$keys_out"

RESULT="PASS"
DETAIL="$(sed -n 's/^rows=//p' "${DUMP%.dump}.manifest") rows restored exactly, keys OK, backup ${age_h}h old"
