-- PaisaMap core tables — PostgreSQL
--
-- Canonical definitions live in paisamap-etl/etl/_db.py (SQLAlchemy Core,
-- so the same code can target SQLite for local testing). This file is the
-- human-readable reference for anyone inspecting the database directly and
-- is what _db.init_schema() produces on Postgres. Keep the two in sync.
--
-- Replaces ppi_ml_refined.csv / ppi_map_data.csv (system of record for PPI,
-- income, spend) and enrichment_log.csv (audit trail of every enrichment
-- event). See ../../../.claude memory project_ppi_map_schema_incident.md
-- for why a real schema + transactions beats hand-rolled CSV rewrites.

CREATE TABLE IF NOT EXISTS pincodes (
    pincode                 TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    lat                     DOUBLE PRECISION NOT NULL,
    lng                     DOUBLE PRECISION NOT NULL,
    ppi_ml                  INTEGER NOT NULL,
    ppi_original            DOUBLE PRECISION,          -- NULL for spatially-interpolated new pincodes
    est_monthly_income_hh   DOUBLE PRECISION NOT NULL,
    est_monthly_spend_hh    DOUBLE PRECISION,
    updated_at              TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_pincodes_ppi_ml ON pincodes (ppi_ml);

CREATE TABLE IF NOT EXISTS enrichment_log (
    id       SERIAL PRIMARY KEY,
    ts       TIMESTAMPTZ NOT NULL,
    pincode  TEXT NOT NULL,
    name     TEXT,
    lat      DOUBLE PRECISION,
    lng      DOUBLE PRECISION,
    source   TEXT NOT NULL,                            -- yah | prefetch | search | manual | phase1
    ppi      INTEGER,
    income   DOUBLE PRECISION,
    CONSTRAINT uq_enrichment_log_event UNIQUE (ts, pincode, source)
);

CREATE INDEX IF NOT EXISTS ix_enrichment_log_pincode ON enrichment_log (pincode);
CREATE INDEX IF NOT EXISTS ix_enrichment_log_ts      ON enrichment_log (ts);
CREATE INDEX IF NOT EXISTS ix_enrichment_log_source  ON enrichment_log (source);
