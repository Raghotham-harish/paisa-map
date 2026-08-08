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
#
# NOT a row-count comparison — that heuristic wholesale-reverted a real
# content fix once already (found live 2026-08-08: 15,273 corrected rows
# got discarded because 3 stray live-visit rows made the backup's row
# count higher). merge_enrichment_file.py instead keeps the post-reset
# repo file as authoritative content and only adds pincodes the backup
# has that the repo doesn't — see that script's docstring for the story.
_save_if_newer() {
  local f="$1"
  local bak="/tmp/paisamap_$(basename $f).bak"
  [ -f "$f" ] && cp "$f" "$bak" || true
}
_restore_if_newer() {
  local f="$1"
  local bak="/tmp/paisamap_$(basename $f).bak"
  if [ -f "$bak" ] && [ -f "$f" ]; then
    "$REPO/paisamap-etl/venv/bin/python3" "$REPO/merge_enrichment_file.py" "$bak" "$f" \
      || echo "[deploy] WARN: merge failed for $f, keeping repo version as-is"
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

# The snapshot/reset/restore sequence below races enrich_single.py /
# batch_enrich_hces.py's own writes to these same files: a deploy can
# snapshot a file a moment BEFORE a live enrichment finishes writing it,
# then overwrite that fresh write with its own stale snapshot after the
# fact — a live user's real visit lost, no error anywhere (found
# 2026-08-08: an 11-pincode live visit vanished this way). Both those
# scripts serialize their own read-modify-write cycles through the same
# _filelock.py flock() on paisamap-etl/data/.write.lock — acquiring that
# same lock here (bash's flock is fully interoperable with Python's
# fcntl.flock() on the same file) closes the race: a live write in
# progress now makes this whole block wait, and vice versa, instead of
# either one silently clobbering the other.
LOCKFILE="$REPO/paisamap-etl/data/.write.lock"
mkdir -p "$(dirname "$LOCKFILE")"
echo "[deploy] acquiring write lock (shared with enrich_single.py/batch_enrich_hces.py)..."
(
  flock -x 200

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
) 200>"$LOCKFILE"
echo "[deploy] write lock released"

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

# Inject the ArcGIS API key into the DEPLOYED static copy only — the key never
# lives in the repo/git, just in this root-only file on the server (same
# pattern as /etc/paisamap/db.env for the Postgres password). No-op if that
# file was never created, so index.html stays inert-safe by default.
if [ -f /etc/paisamap/arcgis_api_key.env ]; then
  . /etc/paisamap/arcgis_api_key.env
  sudo sed -i "s|const ARCGIS_API_KEY = \"\";|const ARCGIS_API_KEY = \"${ARCGIS_API_KEY}\";|" \
    /var/www/paisamap/index.html
  echo "[deploy] ArcGIS API key injected into static index.html"
fi

# Install any new Python deps for Flask server
if [ -f venv-flask/bin/activate ]; then
  source venv-flask/bin/activate
  pip install -q flask openpyxl sqlalchemy psycopg2-binary
  deactivate
fi

# Install any new ETL deps
if [ -d paisamap-etl/venv ]; then
  paisamap-etl/venv/bin/pip install -q pandas requests pdfplumber scikit-learn sqlalchemy psycopg2-binary
fi

# Restart Flask service
sudo systemctl restart "$SERVICE"
echo "[deploy] $SERVICE restarted"

# Reload nginx only if config changed
sudo nginx -t && sudo systemctl reload nginx
echo "[deploy] nginx reloaded"

echo "[deploy] done ✓"
