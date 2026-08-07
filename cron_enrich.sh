#!/usr/bin/env bash
# cron_enrich.sh — Daily enrichment cron for PaisaMap (runs on Lightsail server)
#
# Installs as a cron job:
#   crontab -e
#   0 2 * * * /home/ubuntu/paisa-map/cron_enrich.sh >> /home/ubuntu/logs/enrich_cron.log 2>&1
#
# What it does (in order):
#   1. Pull latest code from GitHub (enrichment_log may have been synced back by Actions)
#   2. Enrich user-visited pincodes from last 7 days that aren't in the ML output
#   3. Pre-enrich up to 30 HCES districts per day (batch fills coverage map)
#   4. Backfill up to 20 new boundary polygons per day (data/boundaries.geojson) —
#      every pincode added by steps 2/3 (or a live pin-drop) starts without a real
#      admin-boundary shape, so the choropleth/heatmap map representations fall
#      back to a synthetic circle for it until this catches up. Previously this
#      only ever ran as a manual one-off, so coverage quietly eroded back down
#      as new pincodes accumulated with nothing backfilling them.
#   5. On Sundays only: full ML ensemble refit (ml_refinement.py). Steps 2/3
#      only ever append new pincodes via IDW interpolation — they never
#      recalibrate the PCA/Ridge/HGB ensemble itself, so its global
#      normalization stats silently go stale as the dataset grows. Found
#      2026-08-07: 3+ weeks with no full refit meant 53% of pincodes had
#      never been through the real model. Weekly keeps each recalibration
#      small enough to review instead of letting drift pile up for weeks.
#      Reverts its own output (doesn't commit) if any of the 10 core
#      validation gates FAIL — a stale-but-correct PPI beats an unreviewed
#      regression going live unattended.
#   6. Mirror the output CSVs to nginx's static root so the live map picks up
#      today's enrichment right away, not just at the next full deploy
#   7. Commit and push all touched data files directly — nothing else pushes
#      these on our behalf, so a missed push here means the next `deploy.sh`
#      hard-reset silently erases the night's work (see 2026-07-14 incident).

set -euo pipefail

REPO="/home/ubuntu/paisa-map"
ETL="$REPO/paisamap-etl"
PYTHON="$ETL/venv/bin/python3"
LOG_DIR="/home/ubuntu/logs"
DATE=$(date +%Y-%m-%d)

mkdir -p "$LOG_DIR"

echo ""
echo "=========================================="
echo " PaisaMap daily enrich — $DATE"
echo "=========================================="

cd "$REPO"

# ── 1. Pull latest (enrichment_log synced by GitHub Actions every 6h) ──────────
echo ""
echo "[1/7] Pulling latest code..."
git fetch origin main --quiet
# Only fast-forward merge data files — don't discard local enrichment data
git merge --ff-only origin/main --quiet || {
    echo "  Fast-forward failed (local changes) — skipping pull"
}

# ── 2. Enrich user-visited pincodes ─────────────────────────────────────────────
echo ""
echo "[2/7] Auto-enriching user-visited pincodes (last 7 days)..."
"$PYTHON" "$ETL/etl/auto_enrich_visited.py" --days 7 || {
    echo "  auto_enrich_visited.py exited non-zero — continuing"
}

# ── 3. Batch pre-enrich HCES districts (30 per day) ─────────────────────────────
echo ""
echo "[3/7] Batch pre-enriching HCES districts (up to 30 today)..."
"$PYTHON" "$ETL/etl/batch_enrich_hces.py" --limit 30 || {
    echo "  batch_enrich_hces.py exited non-zero — continuing"
}

# ── 4. Backfill boundary polygons (60 new per day, resumable) ───────────────────
# Bumped from 20 → 60 (adds ~3 more minutes to this step, still well within the
# nightly cron window) to clear the ~300-pincode backlog in days instead of weeks —
# choropleth/heatmap fall back to synthetic-looking circles for any pincode without
# a real boundary yet.
echo ""
echo "[4/7] Backfilling boundary polygons (up to 60 new today)..."
"$PYTHON" "$ETL/etl/fetch_boundaries.py" --resume --limit 60 || {
    echo "  fetch_boundaries.py exited non-zero — continuing"
}

# ── 5. Weekly full ML ensemble refit (Sundays only) ──────────────────────────────
if [ "$(date +%u)" = "7" ]; then
    echo ""
    echo "[5/7] Sunday — running full ML ensemble refit..."
    REFIT_LOG="$LOG_DIR/full_refit_${DATE}.log"
    if (cd "$ETL" && "$PYTHON" etl/ml_refinement.py) > "$REFIT_LOG" 2>&1; then
        if grep -q "^  FAIL" "$REFIT_LOG"; then
            echo "  Validation gate FAILED — see $REFIT_LOG"
            echo "  Reverting refit output, keeping last known-good PPI live."
            git checkout -- \
                data/output/ppi_map_data.csv \
                paisamap-etl/data/output/ppi_map_data.csv \
                paisamap-etl/data/output/ppi_ml_refined.csv \
                paisamap-etl/data/output/ml_diagnostics.json 2>/dev/null || true
        else
            echo "  Full refit OK — see $REFIT_LOG for gate/swing summary."
        fi
    else
        echo "  ml_refinement.py exited non-zero — see $REFIT_LOG, skipping this week's refit"
    fi
else
    echo ""
    echo "[5/7] Not Sunday — skipping weekly full refit."
fi

# ── 6. Mirror to nginx's static root — that's what the live map actually
#      reads (see /var/www/paisamap), and it's only otherwise refreshed by
#      a full deploy. Copying here means today's enrichment shows up on the
#      map right away instead of waiting on the next non-cron push. ───────────
STATIC_OUT="/var/www/paisamap/data/output"
if [ -d "$STATIC_OUT" ]; then
    echo ""
    echo "[6/7] Mirroring output CSVs to $STATIC_OUT..."
    cp -f data/output/enrichment_log.csv data/output/ppi_map_data.csv "$STATIC_OUT/" 2>/dev/null || true
fi
STATIC_ROOT="/var/www/paisamap/data"
if [ -d "$STATIC_ROOT" ]; then
    cp -f data/boundaries.geojson "$STATIC_ROOT/" 2>/dev/null || true
fi

# ── 7. Stage any new/updated data files ─────────────────────────────────────────
echo ""
echo "Staging updated data files..."
cd "$REPO"
git add \
    data/output/enrichment_log.csv \
    data/output/ppi_map_data.csv \
    data/boundaries.geojson \
    paisamap-etl/data/output/ppi_ml_refined.csv \
    paisamap-etl/data/output/ppi_map_data.csv \
    paisamap-etl/data/output/ml_diagnostics.json \
    paisamap-etl/data/output/batch_enrich_log.csv \
    paisamap-etl/data/raw/pincode_coords.csv \
    paisamap-etl/data/raw/pincode_names.csv \
    paisamap-etl/data/raw/property_rates.csv \
    paisamap-etl/data/raw/bank_deposits.csv \
    paisamap-etl/data/raw/nightlights.csv \
    paisamap-etl/data/raw/poi_density.csv \
    paisamap-etl/data/raw/itr_filers.csv \
    paisamap-etl/data/raw/vehicle_density.csv \
    paisamap-etl/data/raw/financial_inclusion.csv \
    paisamap-etl/data/raw/rto_enhanced.csv \
    paisamap-etl/data/reference/pincode_district_map.csv \
    2>/dev/null || true

if git diff --staged --quiet; then
    echo "  No new data — nothing to commit."
else
    TOTAL=$(tail -n +2 data/output/ppi_map_data.csv 2>/dev/null | wc -l | tr -d ' ')
    REFIT_SUFFIX=""
    if [ "$(date +%u)" = "7" ] && git diff --staged --name-only | grep -q "paisamap-etl/data/output/ppi_ml_refined.csv"; then
        REFIT_SUFFIX=" + weekly full refit"
    fi
    git commit -m "cron: daily enrich ${DATE} — ${TOTAL} pincodes total${REFIT_SUFFIX} [skip ci]" \
        --author "PaisaMap Cron <noreply@cooterlabs.com>" --quiet
    echo "  Committed. Total pincodes: $TOTAL"

    # ── Push — rebase onto origin first since the 6h GitHub Actions sync
    #      and manual pushes can land while this script is mid-run ──────────
    echo ""
    echo "Pushing to origin/main..."
    if git fetch origin main --quiet && git rebase origin/main --quiet --autostash; then
        if git push origin main --quiet; then
            echo "  Pushed."
        else
            echo "  Push rejected — commit stays local, will retry next run."
        fi
    else
        echo "  Rebase onto origin/main failed — commit stays local, will retry next run."
        git rebase --abort 2>/dev/null || true
    fi
fi

echo ""
echo "=========================================="
echo " Done — $(date +%H:%M:%S)"
echo "=========================================="
