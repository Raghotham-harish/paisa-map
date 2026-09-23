#!/usr/bin/env bash
# restore_db.sh — restore a backup_db.sh dump. Runs ON the server as `ubuntu`.
#
#   restore_db.sh DUMP NEW_DB        restore into a NEW database (refuses if it exists) and check
#                                    it; the live site is untouched. Use this to inspect old data.
#   restore_db.sh DUMP --live        replace the live database with DUMP (asks you to type its name)
#   restore_db.sh --rollback OLD_DB  undo a --live restore: put OLD_DB back as the live database
#
# DUMP may be a path or the LATEST symlink, e.g.
#   restore_db.sh /home/ubuntu/backups/paisamap/LATEST paisamap_check
#
# Every restore is checked against the dump's .manifest: the file's sha256 must match, and every
# table must come back with EXACTLY the row count recorded when the dump was taken (backup_db.sh
# counts inside the dump's own snapshot, so any difference means data was lost). A dump without a
# manifest is refused unless ALLOW_NO_MANIFEST=1.
#
# How --live works (downtime = only step 3-5, a few seconds on today's database size):
#   1. restore DUMP into `paisamap_incoming` and check it — the site keeps running meanwhile
#   2. ask you to confirm
#   3. stop the paisamap service, take a `pre-restore` safety backup of the live database
#   4. swap names: paisamap -> paisamap_prerestore_<time>, paisamap_incoming -> paisamap
#   5. start the service and call /api/health
# Nothing is dropped. The old database stays as paisamap_prerestore_<time>, and the rollback
# command is printed at the end. Drop it yourself once you are happy:
#   sudo -u postgres dropdb paisamap_prerestore_<time>
#
# Restored objects are owned by the `paisamap` role (pg_restore --no-owner --role=paisamap), so
# the app's DATABASE_URL works against the restored database unchanged.
set -euo pipefail
umask 077

LIVE_DB="${PAISAMAP_DB:-paisamap}"
OWNER="${PAISAMAP_DB_OWNER:-paisamap}"
SERVICE="${PAISAMAP_SERVICE-paisamap}"                         # empty = no service to stop/start
HEALTH_URL="${HEALTH_URL-http://127.0.0.1:8080/api/health}"      # empty = skip the health check
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date -u +%Y%m%d_%H%M%S)"

pg() { if [ "$(id -un)" = postgres ]; then "$@"; else sudo -n -u postgres "$@"; fi; }
say() { echo "[restore] $*"; }
die() { echo "[restore] FAILED: $*" >&2; exit 1; }
sql() { pg psql -X -q -A -t -v ON_ERROR_STOP=1 -d postgres "$@"; }
valid_name() { [[ "$1" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || die "not a safe database name: '$1'"; }
db_exists() { [ "$(sql -c "SELECT 1 FROM pg_database WHERE datname = '$1'")" = 1 ]; }
service() {  # service start|stop — no-op when SERVICE is empty (tests, or a box without systemd)
  [ -n "$SERVICE" ] || { say "(no service configured — skipping $1)"; return 0; }
  sudo -n systemctl "$1" "$SERVICE"
}

# Same query backup_db.sh runs inside the dump's snapshot: "schema.table|rows", sorted.
count_rows() {
  pg psql -X -q -A -t -v ON_ERROR_STOP=1 -d "$1" <<'SQL' | LC_ALL=C sort
SELECT coalesce(string_agg(format('SELECT %L || ''|'' || count(*) FROM %I.%I',
                                  schemaname || '.' || tablename, schemaname, tablename),
                           ' UNION ALL '), 'SELECT NULL WHERE false')
  FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema') \gexec
SQL
}

check_dump() {  # sets MAN; refuses a corrupt dump
  [ -f "$DUMP" ] || die "no such dump: $DUMP"
  MAN="${DUMP%.dump}.manifest"
  if [ -f "$MAN" ]; then
    local want got
    want="$(sed -n 's/^dump_sha256=//p' "$MAN")"
    got="$(sha256sum "$DUMP" | cut -d' ' -f1)"
    [ "$want" = "$got" ] || die "sha256 mismatch — $DUMP is corrupt or not the file its manifest describes"
    say "checksum OK ($(sed -n 's/^rows=//p' "$MAN") rows in $(sed -n 's/^tables=//p' "$MAN") tables expected)"
  elif [ "${ALLOW_NO_MANIFEST:-}" = 1 ]; then
    MAN=""; say "WARNING: no manifest — restoring without checking row counts"
  else
    die "no manifest next to $DUMP (set ALLOW_NO_MANIFEST=1 to restore it unchecked)"
  fi
}

restore_into() {  # restore_into NEW_DB — create, restore in one transaction, verify
  local db="$1"
  sql -c "CREATE DATABASE \"$db\" OWNER \"$OWNER\""
  say "restoring into $db…"
  # stdin, so the postgres OS user never needs read access to ubuntu's backup files
  if ! pg pg_restore --no-owner --no-privileges --role="$OWNER" --exit-on-error \
         --single-transaction -d "$db" < "$DUMP"; then
    die "pg_restore failed — $db is left behind empty for inspection (drop it: sudo -u postgres dropdb $db)"
  fi
  if [ -n "$MAN" ]; then
    local diff_out
    if ! diff_out="$(diff <(sed -n '/^\[counts\]$/,$p' "$MAN" | tail -n +2) <(count_rows "$db"))"; then
      echo "$diff_out" | sed 's/^/[restore]   /' >&2
      die "row counts in $db differ from the manifest (< manifest, > restored)"
    fi
    say "row counts match the manifest exactly ($(sed -n 's/^tables=//p' "$MAN") tables)"
  fi
}

swap_in() {  # swap_in NEW_LIVE RETIRED_NAME — rename the live DB away, rename NEW_LIVE to live
  local new="$1" retired="$2" i
  sql -c "ALTER DATABASE \"$LIVE_DB\" WITH ALLOW_CONNECTIONS false"
  for i in 1 2 3 4 5; do
    sql -c "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity
             WHERE datname IN ('$LIVE_DB', '$new') AND pid <> pg_backend_pid()" >/dev/null
    if sql <<SQL 2>/dev/null
BEGIN;
ALTER DATABASE "$LIVE_DB" RENAME TO "$retired";
ALTER DATABASE "$new" RENAME TO "$LIVE_DB";
COMMIT;
SQL
    then
      sql -c "ALTER DATABASE \"$retired\" WITH ALLOW_CONNECTIONS true"
      return 0
    fi
    sleep 1
  done
  sql -c "ALTER DATABASE \"$LIVE_DB\" WITH ALLOW_CONNECTIONS true"
  return 1
}

health() {
  [ -n "$HEALTH_URL" ] || { say "(no HEALTH_URL — skipping health check)"; return 0; }
  local i
  for i in $(seq 1 15); do
    curl -fsS -m 5 "$HEALTH_URL" 2>/dev/null && { echo; return 0; }
    sleep 2
  done
  return 1
}

confirm() {
  [ "${CONFIRM:-}" = "$LIVE_DB" ] && return 0
  [ -t 0 ] || die "not a terminal — set CONFIRM=$LIVE_DB to confirm non-interactively"
  local ans
  read -r -p "[restore] $1 Type '$LIVE_DB' to go ahead: " ans
  [ "$ans" = "$LIVE_DB" ] || die "not confirmed — nothing changed"
}

# Once the service is stopped, any failure must still bring the site back up.
STOPPED=0
on_exit() { [ "$STOPPED" = 1 ] && { say "bringing the service back up after a failure…"; service start || true; }; true; }
trap on_exit EXIT

valid_name "$LIVE_DB"; valid_name "$OWNER"

case "${1:-}" in
  --rollback)
    OLD="${2:-}"; valid_name "$OLD"
    db_exists "$OLD" || die "no database named $OLD"
    RETIRED="${LIVE_DB}_rolledback_${STAMP}"
    confirm "This makes $OLD the live database again (the current one is kept as $RETIRED)."
    service stop; STOPPED=1
    swap_in "$OLD" "$RETIRED" || die "could not swap databases (something kept reconnecting) — nothing changed"
    service start; STOPPED=0
    health || die "service did not answer $HEALTH_URL after the rollback — check: sudo journalctl -u $SERVICE -n 50"
    say "ROLLED BACK — $OLD is live again; the replaced database is kept as $RETIRED"
    ;;
  ""|-h|--help)
    sed -n '2,12p' "$0"; exit 0
    ;;
  *)
    DUMP="$(readlink -f "$1")"; TARGET="${2:-}"
    [ -n "$TARGET" ] || die "say where to restore: a new database name, or --live"
    check_dump
    if [ "$TARGET" != "--live" ]; then
      valid_name "$TARGET"
      [ "$TARGET" != "$LIVE_DB" ] || die "that is the live database — use --live to replace it"
      db_exists "$TARGET" && die "database $TARGET already exists — pick another name or drop it first"
      restore_into "$TARGET"
      say "OK — $DUMP restored into $TARGET (the live database was not touched)"
      exit 0
    fi

    INCOMING="${LIVE_DB}_incoming"; OLD="${LIVE_DB}_prerestore_${STAMP}"
    valid_name "$OLD"
    db_exists "$LIVE_DB" || die "live database $LIVE_DB does not exist"
    if db_exists "$INCOMING"; then
      say "dropping $INCOMING left over from an earlier attempt (it was never live)"
      sql -c "DROP DATABASE \"$INCOMING\" WITH (FORCE)"
    fi
    restore_into "$INCOMING"
    confirm "The live database will be REPLACED by $(basename "$DUMP") (current one kept as $OLD)."

    say "stopping the service…"
    service stop; STOPPED=1
    say "safety backup of the live database before replacing it…"
    bash "$HERE/backup_db.sh" pre-restore || die "safety backup failed — live database NOT replaced"
    swap_in "$INCOMING" "$OLD" || die "could not swap databases (something kept reconnecting) — live database NOT replaced"
    say "swapped: $OLD (old) / $LIVE_DB (restored)"
    service start; STOPPED=0
    if ! health; then
      say "service did not answer $HEALTH_URL — check: sudo journalctl -u $SERVICE -n 50"
      say "to undo: bash $HERE/restore_db.sh --rollback $OLD"
      exit 1
    fi
    say "RESTORED — the live database now holds $(basename "$DUMP")"
    say "old database kept as $OLD. To undo: bash $HERE/restore_db.sh --rollback $OLD"
    say "once you are happy, drop it: sudo -u postgres dropdb $OLD"
    ;;
esac
