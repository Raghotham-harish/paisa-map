#!/usr/bin/env bash
# deploy.sh — runs on the server when GitHub Actions pushes to main
# Place this file at /home/ubuntu/deploy.sh on the server
set -euo pipefail

REPO="/home/ubuntu/paisa-map"   # adjust if repo is checked out elsewhere
SERVICE="paisamap"

echo "[deploy] $(date) — starting"

cd "$REPO"

# Pull latest code
git fetch origin main
git reset --hard origin/main
echo "[deploy] code updated"

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
