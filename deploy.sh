#!/usr/bin/env bash
# deploy.sh — runs on the server when GitHub Actions pushes to main
# Place this file at /home/ubuntu/deploy.sh on the server
set -euo pipefail

REPO="/home/ubuntu/paisa-map"   # adjust if repo is checked out elsewhere
SERVICE="paisamap"

echo "[deploy] $(date) — starting"

cd "$REPO"

# Preserve server-generated enrichment data before hard reset.
# git reset --hard would wipe new pincodes written since last sync.
# We keep whichever version (server vs repo) has more rows.
_save_if_newer() {
  local f="$1"
  local bak="/tmp/paisamap_$(basename $f).bak"
  [ -f "$f" ] && cp "$f" "$bak" || true
}
_restore_if_newer() {
  local f="$1"
  local bak="/tmp/paisamap_$(basename $f).bak"
  if [ -f "$bak" ] && [ -f "$f" ]; then
    local bak_lines=$(wc -l < "$bak")
    local repo_lines=$(wc -l < "$f")
    if [ "$bak_lines" -gt "$repo_lines" ]; then
      cp "$bak" "$f"
      echo "[deploy] restored server-side $f ($bak_lines rows > repo $repo_lines rows)"
    fi
  fi
}

# Every file enrich_single.py / batch_enrich_hces.py can write between
# deploys. cron_enrich.sh pushes these nightly, but a manual push landing
# mid-day (or a failed cron push) would otherwise be silently wiped here.
ENRICHMENT_FILES=(
  "data/output/enrichment_log.csv"
  "data/output/ppi_map_data.csv"
  "paisamap-etl/data/output/ppi_ml_refined.csv"
  "paisamap-etl/data/output/ppi_map_data.csv"
  "paisamap-etl/data/output/batch_enrich_log.csv"
  "paisamap-etl/data/raw/pincode_coords.csv"
  "paisamap-etl/data/raw/pincode_names.csv"
  "paisamap-etl/data/raw/property_rates.csv"
  "paisamap-etl/data/raw/bank_deposits.csv"
  "paisamap-etl/data/raw/nightlights.csv"
  "paisamap-etl/data/raw/poi_density.csv"
  "paisamap-etl/data/raw/itr_filers.csv"
  "paisamap-etl/data/raw/vehicle_density.csv"
  "paisamap-etl/data/raw/financial_inclusion.csv"
  "paisamap-etl/data/raw/rto_enhanced.csv"
  "paisamap-etl/data/reference/pincode_district_map.csv"
)

for f in "${ENRICHMENT_FILES[@]}"; do
  _save_if_newer "$f"
done

# Pull latest code
git fetch origin main
git reset --hard origin/main
echo "[deploy] code updated"

for f in "${ENRICHMENT_FILES[@]}"; do
  _restore_if_newer "$f"
done

# Mirror app files to nginx's static root (serves /data/, frontend assets;
# /api/ is proxied straight to the Flask service above, not through here).
sudo rsync -av --delete \
  --exclude='.git' \
  --exclude='.github' \
  --exclude='venv-flask' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  "$REPO/" \
  /var/www/paisamap/
echo "[deploy] synced to /var/www/paisamap"

# Install any new Python deps for Flask server
if [ -f venv-flask/bin/activate ]; then
  source venv-flask/bin/activate
  pip install -q flask
  deactivate
fi

# Install any new ETL deps
if [ -d paisamap-etl/venv ]; then
  paisamap-etl/venv/bin/pip install -q pandas requests pdfplumber scikit-learn
fi

# Restart Flask service
sudo systemctl restart "$SERVICE"
echo "[deploy] $SERVICE restarted"

# Reload nginx only if config changed
sudo nginx -t && sudo systemctl reload nginx
echo "[deploy] nginx reloaded"

echo "[deploy] done ✓"
