#!/usr/bin/env bash
# refit_weekly.sh — the Sunday full PPI refit, called by cron_enrich.sh step 5.
#
# Split out of cron_enrich.sh (F2, refit peer review 2026-10-06) so it can be
# tested on its own (tests/test_refit_weekly_sh.py overrides REPO/PYTHON/LOG_DIR).
#
# ml_refinement.py now decides before it writes: a failed gate writes no CSV
# and no DB row and exits 3, so a rejection no longer needs reverting here. The
# old `git checkout` revert also threw away the rows steps 2/3 had appended to
# the same CSVs that night while the DB kept them (CSV/DB drift).
#
# What's left for this script:
#   - a one-shot owner waiver: if $ACCEPT_FILE exists, its text is the reason
#     passed as --accept-drift (waives the ±10pt stability gate only), and the
#     file is removed after the run whatever the outcome — a waiver never
#     carries over to a later week;
#   - a crash (any exit other than 0/3) restores the four refit outputs from a
#     snapshot taken just before the refit, in case it died mid-write.
#
# To approve a deliberate shift for the next Sunday run (owner, on the server):
#   echo "reason, e.g. F1 state fix approved 2026-10-08" > /home/ubuntu/refit_accept_drift

set -uo pipefail

REPO="${REPO:-/home/ubuntu/paisa-map}"
ETL="$REPO/paisamap-etl"
PYTHON="${PYTHON:-$ETL/venv/bin/python3}"
LOG_DIR="${LOG_DIR:-/home/ubuntu/logs}"
ACCEPT_FILE="${REFIT_ACCEPT_FILE:-/home/ubuntu/refit_accept_drift}"
DATE=$(date +%Y-%m-%d)
REFIT_LOG="$LOG_DIR/full_refit_${DATE}.log"
LOCK="$ETL/data/.write.lock"    # same lock as _filelock.py's write_lock()

OUTPUTS=(
    data/output/ppi_map_data.csv
    paisamap-etl/data/output/ppi_map_data.csv
    paisamap-etl/data/output/ppi_ml_refined.csv
    paisamap-etl/data/output/ml_diagnostics.json
)

mkdir -p "$LOG_DIR"
cd "$REPO" || exit 1

SNAP=$(mktemp -d)
trap 'rm -rf "$SNAP"' EXIT
for f in "${OUTPUTS[@]}"; do
    [ -f "$f" ] && mkdir -p "$SNAP/$(dirname "$f")" && cp -p "$f" "$SNAP/$f"
done

ARGS=()
if [ -e "$ACCEPT_FILE" ]; then
    REASON=$(head -c 300 "$ACCEPT_FILE" | tr '\n' ' ' | sed 's/ *$//')
    [ -n "$REASON" ] || REASON="approved by owner"
    ARGS=(--accept-drift "$REASON")
    echo "  Owner drift waiver found: \"$REASON\" (stability gate waived for this run only)"
fi

(cd "$ETL" && "$PYTHON" etl/ml_refinement.py "${ARGS[@]+"${ARGS[@]}"}") > "$REFIT_LOG" 2>&1
RC=$?
[ -e "$ACCEPT_FILE" ] && rm -f "$ACCEPT_FILE"

restore() {
    # Under the writers' lock where flock exists (the server), so a live
    # enrich doesn't interleave with the copy-back.
    _copy() { for f in "${OUTPUTS[@]}"; do [ -f "$SNAP/$f" ] && cp -p "$SNAP/$f" "$f"; done; }
    if command -v flock >/dev/null 2>&1; then
        mkdir -p "$(dirname "$LOCK")"
        exec 9>"$LOCK"; flock 9; _copy; flock -u 9; exec 9>&-
    else
        _copy
    fi
}

if [ "$RC" -eq 0 ] && ! grep -q "^  FAIL" "$REFIT_LOG"; then
    echo "  Full refit OK — see $REFIT_LOG for gate/swing summary."
elif [ "$RC" -eq 3 ]; then
    echo "  Refit REJECTED by a validation gate — nothing was written, last accepted PPI stays live."
    grep "^  FAIL" "$REFIT_LOG" | sort -u | sed 's/^/  /' || true
    echo "  Details: $REFIT_LOG and paisamap-etl/data/output/refit_last_run.json (committed)."
else
    restore
    echo "  ml_refinement.py exited $RC — restored the refit outputs from the pre-run snapshot."
    echo "  If the log shows 'DB dual-write: upserted', the DB holds this run's scores: re-run or resync."
    echo "  See $REFIT_LOG."
fi
exit 0
