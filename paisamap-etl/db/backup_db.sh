#!/usr/bin/env bash
# backup_db.sh — nightly backup of the PaisaMap Postgres database (go-live item "backups + a
# tested restore", docs/PENTEST_CHECKLIST.md). Everything that matters lives in this one
# database — accounts, orders, invoices, wallets, uploaded customer data — the app writes no
# user data to disk. Runs ON the server as `ubuntu` from cron (installed with --install-cron).
#
#   bash paisamap-etl/db/backup_db.sh                 # nightly backup -> daily/ (+ weekly/, monthly/)
#   bash paisamap-etl/db/backup_db.sh pre-restore     # tagged one-off backup -> manual/
#   bash paisamap-etl/db/backup_db.sh --install-cron  # add the backup + weekly drill to crontab
#
# What each run does:
#   1. Opens a REPEATABLE READ transaction, exports its snapshot, and runs pg_dump against that
#      snapshot — then counts every table's rows inside the SAME transaction. The .manifest
#      therefore describes exactly what is in the .dump, which is what lets restore_drill.sh
#      demand identical row counts instead of "roughly the same".
#   2. Checks the archive reads back (pg_restore --list) and holds one TABLE DATA entry per table.
#   3. Only then renames it into place (a failed run never leaves a half-written .dump).
#   4. Keeps 14 daily, 8 weekly (Sundays), 12 monthly (the 1st) — hard links, so a dump that is
#      both daily and weekly takes disk space once — and 10 manual ones.
#
# pg_dump runs as the `postgres` OS user over the local socket (peer auth), so this script
# never reads /etc/paisamap/db.env or handles the DB password.
#
# Files are NOT encrypted here: they sit on the same disk as the live database, readable only
# by `ubuntu` (dir 700, files 600), so they add no exposure the live DB does not already have.
# Copies that leave the box are covered in docs/BACKUP_RESTORE.md.
#
# NB: a restored database is only readable with the SAME CUSTOMER_DATA_KEY and
# ANALYTICS_TOKEN_KEY that encrypted it. Those keys live in /etc/paisamap/db.env, not in the
# dump — keep a copy of db.env somewhere that is not this server (docs/BACKUP_RESTORE.md).
set -euo pipefail
umask 077

DB="${PAISAMAP_DB:-paisamap}"
DIR="${BACKUP_DIR:-/home/ubuntu/backups/paisamap}"
KEEP_DAILY="${KEEP_DAILY:-14}"
KEEP_WEEKLY="${KEEP_WEEKLY:-8}"
KEEP_MONTHLY="${KEEP_MONTHLY:-12}"
KEEP_MANUAL="${KEEP_MANUAL:-10}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pg() { if [ "$(id -un)" = postgres ]; then "$@"; else sudo -n -u postgres "$@"; fi; }
die() { echo "[backup] FAILED: $*" >&2; exit 1; }

if [ "${1:-}" = "--install-cron" ]; then
  LOGS="$HOME/logs"; mkdir -p "$LOGS"
  current="$(crontab -l 2>/dev/null || true)"
  if grep -q "backup_db.sh" <<<"$current"; then
    echo "crontab already has the backup entries — nothing to do"; exit 0
  fi
  # 01:30 backup lands before cron_enrich.sh's 02:00 run; the drill runs Sundays at 03:30.
  printf '%s\n%s\n%s\n' "$current" \
    "30 1 * * * $HERE/backup_db.sh >> $LOGS/backup.log 2>&1" \
    "30 3 * * 0 $HERE/restore_drill.sh >> $LOGS/restore_drill.log 2>&1" | sed '/^$/d' | crontab -
  echo "installed:"; crontab -l | grep -E "backup_db|restore_drill"
  exit 0
fi

TAG="${1:-}"
if [ -n "$TAG" ] && ! [[ "$TAG" =~ ^[a-z0-9-]{1,40}$ ]]; then
  die "tag must be lowercase letters, digits and dashes (got '$TAG')"
fi

mkdir -p "$DIR"/{daily,weekly,monthly,manual}
chmod 700 "$DIR"
exec 9>"$DIR/.lock"
flock -n 9 || die "another backup is already running"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="${DB}-${STAMP}${TAG:+-$TAG}"
DEST="$DIR/$([ -n "$TAG" ] && echo manual || echo daily)"
TMP_DUMP="$DEST/.$NAME.dump.partial"
TMP_MAN="$DEST/.$NAME.manifest.partial"
trap 'rm -f "$TMP_DUMP" "$TMP_MAN" "$TMP_MAN.counts"; [ -n "${PSQL_PID:-}" ] && kill "$PSQL_PID" 2>/dev/null; true' EXIT

echo "[backup] $(date -u +%FT%TZ) — $DB -> $DEST/$NAME.dump"

# 1. Hold a snapshot open in a psql coprocess for the whole dump + count.
coproc PSQL { pg psql -X -q -A -t -v ON_ERROR_STOP=1 -d "$DB" 2>&1; }
PSQL_PID=$PSQL_PID
echo "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();" >&"${PSQL[1]}"
read -r -t 60 SNAP <&"${PSQL[0]}" || die "could not open a snapshot on $DB"
[[ "$SNAP" =~ ^[0-9A-F]+-[0-9A-F]+-[0-9]+$ ]] || die "unexpected snapshot id from psql: $SNAP"

pg pg_dump -Fc --snapshot="$SNAP" -d "$DB" > "$TMP_DUMP" || die "pg_dump exited non-zero"

cat >&"${PSQL[1]}" <<'SQL'
SELECT coalesce(string_agg(format('SELECT %L || ''|'' || count(*) FROM %I.%I',
                                  schemaname || '.' || tablename, schemaname, tablename),
                           ' UNION ALL '), 'SELECT NULL WHERE false')
  FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema') \gexec
SELECT '__END__';
SQL
: > "$TMP_MAN.counts"
while read -r -t 600 line <&"${PSQL[0]}"; do
  [ "$line" = "__END__" ] && break
  [[ "$line" =~ ^[A-Za-z0-9_.]+\|[0-9]+$ ]] || die "unexpected line while counting rows: $line"
  echo "$line" >> "$TMP_MAN.counts"
done
[ "${line:-}" = "__END__" ] || die "row count did not finish"
echo "COMMIT;" >&"${PSQL[1]}"
eval "exec ${PSQL[1]}>&-"
wait "$PSQL_PID" 2>/dev/null || true
PSQL_PID=""

# 2. The archive must read back, with one TABLE DATA entry per counted table.
TOC="$(pg_restore --list "$TMP_DUMP")" || die "pg_restore cannot read the archive"
n_tables=$(wc -l < "$TMP_MAN.counts" | tr -d ' ')
n_data=$(grep -c " TABLE DATA " <<<"$TOC" || true)
[ "$n_tables" -gt 0 ] || die "no tables found in $DB"
[ "$n_data" -eq "$n_tables" ] || die "archive has $n_data TABLE DATA entries but $DB has $n_tables tables"
n_rows=$(awk -F'|' '{s+=$2} END {print s+0}' "$TMP_MAN.counts")
size=$(wc -c < "$TMP_DUMP" | tr -d ' ')
sha=$(sha256sum "$TMP_DUMP" | cut -d' ' -f1)

{
  echo "# PaisaMap backup manifest — counts taken in the same snapshot as the dump"
  echo "database=$DB"
  echo "created_utc=$STAMP"
  echo "snapshot=$SNAP"
  echo "server_version=$(pg psql -X -At -d "$DB" -c 'SHOW server_version' | cut -d' ' -f1)"
  echo "dump_bytes=$size"
  echo "dump_sha256=$sha"
  echo "tables=$n_tables"
  echo "rows=$n_rows"
  echo "[counts]"
  LC_ALL=C sort "$TMP_MAN.counts"
} > "$TMP_MAN"
rm -f "$TMP_MAN.counts"

# 3. Publish atomically (manifest first, so any .dump that exists has its manifest).
mv "$TMP_MAN" "$DEST/$NAME.manifest"
mv "$TMP_DUMP" "$DEST/$NAME.dump"

# 4. Rotation.
link_pair() { ln -f "$DEST/$NAME.dump" "$DIR/$1/$NAME.dump"; ln -f "$DEST/$NAME.manifest" "$DIR/$1/$NAME.manifest"; }
prune() {  # keep the newest $2 dumps in $1 (names sort by timestamp)
  local d="$DIR/$1" keep="$2" old
  { ls -1 "$d" 2>/dev/null | grep '\.dump$' || true; } | sort -r | tail -n +"$((keep + 1))" | while read -r old; do
    rm -f "$d/$old" "$d/${old%.dump}.manifest"
  done
}
if [ -z "$TAG" ]; then
  [ "$(date -u +%u)" = 7 ] && link_pair weekly
  [ "$(date -u +%d)" = 01 ] && link_pair monthly
  ln -sfn "daily/$NAME.dump" "$DIR/LATEST"
  prune daily "$KEEP_DAILY"; prune weekly "$KEEP_WEEKLY"; prune monthly "$KEEP_MONTHLY"
else
  prune manual "$KEEP_MANUAL"
fi
echo "$STAMP $NAME.dump $size bytes, $n_tables tables, $n_rows rows" > "$DIR/last_backup"

echo "[backup] OK $DEST/$NAME.dump — $size bytes, $n_tables tables, $n_rows rows, sha256 $sha"
