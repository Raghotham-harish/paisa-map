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
#   4. Commit and push all touched data files directly — nothing else pushes
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
echo "[1/3] Pulling latest code..."
git fetch origin main --quiet
# Only fast-forward merge data files — don't discard local enrichment data
git merge --ff-only origin/main --quiet || {
    echo "  Fast-forward failed (local changes) — skipping pull"
}

# ── 2. Enrich user-visited pincodes ─────────────────────────────────────────────
echo ""
echo "[2/3] Auto-enriching user-visited pincodes (last 7 days)..."
"$PYTHON" "$ETL/etl/auto_enrich_visited.py" --days 7 || {
    echo "  auto_enrich_visited.py exited non-zero — continuing"
}

# ── 3. Batch pre-enrich HCES districts (30 per day) ─────────────────────────────
echo ""
echo "[3/3] Batch pre-enriching HCES districts (up to 30 today)..."
"$PYTHON" "$ETL/etl/batch_enrich_hces.py" --limit 30 || {
    echo "  batch_enrich_hces.py exited non-zero — continuing"
}

# ── 4. Stage any new/updated data files ─────────────────────────────────────────
echo ""
echo "Staging updated data files..."
cd "$REPO"
git add \
    data/output/enrichment_log.csv \
    data/output/ppi_map_data.csv \
    paisamap-etl/data/output/ppi_ml_refined.csv \
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
    git commit -m "cron: daily enrich ${DATE} — ${TOTAL} pincodes total [skip ci]" \
        --author "PaisaMap Cron <noreply@cooterlabs.com>" --quiet
    echo "  Committed. Total pincodes: $TOTAL"

    # ── 5. Push — rebase onto origin first since the 6h GitHub Actions sync
    #      and manual pushes can land while this script is mid-run ──────────
    echo ""
    echo "Pushing to origin/main..."
    if git fetch origin main --quiet && git rebase origin/main --quiet; then
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
