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


-- Auth / workspace tables — PostgreSQL
--
-- Canonical definitions live in paisamap-etl/etl/_auth_db.py (SQLAlchemy Core,
-- same dual-target Postgres/SQLite pattern as _db.py above). This block is the
-- human-readable mirror — keep the two in sync.
--
-- Unlike pincodes/enrichment_log, these tables have no CSV fallback: a missing
-- DATABASE_URL means auth/workspace features are unavailable (503), not silently
-- degraded. See _auth_db.py's module docstring.

CREATE TABLE IF NOT EXISTS organizations (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    owner_user_id   INTEGER,               -- references users(id); left unenforced at the
                                            -- DB level here to avoid a hard creation-order
                                            -- cycle with users.org_id below
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    google_sub      TEXT NOT NULL UNIQUE,  -- Google's stable account id (JWT 'sub' claim) —
                                            -- not email, which can change/be reassigned
    email           TEXT NOT NULL UNIQUE,
    name            TEXT,
    picture_url     TEXT,
    plan            TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free','pro','team')),
    org_id          INTEGER REFERENCES organizations(id),   -- always NULL until team upgrade
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS org_members (
    id          SERIAL PRIMARY KEY,
    org_id      INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','admin','member')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_org_members_org_user UNIQUE (org_id, user_id)
);

CREATE TABLE IF NOT EXISTS projects (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id       INTEGER REFERENCES organizations(id),  -- NULL = personal project (Phase 0 default)
    name         TEXT NOT NULL,
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_projects_user_id ON projects (user_id);

CREATE TABLE IF NOT EXISTS saved_locations (
    id          SERIAL PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- denormalized for
                                                                           -- ownership checks
                                                                           -- without a join
    pincode     TEXT NOT NULL,        -- no FK to pincodes(pincode) — same convention as
                                       -- enrichment_log.pincode above
    name        TEXT,
    lat         DOUBLE PRECISION,
    lng         DOUBLE PRECISION,
    status      TEXT NOT NULL DEFAULT 'shortlist'
                CHECK (status IN ('shortlist','reviewing','approved','rejected')),
    tags        JSONB,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_saved_locations_project_pincode UNIQUE (project_id, pincode)
);

CREATE INDEX IF NOT EXISTS ix_saved_locations_project_id ON saved_locations (project_id);
CREATE INDEX IF NOT EXISTS ix_saved_locations_status ON saved_locations (status);

CREATE TABLE IF NOT EXISTS reports (
    id           SERIAL PRIMARY KEY,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    format       TEXT NOT NULL DEFAULT 'pdf' CHECK (format IN ('pdf','csv','xlsx')),
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','ready','failed')),
    file_path    TEXT,
    params       JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_reports_project_id ON reports (project_id);

CREATE TABLE IF NOT EXISTS credits_ledger (
    id             SERIAL PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta          INTEGER NOT NULL,       -- positive = grant, negative = spend
    reason         TEXT NOT NULL,          -- 'signup_bonus' | 'export' | 'report_generate' | ...
    ref_type       TEXT,
    ref_id         INTEGER,
    balance_after  INTEGER NOT NULL,       -- snapshot so balance reads are a single indexed lookup
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_credits_ledger_user_id ON credits_ledger (user_id, id DESC);

CREATE TABLE IF NOT EXISTS activity_log (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action       TEXT NOT NULL,        -- 'login' | 'project_create' | 'location_save' | ...
    target_type  TEXT,
    target_id    INTEGER,
    metadata     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_activity_log_user_id ON activity_log (user_id);
CREATE INDEX IF NOT EXISTS ix_activity_log_created_at ON activity_log (created_at);
