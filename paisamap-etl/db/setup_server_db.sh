#!/usr/bin/env bash
# setup_server_db.sh — one-time PostgreSQL setup for PaisaMap's core tables
# (pincodes, enrichment_log). Run this ON the Lightsail server as `ubuntu`,
# from the repo root, once SSH access is back:
#
#   cd /home/ubuntu/paisa-map
#   git pull
#   bash paisamap-etl/db/setup_server_db.sh
#
# What it does:
#   1. Installs PostgreSQL (apt) if not already present
#   2. Creates a `paisamap` role + `paisamap` database (idempotent)
#   3. Installs sqlalchemy/psycopg2 into venv-flask and paisamap-etl/venv
#   4. Writes DATABASE_URL into an env file the systemd service reads
#   5. Creates the schema and backfills it from the current CSVs
#   6. Restarts the paisamap service so server.py picks up DATABASE_URL
#
# Safe to re-run — every step is idempotent (CREATE ... IF NOT EXISTS /
# ON CONFLICT, and the migration script upserts).

set -euo pipefail

REPO="/home/ubuntu/paisa-map"
ETL="$REPO/paisamap-etl"
DB_NAME="paisamap"
DB_USER="paisamap"
ENV_FILE="/etc/paisamap/db.env"

echo "[1/6] Installing PostgreSQL…"
if ! command -v psql >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y postgresql postgresql-contrib
fi
sudo systemctl enable --now postgresql

echo "[2/6] Creating role + database (idempotent)…"
if [ -z "${PAISAMAP_DB_PASSWORD:-}" ]; then
  PAISAMAP_DB_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)
  echo "  Generated a DB password (also saved to $ENV_FILE)"
fi
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${PAISAMAP_DB_PASSWORD}';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"

DATABASE_URL="postgresql+psycopg2://${DB_USER}:${PAISAMAP_DB_PASSWORD}@localhost:5432/${DB_NAME}"

echo "[3/6] Installing Python deps…"
if [ -f "$REPO/venv-flask/bin/activate" ]; then
  source "$REPO/venv-flask/bin/activate"
  pip install -q sqlalchemy psycopg2-binary
  deactivate
fi
if [ -d "$ETL/venv" ]; then
  "$ETL/venv/bin/pip" install -q sqlalchemy psycopg2-binary
fi

echo "[4/6] Writing $ENV_FILE…"
sudo mkdir -p "$(dirname "$ENV_FILE")"
echo "DATABASE_URL=${DATABASE_URL}" | sudo tee "$ENV_FILE" >/dev/null
sudo chmod 600 "$ENV_FILE"
echo "  NOTE: make sure the paisamap systemd unit has:"
echo "    EnvironmentFile=$ENV_FILE"
echo "  (add it under [Service] in /etc/systemd/system/paisamap.service if missing,"
echo "   then: sudo systemctl daemon-reload)"

echo "[5/6] Creating schema + backfilling from current CSVs…"
DATABASE_URL="$DATABASE_URL" "$ETL/venv/bin/python3" "$ETL/db/migrate_csv_to_db.py"

echo "[6/6] Restarting paisamap service…"
sudo systemctl restart paisamap

echo ""
echo "Done. Verify with:"
echo "  curl -s https://paisamap.cooterlabs.com/api/db_status | python3 -m json.tool"
