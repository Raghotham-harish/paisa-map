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

_save_if_newer "data/output/enrichment_log.csv"
_save_if_newer "data/output/ppi_map_data.csv"

# Pull latest code
git fetch origin main
git reset --hard origin/main
echo "[deploy] code updated"

_restore_if_newer "data/output/enrichment_log.csv"
_restore_if_newer "data/output/ppi_map_data.csv"

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
