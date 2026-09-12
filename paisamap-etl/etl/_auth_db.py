"""
_auth_db.py — Postgres connector for PaisaMap's auth/workspace tables: users,
organizations, org_members, projects, saved_locations, reports, credits_ledger,
activity_log.

Unlike _db.py (pincodes/enrichment_log — CSV is the system of record, DB is an
optional dual-write that silently no-ops if DATABASE_URL isn't set), these tables
have NO CSV fallback. There is nothing to fall back to for "who is signed in" or
"what did they save." Every write function here raises RuntimeError if the DB
isn't configured — callers (the auth/projects blueprints) turn that into a loud
503, never a silent empty response.

Reuses _db._get_engine() for the connection pool (one engine per DATABASE_URL
per process) but owns its own MetaData()/tables, exactly like _db.py owns its.

Set DATABASE_URL to enable, e.g.:
  postgresql+psycopg2://paisamap:PASSWORD@localhost:5432/paisamap   (prod)
  sqlite:///paisamap_dev.db                                        (local testing only)
"""

import threading
from datetime import datetime, timezone

import _db  # sibling module — reuses its engine, not its tables

_metadata = None
_tables = None
_lock = threading.Lock()


def _json_type(engine):
    """Dialect-conditional JSON column: JSONB on Postgres, plain JSON elsewhere
    (SQLite for local dev) — mirrors _db.py's dialect-branching pattern for
    on_conflict_do_update/nothing (_db.py's _upsert_stmt)."""
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB
        return JSONB
    from sqlalchemy import JSON
    return JSON


def enabled() -> bool:
    return _db._get_engine() is not None


def _require_engine():
    engine = _db._get_engine()
    if engine is None:
        raise RuntimeError("DATABASE_URL is not configured — auth/workspace features require it")
    return engine


def _get_tables():
    global _metadata, _tables
    if _tables is not None:
        return _tables
    engine = _require_engine()
    from sqlalchemy import (MetaData, Table, Column, Text, Integer, Float, Date,
                             DateTime, ForeignKey, UniqueConstraint, CheckConstraint)
    JSONType = _json_type(engine)
    _metadata = MetaData()

    organizations = Table(
        "organizations", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", Text, nullable=False),
        Column("owner_user_id", Integer),  # FK added at the DB level via schema.sql;
                                               # left unconstrained here to avoid a hard
                                               # creation-order cycle with users
        # Phase C, staged: plan/website_url exist on the org now so the
        # organizations blueprint has somewhere real to read/write, but
        # nothing else in the app resolves plan from here yet — users.plan
        # is still the one actually enforced (see the Organizations section
        # of this file for why that cutover is deliberately separate).
        Column("plan", Text, nullable=False, server_default="free"),
        Column("website_url", Text),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )

    users = Table(
        "users", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("google_sub", Text, nullable=False, unique=True),
        Column("email", Text, nullable=False, unique=True),
        Column("name", Text),
        Column("picture_url", Text),
        Column("plan", Text, nullable=False, server_default="free"),
        Column("org_id", Integer, ForeignKey("organizations.id")),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("last_login_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("plan IN ('free','pro','team')", name="ck_users_plan"),
    )

    org_members = Table(
        "org_members", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("org_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("role", Text, nullable=False, server_default="member"),
        Column("created_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("org_id", "user_id", name="uq_org_members_org_user"),
        CheckConstraint("role IN ('owner','admin','member')", name="ck_org_members_role"),
    )

    # Phase D1: invites for an email with no PaisaMap account yet. A known
    # email short-circuits straight into org_members (see add_org_member) —
    # this table only exists for the "hasn't signed up" case.
    org_invites = Table(
        "org_invites", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("org_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("email", Text, nullable=False),
        Column("role", Text, nullable=False, server_default="member"),
        Column("token", Text, nullable=False, unique=True),
        Column("invited_by_user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("status", Text, nullable=False, server_default="pending"),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
        Column("accepted_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("responded_at", DateTime(timezone=True)),
        CheckConstraint("role IN ('owner','admin','member')", name="ck_org_invites_role"),
        CheckConstraint("status IN ('pending','accepted','declined','revoked')", name="ck_org_invites_status"),
    )

    projects = Table(
        "projects", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("org_id", Integer, ForeignKey("organizations.id")),
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("business_type", Text),
        Column("target_segment", Text),
        Column("avg_ticket", Float),
        Column("website_url", Text),
        Column("analytics_consent_at", DateTime(timezone=True)),
        # Project-setup wizard fields (map-first workspace). signals /
        # target_pincodes are JSON arrays stored as text — same "no JSON column
        # type, for SQLite portability" convention used elsewhere in this file.
        Column("industry", Text),
        Column("signals", Text),
        Column("target_pincodes", Text),
        Column("catchment_km", Float),
        Column("total_investment", Float),
        Column("outcome_goal", Text),
        Column("time_horizon_months", Integer),
        # Forecast model inputs (P3) — gross margin drives payback, revenue_period
        # says whether an uploaded store's `revenue` figure is monthly or annual.
        Column("gross_margin_pct", Float),
        Column("revenue_period", Text),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        # Phase E3/E2 — additive, see _MIGRATIONS below for the ALTER TABLE.
        Column("archived_at", DateTime(timezone=True)),
        Column("share_token", Text),
    )

    saved_locations = Table(
        "saved_locations", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("pincode", Text, nullable=False),  # no FK to pincodes(pincode) — same
                                                    # convention as enrichment_log.pincode
        Column("name", Text),
        Column("lat", Float),
        Column("lng", Float),
        Column("status", Text, nullable=False, server_default="shortlist"),
        Column("tags", JSONType),
        Column("notes", Text),
        # ₹ this project's plan allocates to this specific site — the compare
        # view's shortlist panel (map-first workspace). Purely user-entered,
        # not derived from the forecast model.
        Column("allocated_investment", Float),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("project_id", "pincode", name="uq_saved_locations_project_pincode"),
        CheckConstraint("status IN ('shortlist','reviewing','approved','rejected')",
                         name="ck_saved_locations_status"),
    )

    reports = Table(
        "reports", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("title", Text, nullable=False),
        Column("format", Text, nullable=False, server_default="pdf"),
        Column("status", Text, nullable=False, server_default="pending"),
        Column("file_path", Text),
        Column("params", JSONType),
        Column("share_token", Text, unique=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("completed_at", DateTime(timezone=True)),
        CheckConstraint("format IN ('pdf','csv','xlsx')", name="ck_reports_format"),
        CheckConstraint("status IN ('pending','processing','ready','failed')", name="ck_reports_status"),
    )

    credits_ledger = Table(
        "credits_ledger", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # Phase C, staged: NULL until credits pooling actually cuts over to the
        # org level — every existing/new row keeps writing user_id as today.
        Column("org_id", Integer, ForeignKey("organizations.id")),
        Column("delta", Integer, nullable=False),
        Column("reason", Text, nullable=False),
        Column("ref_type", Text),
        Column("ref_id", Integer),
        Column("balance_after", Integer, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )

    activity_log = Table(
        "activity_log", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("action", Text, nullable=False),
        Column("target_type", Text),
        Column("target_id", Integer),
        Column("metadata", JSONType),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )

    customer_uploads = Table(
        "customer_uploads", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        # Phase C, staged: NULL until store-data sharing across a company's
        # projects actually cuts over — see the Organizations section above.
        Column("org_id", Integer, ForeignKey("organizations.id")),
        Column("filename", Text, nullable=False),
        Column("format", Text, nullable=False),
        Column("status", Text, nullable=False, server_default="pending_mapping"),
        Column("headers", JSONType),
        Column("raw_rows", JSONType),
        Column("mapping", JSONType),
        Column("quality_report", JSONType),
        Column("error", Text),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("format IN ('csv','xlsx')", name="ck_customer_uploads_format"),
        CheckConstraint("status IN ('pending_mapping','geocoding','ready','failed')",
                         name="ck_customer_uploads_status"),
    )

    customer_locations = Table(
        "customer_locations", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        # Phase C, staged: NULL until store-data sharing across a company's
        # projects actually cuts over — see the Organizations section above.
        Column("org_id", Integer, ForeignKey("organizations.id")),
        Column("upload_id", Integer, ForeignKey("customer_uploads.id", ondelete="CASCADE"), nullable=False),
        Column("store_name", Text),
        Column("raw_address", Text),
        Column("pincode", Text),  # no FK — same convention as saved_locations.pincode
        Column("lat", Float),
        Column("lng", Float),
        Column("geocode_status", Text, nullable=False, server_default="pending"),
        # Phase H1, staged: revenue/rent/capex/raw_address above are the
        # legacy plaintext columns, kept for now so pre-migration rows keep
        # reading correctly (see _MIGRATIONS' encrypted twins below and
        # create_customer_locations_bulk / list_customer_locations /
        # list_pending_geocode_locations for the dual-read/encrypt-on-write
        # logic). A later, separately-approved backfill+cutover step will
        # encrypt existing rows and eventually drop these plaintext columns.
        Column("revenue", Float),
        Column("rent", Float),
        Column("capex", Float),
        Column("extra_fields", JSONType),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        Column("revenue_encrypted", Text),
        Column("rent_encrypted", Text),
        Column("capex_encrypted", Text),
        Column("raw_address_encrypted", Text),
        CheckConstraint(
            "geocode_status IN ('direct','pending','geocoded','failed','unresolvable')",
            name="ck_customer_locations_geocode_status",
        ),
    )

    oauth_connections = Table(
        "oauth_connections", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # Phase C5: a connection belongs to the company, not any one project —
        # project_id above is now just "which project's Connect flow created
        # this row" (kept for the picker's "connected via <project>" display),
        # not an access-control boundary. See project_connection_selections
        # below for which connection(s) each project actually uses.
        Column("org_id", Integer, ForeignKey("organizations.id")),
        Column("provider", Text, nullable=False),
        Column("status", Text, nullable=False, server_default="connected"),
        Column("external_account_email", Text),
        Column("scopes", Text),
        Column("access_token_encrypted", Text, nullable=False),
        Column("refresh_token_encrypted", Text),
        Column("token_expiry", DateTime(timezone=True)),
        Column("external_ref", Text),  # GA4 property id or GSC site URL, chosen after connecting
        Column("last_error", Text),
        Column("connected_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("project_id", "provider", name="uq_oauth_connections_project_provider"),
        CheckConstraint("provider IN ('google_analytics','search_console')",
                         name="ck_oauth_connections_provider"),
        CheckConstraint("status IN ('connected','error')", name="ck_oauth_connections_status"),
    )

    # Phase C5: which oauth_connection a project actually uses, per provider —
    # decoupled from "who created it" (oauth_connections.project_id above) so
    # a company's projects can each pick from the same shared pool of
    # connections instead of every project needing its own. Always written
    # explicitly (at connect time, and whenever a project switches which
    # connection it uses) — no implicit/legacy fallback, so resolution is a
    # single lookup, not a two-path "selection row, else guess from project_id".
    project_connection_selections = Table(
        "project_connection_selections", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("provider", Text, nullable=False),
        Column("oauth_connection_id", Integer, ForeignKey("oauth_connections.id", ondelete="CASCADE"), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("project_id", "provider", name="uq_project_connection_selections_project_provider"),
    )

    orders = Table(
        "orders", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("kind", Text, nullable=False),  # 'credit_pack' | 'plan_upgrade' | 'report_purchase'
        Column("razorpay_order_id", Text, nullable=False, unique=True),
        Column("razorpay_payment_id", Text),   # filled on verification
        Column("razorpay_signature", Text),    # filled on verification (audit trail)
        Column("amount_paise", Integer, nullable=False),
        Column("currency", Text, nullable=False, server_default="INR"),
        Column("status", Text, nullable=False, server_default="created"),
        # exactly one of these three is populated, depending on `kind`:
        Column("credit_pack_id", Text),        # key into _pricing.CREDIT_PACKS
        Column("target_plan", Text),           # 'pro' | 'team'
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="SET NULL")),
        Column("report_id", Integer, ForeignKey("reports.id", ondelete="SET NULL")),  # linked
                                                # once the paid-for report is actually generated
        Column("meta", JSONType),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("paid_at", DateTime(timezone=True)),
        CheckConstraint("kind IN ('credit_pack','plan_upgrade','report_purchase')",
                         name="ck_orders_kind"),
        CheckConstraint("status IN ('created','paid','failed','refunded')",
                         name="ck_orders_status"),
    )

    invoices = Table(
        "invoices", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("order_id", Integer, ForeignKey("orders.id", ondelete="CASCADE"),
               nullable=False, unique=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("invoice_number", Text, nullable=False, unique=True),  # "PM-<year>-<seq>"
        Column("buyer_name", Text),
        Column("buyer_email", Text, nullable=False),
        Column("buyer_gstin", Text),           # optional — B2C buyers have none
        Column("seller_gstin", Text),          # PaisaMap's own GSTIN, from env — not user input
        Column("taxable_amount_paise", Integer, nullable=False),
        Column("gst_rate", Float, nullable=False),
        Column("gst_amount_paise", Integer, nullable=False),
        Column("total_amount_paise", Integer, nullable=False),
        Column("line_item_label", Text, nullable=False),
        Column("file_path", Text),
        Column("created_at", DateTime(timezone=True), nullable=False),
        CheckConstraint("gst_rate >= 0", name="ck_invoices_gst_rate"),
    )

    # Track 1 (B2B data-API product) — a key never stores anything recoverable,
    # only a SHA-256 hash (see _api_keys.py); tier is NOT stored here at all —
    # it's resolved live from users.plan on every request (a plan upgrade
    # elevates existing keys immediately, no separate API-product purchase
    # flow to build). usage_count_today/usage_reset_at are for visibility in
    # the key-management UI only, not quota enforcement — no real per-tier
    # numbers exist yet (that's a separate, later pricing decision).
    api_keys = Table(
        "api_keys", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("key_hash", Text, nullable=False, unique=True),
        Column("key_prefix", Text, nullable=False),  # first ~8 chars of the raw key, for
                                                        # display — never enough to guess the rest
        Column("label", Text),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("last_used_at", DateTime(timezone=True)),
        Column("revoked_at", DateTime(timezone=True)),
        Column("usage_count_today", Integer, nullable=False, server_default="0"),
        Column("usage_reset_at", Date),
    )

    _tables = {
        "organizations": organizations, "users": users, "org_members": org_members,
        "org_invites": org_invites,
        "projects": projects, "saved_locations": saved_locations, "reports": reports,
        "credits_ledger": credits_ledger, "activity_log": activity_log,
        "customer_uploads": customer_uploads, "customer_locations": customer_locations,
        "oauth_connections": oauth_connections,
        "project_connection_selections": project_connection_selections,
        "orders": orders, "invoices": invoices,
        "api_keys": api_keys,
    }
    return _tables


def init_schema():
    """Create all tables if they don't exist yet. Raises if DATABASE_URL unset —
    callers (init_auth_schema.py) are expected to check enabled() first and print
    a friendly message rather than let this raise."""
    engine = _require_engine()
    tables = _get_tables()
    # organizations before users (users.org_id references it) — no real cycle since
    # organizations.owner_user_id is left unconstrained at the SQLAlchemy level.
    # orders/invoices last — both reference users/projects/reports, which must
    # already exist.
    _metadata.create_all(engine, tables=[
        tables["organizations"], tables["users"], tables["org_members"],
        tables["org_invites"],
        tables["projects"], tables["saved_locations"], tables["reports"],
        tables["credits_ledger"], tables["activity_log"],
        tables["customer_uploads"], tables["customer_locations"],
        tables["oauth_connections"], tables["project_connection_selections"],
        tables["orders"], tables["invoices"],
        tables["api_keys"],
    ])
    _ensure_invoice_sequence(engine)


def _ensure_invoice_sequence(engine):
    """Postgres SEQUENCE backing sequential invoice numbers — avoids a race two
    concurrent payments could hit with a naive count()+1. No-op on SQLite
    (local dev only, single-writer, _next_invoice_number falls back to
    MAX(id)+1 there instead)."""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("CREATE SEQUENCE IF NOT EXISTS invoice_seq"))


# Additive column changes to tables that may already exist in production —
# create_all() above only creates missing TABLES, it never alters an existing
# one, so a new column needs its own idempotent step. No formal migration
# framework in this codebase (matches its existing "small, documented,
# idempotent script" style elsewhere, e.g. merge_enrichment_file.py) — new
# additive changes should add another (table, column, sql_type) entry here.
_MIGRATIONS = [
    ("projects", "business_type", "TEXT"),
    ("projects", "target_segment", "TEXT"),
    ("projects", "avg_ticket", "FLOAT"),
    ("projects", "website_url", "TEXT"),
    ("reports", "share_token", "TEXT"),
    ("projects", "analytics_consent_at", "TIMESTAMP"),
    ("projects", "industry", "TEXT"),
    ("projects", "signals", "TEXT"),
    ("projects", "target_pincodes", "TEXT"),
    ("projects", "catchment_km", "FLOAT"),
    ("projects", "total_investment", "FLOAT"),
    ("projects", "outcome_goal", "TEXT"),
    ("projects", "time_horizon_months", "INTEGER"),
    ("projects", "gross_margin_pct", "FLOAT"),
    ("projects", "revenue_period", "TEXT"),
    ("saved_locations", "allocated_investment", "FLOAT"),
    # Phase C, staged (organizations layer) — additive only, nothing reads
    # these yet. See the Organizations section of this file.
    ("organizations", "plan", "TEXT DEFAULT 'free'"),
    ("organizations", "website_url", "TEXT"),
    ("credits_ledger", "org_id", "INTEGER"),
    ("customer_uploads", "org_id", "INTEGER"),
    ("customer_locations", "org_id", "INTEGER"),
    ("oauth_connections", "org_id", "INTEGER"),
    # Phase E3/E2 — additive, nothing reads these until the functions below ship.
    ("projects", "archived_at", "TIMESTAMP"),
    ("projects", "share_token", "TEXT"),
    # Phase H1 — additive, staged (see the Table definition above for the
    # dual-read/encrypt-on-write plan; legacy plaintext columns stay put).
    ("customer_locations", "revenue_encrypted", "TEXT"),
    ("customer_locations", "rent_encrypted", "TEXT"),
    ("customer_locations", "capex_encrypted", "TEXT"),
    ("customer_locations", "raw_address_encrypted", "TEXT"),
]


def migrate_schema():
    """Add any missing columns from _MIGRATIONS — safe to call every time,
    including against a fresh DB where create_all() already added them.
    SQLite's ALTER TABLE has no ADD COLUMN IF NOT EXISTS (unlike Postgres
    9.6+), so existence is checked per-dialect instead of relied on syntax."""
    engine = _require_engine()
    from sqlalchemy import text, inspect
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column, sql_type in _MIGRATIONS:
            existing_cols = {c["name"] for c in inspector.get_columns(table)}
            if column in existing_cols:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def _now():
    return datetime.now(timezone.utc)


# ── Users ────────────────────────────────────────────────────────────────────
def upsert_user(google_sub, email, name, picture_url):
    """Insert or update a user by google_sub. Returns {"id","plan","created"} —
    "created" tells the auth blueprint whether to grant a signup bonus."""
    engine = _require_engine()
    tables = _get_tables()
    users = tables["users"]
    from sqlalchemy import select

    with engine.begin() as conn:
        existing = conn.execute(
            select(users.c.id, users.c.plan).where(users.c.google_sub == google_sub)
        ).first()
        now = _now()
        if existing:
            conn.execute(
                users.update().where(users.c.id == existing.id).values(
                    email=email, name=name, picture_url=picture_url, last_login_at=now
                )
            )
            return {"id": existing.id, "plan": existing.plan, "created": False}

        result = conn.execute(
            users.insert().values(
                google_sub=google_sub, email=email, name=name, picture_url=picture_url,
                plan="free", created_at=now, last_login_at=now,
            )
        )
        new_id = result.inserted_primary_key[0]
        return {"id": new_id, "plan": "free", "created": True}


def get_user(user_id):
    engine = _require_engine()
    tables = _get_tables()
    users = tables["users"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(users.c.id, users.c.email, users.c.name, users.c.picture_url, users.c.plan)
            .where(users.c.id == user_id)
        ).mappings().first()
    return dict(row) if row else None


# ── Credits ──────────────────────────────────────────────────────────────────
def grant_credits(user_id, amount, reason, ref_type=None, ref_id=None, conn=None):
    """Append a credits_ledger row and return the new balance. If `conn` is given,
    runs inside the caller's transaction (used by the signup flow to grant the
    bonus atomically with the user creation); otherwise opens its own."""
    tables = _get_tables()
    ledger = tables["credits_ledger"]

    def _do(c):
        from sqlalchemy import select
        prev = c.execute(
            select(ledger.c.balance_after).where(ledger.c.user_id == user_id)
            .order_by(ledger.c.id.desc()).limit(1)
        ).scalar()
        new_balance = (prev or 0) + amount
        c.execute(ledger.insert().values(
            user_id=user_id, delta=amount, reason=reason, ref_type=ref_type, ref_id=ref_id,
            balance_after=new_balance, created_at=_now(),
        ))
        return new_balance

    if conn is not None:
        return _do(conn)
    engine = _require_engine()
    with engine.begin() as c:
        return _do(c)


def get_credit_balance(user_id):
    engine = _require_engine()
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    from sqlalchemy import select
    with engine.connect() as conn:
        bal = conn.execute(
            select(ledger.c.balance_after).where(ledger.c.user_id == user_id)
            .order_by(ledger.c.id.desc()).limit(1)
        ).scalar()
    return bal or 0


class InsufficientCreditsError(Exception):
    """Raised by spend_credits() when the balance is short. The caller (a
    blueprint route) turns this into a 402 Payment Required with the current
    balance and the shortfall, so the frontend can offer a top-up or a one-off
    purchase inline instead of a bare error."""
    def __init__(self, balance, required):
        self.balance = balance
        self.required = required
        super().__init__(f"insufficient credits: have {balance}, need {required}")


def spend_credits(user_id, amount, reason, ref_type=None, ref_id=None):
    """Debit `amount` credits if the balance covers it, atomically. Raises
    InsufficientCreditsError (balance left unchanged) if not. Unlike
    grant_credits() above (read-then-insert, fine since it only ever adds),
    this locks the latest ledger row with SELECT ... FOR UPDATE on Postgres so
    two concurrent spends for the same user can't both read the same stale
    balance and drive it negative. SQLite (local dev only) has no row-level
    locking but is single-writer by default, so it degrades safely without it."""
    assert amount > 0, "spend_credits amount must be positive"
    engine = _require_engine()
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    from sqlalchemy import select
    with engine.begin() as conn:
        query = (select(ledger.c.balance_after).where(ledger.c.user_id == user_id)
                 .order_by(ledger.c.id.desc()).limit(1))
        if engine.dialect.name == "postgresql":
            query = query.with_for_update()
        balance = conn.execute(query).scalar() or 0
        if balance < amount:
            raise InsufficientCreditsError(balance, amount)
        new_balance = balance - amount
        conn.execute(ledger.insert().values(
            user_id=user_id, delta=-amount, reason=reason, ref_type=ref_type, ref_id=ref_id,
            balance_after=new_balance, created_at=_now(),
        ))
        return new_balance


# ── Plan ─────────────────────────────────────────────────────────────────────
def set_user_plan(user_id, plan):
    """First-ever writer of users.plan post-signup (upsert_user only ever sets
    it to 'free' at creation). No plan-history table this phase — activity_log
    already covers "when did this happen" well enough."""
    assert plan in ("free", "pro", "team")
    engine = _require_engine()
    tables = _get_tables()
    users = tables["users"]
    with engine.begin() as conn:
        conn.execute(users.update().where(users.c.id == user_id).values(plan=plan))
    return get_user(user_id)


# ── Orders (Razorpay) ────────────────────────────────────────────────────────
def create_order(user_id, kind, razorpay_order_id, amount_paise, *, credit_pack_id=None,
                  target_plan=None, project_id=None, report_id=None, meta=None):
    engine = _require_engine()
    tables = _get_tables()
    orders = tables["orders"]
    with engine.begin() as conn:
        result = conn.execute(
            orders.insert().values(
                user_id=user_id, kind=kind, razorpay_order_id=razorpay_order_id,
                amount_paise=amount_paise, currency="INR", status="created",
                credit_pack_id=credit_pack_id, target_plan=target_plan,
                project_id=project_id, report_id=report_id, meta=meta,
                created_at=_now(),
            )
        )
        new_id = result.inserted_primary_key[0]
    return get_order(new_id, user_id)


def get_order(order_id, user_id):
    """Ownership-scoped, same convention as get_project/get_report."""
    engine = _require_engine()
    tables = _get_tables()
    orders = tables["orders"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(orders).where(orders.c.id == order_id, orders.c.user_id == user_id)
        ).mappings().first()
    return dict(row) if row else None


def get_order_by_razorpay_id(razorpay_order_id):
    """Unscoped by design — the webhook has no Flask session, only whatever
    Razorpay's payload gives it (the razorpay_order_id), so there's no user_id
    to scope by until after this lookup."""
    engine = _require_engine()
    tables = _get_tables()
    orders = tables["orders"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(orders).where(orders.c.razorpay_order_id == razorpay_order_id)
        ).mappings().first()
    return dict(row) if row else None


def mark_order_paid(order_id, razorpay_payment_id, razorpay_signature):
    """UPDATE ... WHERE status='created' — the rowcount tells the caller
    whether this call actually transitioned the order (rowcount 1) or the
    order was already paid (rowcount 0), which matters because Razorpay
    webhooks can be redelivered and the client-side /verify call can race the
    webhook for the same payment. Callers must treat rowcount 0 as "already
    handled, no-op the side effect" rather than double-granting credits or
    re-setting a plan."""
    engine = _require_engine()
    tables = _get_tables()
    orders = tables["orders"]
    from sqlalchemy import select
    with engine.begin() as conn:
        result = conn.execute(
            orders.update()
            .where(orders.c.id == order_id, orders.c.status == "created")
            .values(status="paid", razorpay_payment_id=razorpay_payment_id,
                    razorpay_signature=razorpay_signature, paid_at=_now())
        )
        newly_paid = result.rowcount > 0
        row = conn.execute(select(orders).where(orders.c.id == order_id)).mappings().first()
    return (dict(row) if row else None), newly_paid


def link_order_to_report(order_id, report_id):
    engine = _require_engine()
    tables = _get_tables()
    orders = tables["orders"]
    with engine.begin() as conn:
        conn.execute(orders.update().where(orders.c.id == order_id).values(report_id=report_id))


def list_orders(user_id, limit=50):
    engine = _require_engine()
    tables = _get_tables()
    orders = tables["orders"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(orders).where(orders.c.user_id == user_id)
            .order_by(orders.c.id.desc()).limit(limit)
        ).mappings().all()
    return [dict(r) for r in rows]


# ── Invoices ─────────────────────────────────────────────────────────────────
def _next_invoice_number(conn, engine):
    """Sequential, per-year: PM-<year>-<zero-padded-seq>. Uses a real Postgres
    SEQUENCE (see _ensure_invoice_sequence) for correctness under concurrency —
    two payments settling at once must never compute the same "next" number.
    SQLite (local dev only, single-writer) falls back to a naive MAX(id)+1."""
    year = _now().year
    if engine.dialect.name == "postgresql":
        from sqlalchemy import text
        seq = conn.execute(text("SELECT nextval('invoice_seq')")).scalar()
    else:
        from sqlalchemy import text
        seq = (conn.execute(text("SELECT COALESCE(MAX(id), 0) + 1 FROM invoices")).scalar())
    return f"PM-{year}-{seq:06d}"


def create_invoice(order_id, user_id, buyer_email, taxable_amount_paise, gst_amount_paise,
                    total_amount_paise, line_item_label, *, buyer_name=None, buyer_gstin=None,
                    seller_gstin=None):
    import _pricing
    engine = _require_engine()
    tables = _get_tables()
    invoices = tables["invoices"]
    with engine.begin() as conn:
        invoice_number = _next_invoice_number(conn, engine)
        result = conn.execute(
            invoices.insert().values(
                order_id=order_id, user_id=user_id, invoice_number=invoice_number,
                buyer_name=buyer_name, buyer_email=buyer_email, buyer_gstin=buyer_gstin,
                seller_gstin=seller_gstin, taxable_amount_paise=taxable_amount_paise,
                gst_rate=_pricing.GST_RATE, gst_amount_paise=gst_amount_paise,
                total_amount_paise=total_amount_paise, line_item_label=line_item_label,
                created_at=_now(),
            )
        )
        new_id = result.inserted_primary_key[0]
    return get_invoice(new_id, user_id)


def get_invoice(invoice_id, user_id):
    engine = _require_engine()
    tables = _get_tables()
    invoices = tables["invoices"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(invoices).where(invoices.c.id == invoice_id, invoices.c.user_id == user_id)
        ).mappings().first()
    return dict(row) if row else None


def list_invoices(user_id):
    engine = _require_engine()
    tables = _get_tables()
    invoices = tables["invoices"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(invoices).where(invoices.c.user_id == user_id)
            .order_by(invoices.c.created_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def update_invoice_file_path(invoice_id, file_path):
    engine = _require_engine()
    tables = _get_tables()
    invoices = tables["invoices"]
    with engine.begin() as conn:
        conn.execute(invoices.update().where(invoices.c.id == invoice_id).values(file_path=file_path))


# ── Activity log ─────────────────────────────────────────────────────────────
def log_activity(user_id, action, target_type=None, target_id=None, metadata=None, conn=None):
    tables = _get_tables()
    log = tables["activity_log"]
    values = dict(user_id=user_id, action=action, target_type=target_type,
                  target_id=target_id, metadata=metadata, created_at=_now())
    if conn is not None:
        conn.execute(log.insert().values(**values))
        return
    engine = _require_engine()
    with engine.begin() as c:
        c.execute(log.insert().values(**values))


def get_user_by_email(email):
    engine = _require_engine()
    tables = _get_tables()
    users = tables["users"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(users.c.id, users.c.email, users.c.name, users.c.picture_url)
            .where(users.c.email == email)
        ).mappings().first()
    return dict(row) if row else None


# ── Organizations (Phase C, staged) ───────────────────────────────────────────
# Additive-only for now: real orgs + membership, but nothing existing
# (users.plan, credits_ledger, project/connection/upload ownership) reads from
# an org yet. Cutting those over is a separate, later step — plan/credits sit
# on top of a payment surface (orders, invoices, Razorpay webhooks, api_keys
# tier resolution) that's all wired straight to user_id today, and getting
# that migration wrong touches real money. This section only ships the
# container + membership so the frontend has something real to build a
# company switcher against; org_id columns added elsewhere in this file
# (credits_ledger, customer_uploads, customer_locations, oauth_connections)
# stay NULL until that later cutover actually writes to them.

ORG_ROLES = ("owner", "admin", "member")


def create_organization(user_id, name):
    """Creates the org and adds the creator as 'owner' in one transaction."""
    engine = _require_engine()
    tables = _get_tables()
    orgs = tables["organizations"]
    members = tables["org_members"]
    now = _now()
    with engine.begin() as conn:
        result = conn.execute(
            orgs.insert().values(name=name, owner_user_id=user_id, plan="free", created_at=now)
        )
        new_id = result.inserted_primary_key[0]
        conn.execute(
            members.insert().values(org_id=new_id, user_id=user_id, role="owner", created_at=now)
        )
    return get_organization(new_id, user_id)


def create_default_organization_for_user(user_id, name, plan="free"):
    """Like create_organization, but also stamps users.org_id — for the one
    org that's this user's *primary* company (their first one, created for
    them rather than by them: at signup, or by C3's backfill for pre-existing
    accounts). A user later creating an additional company via
    create_organization deliberately does NOT touch users.org_id — that stays
    pointing at their original primary org."""
    engine = _require_engine()
    tables = _get_tables()
    orgs = tables["organizations"]
    members = tables["org_members"]
    users = tables["users"]
    now = _now()
    with engine.begin() as conn:
        result = conn.execute(
            orgs.insert().values(name=name, owner_user_id=user_id, plan=plan, created_at=now)
        )
        new_id = result.inserted_primary_key[0]
        conn.execute(
            members.insert().values(org_id=new_id, user_id=user_id, role="owner", created_at=now)
        )
        conn.execute(users.update().where(users.c.id == user_id).values(org_id=new_id))
    return get_organization(new_id, user_id)


def get_org_role(org_id, user_id):
    """The caller's role in this org, or None if they're not a member."""
    engine = _require_engine()
    tables = _get_tables()
    members = tables["org_members"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(members.c.role).where(members.c.org_id == org_id, members.c.user_id == user_id)
        ).first()
    return row.role if row else None


def list_member_org_ids(user_id):
    """Every org this user belongs to, any role — used to scope "everything
    my company can see" queries (list_projects and anything gated through
    project access below)."""
    engine = _require_engine()
    tables = _get_tables()
    members = tables["org_members"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(select(members.c.org_id).where(members.c.user_id == user_id)).scalars().all()
    return list(rows)


def _project_role(project, user_id):
    """Phase D2: the caller's effective permission level on an already-loaded
    project row — 'owner' if they're the literal creator (kept at owner-tier
    regardless of their org role, so today's single-owner behavior never
    regresses) or if they're the org's own owner; otherwise their real org
    role ('admin'/'member'); None if they have no relationship to it at all.
    Read + write are anything-but-None; delete requires 'owner'/'admin' —
    see PROJECT_DELETE_ROLES below (member creates/edits, can't delete)."""
    if project is None:
        return None
    if project["user_id"] == user_id:
        return "owner"
    if project.get("org_id") is not None:
        return get_org_role(project["org_id"], user_id)
    return None


PROJECT_DELETE_ROLES = ("owner", "admin")


def _load_project_row(project_id):
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(select(projects).where(projects.c.id == project_id)).mappings().first()
    return dict(row) if row else None


def get_project_role(project_id, user_id):
    """Public version of _project_role for callers (blueprints) that need to
    know *which* role applies, not just whether access exists — e.g. to show
    or hide a Delete button. Loads the row itself; prefer _project_role when
    you already have the row (avoids a second query)."""
    return _project_role(_load_project_row(project_id), user_id)


def list_organizations(user_id):
    """Orgs the caller is a member of, with their role in each."""
    engine = _require_engine()
    tables = _get_tables()
    orgs = tables["organizations"]
    members = tables["org_members"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(orgs, members.c.role)
            .select_from(orgs.join(members, members.c.org_id == orgs.c.id))
            .where(members.c.user_id == user_id)
            .order_by(orgs.c.created_at.asc())
        ).mappings().all()
    return [dict(r) for r in rows]


def get_organization(org_id, user_id):
    """Membership-scoped — returns None if the org doesn't exist or the caller
    isn't a member (same not-found-vs-not-a-member non-disclosure as get_project)."""
    role = get_org_role(org_id, user_id)
    if role is None:
        return None
    engine = _require_engine()
    tables = _get_tables()
    orgs = tables["organizations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(select(orgs).where(orgs.c.id == org_id)).mappings().first()
    if row is None:
        return None
    out = dict(row)
    out["role"] = role
    return out


def update_organization(org_id, user_id, **fields):
    """Owner/admin only."""
    role = get_org_role(org_id, user_id)
    if role not in ("owner", "admin"):
        return None
    allowed = {k: v for k, v in fields.items() if k in ("name", "website_url") and v is not None}
    if not allowed:
        return get_organization(org_id, user_id)
    engine = _require_engine()
    tables = _get_tables()
    orgs = tables["organizations"]
    with engine.begin() as conn:
        conn.execute(orgs.update().where(orgs.c.id == org_id).values(**allowed))
    return get_organization(org_id, user_id)


def delete_organization(org_id, user_id):
    """Owner only."""
    role = get_org_role(org_id, user_id)
    if role != "owner":
        return False
    engine = _require_engine()
    tables = _get_tables()
    orgs = tables["organizations"]
    with engine.begin() as conn:
        result = conn.execute(orgs.delete().where(orgs.c.id == org_id))
    return result.rowcount > 0


def list_org_members(org_id, user_id):
    """Any member can view the roster."""
    if get_org_role(org_id, user_id) is None:
        return None
    engine = _require_engine()
    tables = _get_tables()
    members = tables["org_members"]
    users = tables["users"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(members.c.id, members.c.role, members.c.created_at,
                   users.c.id.label("user_id"), users.c.email, users.c.name, users.c.picture_url)
            .select_from(members.join(users, users.c.id == members.c.user_id))
            .where(members.c.org_id == org_id)
            .order_by(members.c.created_at.asc())
        ).mappings().all()
    return [dict(r) for r in rows]


# Phase D4: "who viewed/exported what", admin-visible — a deliberate subset of
# every activity_log action, not the full personal feed. login/purchase/
# connection_connected/etc. stay off this view; they're account events, not
# data access. Anonymous data access (a public /api/export call, or a
# report_share_view with no session) has no user_id to attribute to any
# org's roster and simply won't appear here — an honest limitation given
# real production has exactly one org to verify a fancier attribution path
# against, not a bug to work around yet.
DATA_ACCESS_ACTIONS = (
    "data_export", "report_generate", "report_download", "report_share_view",
    "location_score", "location_compare", "customer_data_upload_commit",
)


def list_org_audit_log(org_id, user_id, limit=100):
    """Owner/admin only (same forbidden-for-anyone-else shape as
    update_organization/delete_organization) — activity_log rows for every
    member of this org, filtered to DATA_ACCESS_ACTIONS, newest first."""
    if get_org_role(org_id, user_id) not in ("owner", "admin"):
        return None
    engine = _require_engine()
    tables = _get_tables()
    log = tables["activity_log"]
    members = tables["org_members"]
    users = tables["users"]
    from sqlalchemy import select
    with engine.connect() as conn:
        member_ids = conn.execute(
            select(members.c.user_id).where(members.c.org_id == org_id)
        ).scalars().all()
        if not member_ids:
            return []
        rows = conn.execute(
            select(log, users.c.email, users.c.name)
            .select_from(log.join(users, users.c.id == log.c.user_id))
            .where(log.c.user_id.in_(member_ids), log.c.action.in_(DATA_ACCESS_ACTIONS))
            .order_by(log.c.id.desc()).limit(limit)
        ).mappings().all()
    return [dict(r) for r in rows]


def add_org_member(org_id, actor_user_id, target_email, role="member"):
    """Owner/admin only, and only for a user who already has a PaisaMap
    account — there's no invite-by-email flow yet (Phase D), so the caller
    surfaces "ask them to sign in first" for an unknown email."""
    actor_role = get_org_role(org_id, actor_user_id)
    if actor_role not in ("owner", "admin"):
        return {"error": "forbidden"}
    if role not in ORG_ROLES:
        role = "member"
    target = get_user_by_email(target_email)
    if target is None:
        return {"error": "user_not_found"}
    engine = _require_engine()
    tables = _get_tables()
    members = tables["org_members"]
    from sqlalchemy import select
    now = _now()
    with engine.begin() as conn:
        existing = conn.execute(
            select(members.c.id).where(members.c.org_id == org_id, members.c.user_id == target["id"])
        ).first()
        if existing:
            return {"error": "already_a_member"}
        conn.execute(
            members.insert().values(org_id=org_id, user_id=target["id"], role=role, created_at=now)
        )
    return {"members": list_org_members(org_id, actor_user_id)}


def _count_owners(org_id, conn):
    from sqlalchemy import select, func
    tables = _get_tables()
    members = tables["org_members"]
    return conn.execute(
        select(func.count(members.c.id)).where(members.c.org_id == org_id, members.c.role == "owner")
    ).scalar()


def remove_org_member(org_id, actor_user_id, target_user_id):
    """Owner/admin only; refuses to remove the org's last owner (would leave
    it with no one able to manage membership or billing)."""
    actor_role = get_org_role(org_id, actor_user_id)
    if actor_role not in ("owner", "admin"):
        return {"error": "forbidden"}
    engine = _require_engine()
    tables = _get_tables()
    members = tables["org_members"]
    from sqlalchemy import select
    with engine.begin() as conn:
        target_role = conn.execute(
            select(members.c.role)
            .where(members.c.org_id == org_id, members.c.user_id == target_user_id)
        ).scalar()
        if target_role is None:
            return {"error": "not_a_member"}
        if target_role == "owner" and _count_owners(org_id, conn) <= 1:
            return {"error": "cannot_remove_last_owner"}
        conn.execute(
            members.delete().where(members.c.org_id == org_id, members.c.user_id == target_user_id)
        )
    return {"status": "ok"}


def update_org_member_role(org_id, actor_user_id, target_user_id, role):
    """Owner only; refuses to demote the org's last owner."""
    if get_org_role(org_id, actor_user_id) != "owner":
        return {"error": "forbidden"}
    if role not in ORG_ROLES:
        return {"error": "invalid_role"}
    engine = _require_engine()
    tables = _get_tables()
    members = tables["org_members"]
    from sqlalchemy import select
    with engine.begin() as conn:
        current_role = conn.execute(
            select(members.c.role).where(members.c.org_id == org_id, members.c.user_id == target_user_id)
        ).scalar()
        if current_role is None:
            return {"error": "not_a_member"}
        if current_role == "owner" and role != "owner" and _count_owners(org_id, conn) <= 1:
            return {"error": "cannot_demote_last_owner"}
        conn.execute(
            members.update()
            .where(members.c.org_id == org_id, members.c.user_id == target_user_id)
            .values(role=role)
        )
    return {"members": list_org_members(org_id, actor_user_id)}


# ── Invites (Phase D1) ─────────────────────────────────────────────────────
# add_org_member above only works for an email that already has an account.
# This section covers the other case: invite someone who hasn't signed up
# yet. A pending invite is keyed by a token (same secrets.token_urlsafe(24)
# credential-not-session pattern as reports.py's share links) that's emailed
# out and later exchanged for real org_members row once the invitee signs in.

INVITE_TTL_DAYS = 7


def _ensure_aware(dt):
    """SQLite (local dev) returns naive datetimes even for
    DateTime(timezone=True) columns — Postgres (prod) doesn't. Compared
    against _now() (aware), a naive value raises TypeError rather than
    just comparing wrong, so this normalizes before every expires_at check.
    Same pattern as blueprints/analytics_connections.py's _ensure_aware."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _invite_row_to_dict(row):
    out = dict(row)
    out["email"] = out["email"].lower()
    return out


def create_or_resend_org_invite(org_id, actor_user_id, target_email, role="member"):
    """Owner/admin only. A known email short-circuits into add_org_member
    (no need for an invite when the account already exists). For an unknown
    email: reuses any existing pending invite for this (org, email) rather
    than creating a duplicate — calling this again for the same address is
    exactly "resend" (new token, extended expiry). Returns
    {"invite": {...}, "accept_url": ..., "email_sent": bool} — email_sent is
    surfaced but never blocks success; SES not being configured yet is a
    normal state, not an error (see _email.py)."""
    import secrets
    from datetime import timedelta
    actor_role = get_org_role(org_id, actor_user_id)
    if actor_role not in ("owner", "admin"):
        return {"error": "forbidden"}
    if role not in ORG_ROLES or role == "owner":
        role = "member"
    email = target_email.strip().lower()
    if not email:
        return {"error": "email is required"}

    existing_user = get_user_by_email(email)
    if existing_user is not None:
        return add_org_member(org_id, actor_user_id, email, role)

    engine = _require_engine()
    tables = _get_tables()
    invites = tables["org_invites"]
    members = tables["org_members"]
    from sqlalchemy import select
    now = _now()
    token = secrets.token_urlsafe(24)
    expires_at = now.replace(microsecond=0) + timedelta(days=INVITE_TTL_DAYS)
    with engine.begin() as conn:
        already_member = conn.execute(
            select(members.c.id)
            .select_from(members.join(tables["users"], tables["users"].c.id == members.c.user_id))
            .where(members.c.org_id == org_id, tables["users"].c.email == email)
        ).first()
        if already_member:
            return {"error": "already_a_member"}
        pending = conn.execute(
            select(invites.c.id)
            .where(invites.c.org_id == org_id, invites.c.email == email, invites.c.status == "pending")
        ).first()
        if pending:
            conn.execute(
                invites.update().where(invites.c.id == pending.id)
                .values(role=role, token=token, invited_by_user_id=actor_user_id,
                        created_at=now, expires_at=expires_at)
            )
            invite_id = pending.id
        else:
            result = conn.execute(
                invites.insert().values(
                    org_id=org_id, email=email, role=role, token=token,
                    invited_by_user_id=actor_user_id, status="pending",
                    created_at=now, expires_at=expires_at,
                )
            )
            invite_id = result.inserted_primary_key[0]

    org = get_organization(org_id, actor_user_id)
    inviter = get_user(actor_user_id)
    accept_url = _invite_accept_url(token)
    email_sent = False
    try:
        import _email
        email_sent = _email.send_org_invite_email(
            to_email=email, org_name=org["name"] if org else "PaisaMap",
            inviter_name=(inviter or {}).get("name") or (inviter or {}).get("email") or "Someone",
            role=role, accept_url=accept_url,
        )
    except Exception:
        email_sent = False

    return {
        "invite": {"id": invite_id, "email": email, "role": role, "status": "pending"},
        "accept_url": accept_url,
        "email_sent": email_sent,
    }


def _invite_accept_url(token):
    import os
    base = os.environ.get("PUBLIC_APP_URL", "https://paisamaps.com").rstrip("/")
    return f"{base}/workspace/invite/{token}"


def list_org_invites(org_id, actor_user_id):
    """Owner/admin only. Pending invites, newest first."""
    if get_org_role(org_id, actor_user_id) not in ("owner", "admin"):
        return None
    engine = _require_engine()
    tables = _get_tables()
    invites = tables["org_invites"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(invites.c.id, invites.c.email, invites.c.role, invites.c.status,
                   invites.c.created_at, invites.c.expires_at)
            .where(invites.c.org_id == org_id, invites.c.status == "pending")
            .order_by(invites.c.created_at.desc())
        ).mappings().all()
    return [_invite_row_to_dict(r) for r in rows]


def revoke_org_invite(org_id, actor_user_id, invite_id):
    """Owner/admin only."""
    if get_org_role(org_id, actor_user_id) not in ("owner", "admin"):
        return {"error": "forbidden"}
    engine = _require_engine()
    tables = _get_tables()
    invites = tables["org_invites"]
    with engine.begin() as conn:
        result = conn.execute(
            invites.update()
            .where(invites.c.id == invite_id, invites.c.org_id == org_id, invites.c.status == "pending")
            .values(status="revoked", responded_at=_now())
        )
        if result.rowcount == 0:
            return {"error": "not_found"}
    return {"status": "ok"}


def get_invite_by_token(token):
    """Public lookup for the accept/decline landing page — no auth. None if
    the token doesn't exist, isn't pending, or has expired."""
    engine = _require_engine()
    tables = _get_tables()
    invites = tables["org_invites"]
    orgs = tables["organizations"]
    users = tables["users"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(invites.c.id, invites.c.org_id, invites.c.email, invites.c.role,
                   invites.c.status, invites.c.expires_at,
                   orgs.c.name.label("org_name"), users.c.name.label("inviter_name"),
                   users.c.email.label("inviter_email"))
            .select_from(
                invites.join(orgs, orgs.c.id == invites.c.org_id)
                .join(users, users.c.id == invites.c.invited_by_user_id)
            )
            .where(invites.c.token == token)
        ).mappings().first()
    if row is None or row["status"] != "pending" or _ensure_aware(row["expires_at"]) < _now():
        return None
    return _invite_row_to_dict(row)


def accept_org_invite(token, accepting_user_id):
    """Requires the accepting session's own email to match the invited
    email (case-insensitive) — the token alone isn't enough to join, since
    tokens travel over email and could be forwarded/leaked. On success,
    adds the org_members row and marks the invite accepted."""
    engine = _require_engine()
    tables = _get_tables()
    invites = tables["org_invites"]
    members = tables["org_members"]
    from sqlalchemy import select
    accepting_user = get_user(accepting_user_id)
    if accepting_user is None:
        return {"error": "not_found"}
    with engine.begin() as conn:
        row = conn.execute(
            select(invites.c.id, invites.c.org_id, invites.c.email, invites.c.role,
                   invites.c.status, invites.c.expires_at)
            .where(invites.c.token == token)
        ).mappings().first()
        if row is None or row["status"] != "pending" or _ensure_aware(row["expires_at"]) < _now():
            return {"error": "invalid_invite"}
        if row["email"].lower() != accepting_user["email"].lower():
            return {"error": "email_mismatch"}
        existing = conn.execute(
            select(members.c.id).where(members.c.org_id == row["org_id"], members.c.user_id == accepting_user_id)
        ).first()
        if existing is None:
            conn.execute(
                members.insert().values(
                    org_id=row["org_id"], user_id=accepting_user_id, role=row["role"], created_at=_now()
                )
            )
        conn.execute(
            invites.update().where(invites.c.id == row["id"])
            .values(status="accepted", accepted_by_user_id=accepting_user_id, responded_at=_now())
        )
        org_id = row["org_id"]
    return {"organization": get_organization(org_id, accepting_user_id)}


def decline_org_invite(token):
    """No login required — the token itself is the credential, same as a
    report share-view. Idempotent-ish: declining an already-resolved invite
    is a no-op 404 rather than double-writing responded_at."""
    engine = _require_engine()
    tables = _get_tables()
    invites = tables["org_invites"]
    with engine.begin() as conn:
        result = conn.execute(
            invites.update()
            .where(invites.c.token == token, invites.c.status == "pending")
            .values(status="declined", responded_at=_now())
        )
        if result.rowcount == 0:
            return {"error": "not_found"}
    return {"status": "ok"}


def backfill_organizations():
    """C3 — one-time (and safely re-runnable) backfill: every user without an
    org gets one auto-created default company ("<name>'s Workspace"), and
    every one of their existing projects / oauth_connections /
    customer_uploads / customer_locations / credits_ledger rows gets stamped
    with that org_id. A project's website_url (first one found) is carried up
    to the org too, if the org doesn't already have one.

    Still additive in effect, not a behavioral cutover: nothing in the app
    reads org_id to make a decision yet, so running this changes zero live
    behavior — it only prepares the data a later cutover will actually read.
    Idempotent — only touches rows where org_id IS NULL, so re-running after
    new signups/projects land just backfills what's newly missing. Returns a
    summary dict for the caller to print.
    """
    engine = _require_engine()
    tables = _get_tables()
    users = tables["users"]
    orgs = tables["organizations"]
    projects = tables["projects"]
    connections = tables["oauth_connections"]
    uploads = tables["customer_uploads"]
    locations = tables["customer_locations"]
    ledger = tables["credits_ledger"]
    from sqlalchemy import select

    counts = {
        "users_seen": 0, "orgs_created": 0, "projects_stamped": 0,
        "connections_stamped": 0, "uploads_stamped": 0,
        "locations_stamped": 0, "ledger_rows_stamped": 0,
    }

    with engine.connect() as conn:
        user_rows = conn.execute(
            select(users.c.id, users.c.email, users.c.name, users.c.plan, users.c.org_id)
        ).mappings().all()
    counts["users_seen"] = len(user_rows)

    for u in user_rows:
        org_id = u["org_id"]
        if org_id is None:
            display = u["name"] or (u["email"].split("@")[0] if u["email"] else "My")
            org = create_default_organization_for_user(u["id"], f"{display}'s Workspace", plan=u["plan"])
            org_id = org["id"]
            counts["orgs_created"] += 1

        with engine.begin() as conn:
            first_url = conn.execute(
                select(projects.c.website_url)
                .where(projects.c.user_id == u["id"], projects.c.website_url.isnot(None))
                .limit(1)
            ).scalar()
            if first_url:
                conn.execute(
                    orgs.update()
                    .where(orgs.c.id == org_id, orgs.c.website_url.is_(None))
                    .values(website_url=first_url)
                )
            for table, key in ((projects, "projects_stamped"), (connections, "connections_stamped"),
                                (uploads, "uploads_stamped"), (locations, "locations_stamped"),
                                (ledger, "ledger_rows_stamped")):
                r = conn.execute(
                    table.update()
                    .where(table.c.user_id == u["id"], table.c.org_id.is_(None))
                    .values(org_id=org_id)
                )
                counts[key] += r.rowcount

    return counts


PROJECT_EDITABLE_FIELDS = ("name", "description", "business_type", "target_segment",
                           "avg_ticket", "website_url", "industry", "signals",
                           "target_pincodes", "catchment_km", "total_investment",
                           "outcome_goal", "time_horizon_months",
                           "gross_margin_pct", "revenue_period")


# ── Projects ─────────────────────────────────────────────────────────────────
def create_project(user_id, name, description=None, org_id=None, **fields):
    """`fields` accepts any of PROJECT_EDITABLE_FIELDS (business_type,
    target_segment, avg_ticket, website_url, and the wizard fields industry/
    signals/target_pincodes/catchment_km/total_investment/outcome_goal/
    time_horizon_months) — None values are dropped so the column keeps its
    default rather than being written as NULL explicitly.

    org_id (Phase C, staged): when given, the caller must actually be a
    member of that org, or it's silently ignored rather than raising — a
    stale/tampered org_id shouldn't fail project creation, it should just
    fall through to the default below. When not given (every call site
    before this parameter existed, e.g. get_or_create_default_project),
    falls back to the caller's own primary org (users.org_id, set by C3's
    backfill) so existing behavior is unchanged."""
    extra = {k: v for k, v in fields.items()
             if k in PROJECT_EDITABLE_FIELDS and k != "name" and v is not None}
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    if org_id is not None and get_org_role(org_id, user_id) is None:
        org_id = None
    if org_id is None:
        from sqlalchemy import select
        with engine.connect() as conn:
            org_id = conn.execute(
                select(tables["users"].c.org_id).where(tables["users"].c.id == user_id)
            ).scalar()
    now = _now()
    with engine.begin() as conn:
        result = conn.execute(
            projects.insert().values(
                user_id=user_id, org_id=org_id, name=name, description=description,
                created_at=now, updated_at=now, **extra,
            )
        )
        new_id = result.inserted_primary_key[0]
        log_activity(user_id, "project_create", target_type="project", target_id=new_id, conn=conn)
    return get_project(new_id, user_id)


def get_or_create_default_project(user_id):
    """Finds-or-creates the user's "Saved Locations" project — the implicit
    home for anything saved from the public map without a project picker.
    Matched by exact name, not a schema flag, so this needs no new column."""
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(projects.c.id).where(
                projects.c.user_id == user_id, projects.c.name == "Saved Locations"
            )
        ).first()
    if row:
        return row.id
    return create_project(user_id, "Saved Locations",
                           description="Locations you've saved from the map.")["id"]


def list_projects(user_id, include_archived=False):
    """Includes location_count/report_count via correlated subqueries — cheap
    (indexed FK, a handful of projects per user) and lets the workspace show
    real "12 locations · 3 reports" context on the project list instead of a
    bare name (dashboard audit finding C5). Phase D2: also includes every
    project belonging to any company this user is a member of, not just ones
    they personally created — the whole point of a shared company.

    Phase E3: archived projects are excluded by default (archive is meant to
    declutter the working list, not a soft-delete anyone has to think about)
    — pass include_archived=True for the "Archived" filter view."""
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    locs = tables["saved_locations"]
    reports = tables["reports"]
    from sqlalchemy import select, func, or_
    org_ids = list_member_org_ids(user_id)
    location_count = (
        select(func.count(locs.c.id))
        .where(locs.c.project_id == projects.c.id)
        .scalar_subquery()
    )
    report_count = (
        select(func.count(reports.c.id))
        .where(reports.c.project_id == projects.c.id)
        .scalar_subquery()
    )
    cond = projects.c.user_id == user_id
    if org_ids:
        cond = or_(cond, projects.c.org_id.in_(org_ids))
    if not include_archived:
        cond = cond & projects.c.archived_at.is_(None)
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                projects,
                location_count.label("location_count"),
                report_count.label("report_count"),
            )
            .where(cond)
            .order_by(projects.c.updated_at.desc())
        ).mappings().all()
    out = [dict(r) for r in rows]
    for p in out:
        p["role"] = _project_role(p, user_id)
    return out


def get_project(project_id, user_id):
    """Phase D2: read access for the creator OR any member of the project's
    company (any role) — never distinguishes "doesn't exist" from "no
    access" to the caller, so a 404 doesn't leak which other companies have
    which project ids. Includes the caller's effective `role` on the
    returned dict (like list_projects/get_organization already do) so the
    frontend can eventually hide e.g. Delete for a plain member."""
    project = _load_project_row(project_id)
    role = _project_role(project, user_id)
    if role is None:
        return None
    project["role"] = role
    return project


def update_project(project_id, user_id, **fields):
    """Phase D2: write access for the creator or any org member (member/
    admin/owner) — editing is not restricted to admin+, only deleting is."""
    project = _load_project_row(project_id)
    if _project_role(project, user_id) is None:
        return None
    allowed = {k: v for k, v in fields.items() if k in PROJECT_EDITABLE_FIELDS and v is not None}
    if not allowed:
        return get_project(project_id, user_id)
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    allowed["updated_at"] = _now()
    with engine.begin() as conn:
        conn.execute(projects.update().where(projects.c.id == project_id).values(**allowed))
    return get_project(project_id, user_id)


def delete_project(project_id, user_id):
    """Phase D2: owner/admin only (of the project's company) — or the
    literal creator, always. A plain member can create and edit but not
    delete, per the permission policy this phase locked in."""
    project = _load_project_row(project_id)
    if _project_role(project, user_id) not in PROJECT_DELETE_ROLES:
        return False
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    with engine.begin() as conn:
        result = conn.execute(projects.delete().where(projects.c.id == project_id))
    return result.rowcount > 0


def _set_project_archived(project_id, user_id, archived):
    """Shared body for archive_project/unarchive_project — write-level access
    (same as update_project/rename), not delete-gated: archiving just hides a
    project from the default list, it doesn't touch its data or anyone else's
    ability to still open it directly, so it doesn't need the stronger
    owner/admin bar delete_project enforces."""
    project = _load_project_row(project_id)
    if _project_role(project, user_id) is None:
        return None
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    with engine.begin() as conn:
        conn.execute(
            projects.update().where(projects.c.id == project_id)
            .values(archived_at=_now() if archived else None, updated_at=_now())
        )
    return get_project(project_id, user_id)


def archive_project(project_id, user_id):
    return _set_project_archived(project_id, user_id, True)


def unarchive_project(project_id, user_id):
    return _set_project_archived(project_id, user_id, False)


def duplicate_project(project_id, user_id):
    """E3 — clones the project's own setup (name, description, business
    profile, wizard fields) into a brand-new project so the caller can reuse
    a business profile for a different market/segment. Deliberately does NOT
    copy saved_locations/reports/connections/customer data — those belong to
    the specific expansion effort the source project represents, not to the
    reusable "what business is this" template being duplicated. Read access
    is enough to duplicate (same bar as update_project); the clone lands in
    the source project's own org."""
    source = get_project(project_id, user_id)
    if source is None:
        return None
    fields = {k: source.get(k) for k in PROJECT_EDITABLE_FIELDS
              if k not in ("name", "description") and source.get(k) is not None}
    return create_project(user_id, f"{source['name']} (copy)", source.get("description"),
                           org_id=source.get("org_id"), **fields)


def set_project_share_token(project_id, user_id, token):
    """E2 — owner/admin only (stricter than update_project's any-member bar):
    unlike editing, this controls whether the project becomes visible to
    anyone outside the company at all, so it gets the same bar as inviting
    someone new (create_or_resend_org_invite) rather than the looser
    any-member write bar most project fields use."""
    project = _load_project_row(project_id)
    if _project_role(project, user_id) not in PROJECT_DELETE_ROLES:
        return None
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    with engine.begin() as conn:
        conn.execute(projects.update().where(projects.c.id == project_id).values(share_token=token))
    return get_project(project_id, user_id)


_PROJECT_PUBLIC_FIELDS = ("id", "name", "description", "business_type", "target_segment",
                          "industry", "created_at")


def get_project_by_share_token(token):
    """Public, unauthenticated lookup for the project's read-only share link
    — same "possession of the unguessable token is the credential" pattern
    as get_report_by_share_token. Returns only a public-safe field subset
    (no org_id/user_id/financial-planning fields like total_investment) plus
    its saved locations (name/pincode/lat/lng only, no scores — the viewer's
    own client can look those up the same way the map does) and a bare
    report list (title/status/created_at, no ids — a report's own download
    stays behind its own separate, individually-revocable share link, not
    implicitly opened up by sharing the project)."""
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(projects).where(projects.c.share_token == token, projects.c.archived_at.is_(None))
        ).mappings().first()
        if row is None:
            return None
        project_id = row["id"]
        locs = tables["saved_locations"]
        locations = conn.execute(
            select(locs.c.name, locs.c.pincode, locs.c.lat, locs.c.lng)
            .where(locs.c.project_id == project_id)
        ).mappings().all()
        reports = tables["reports"]
        report_rows = conn.execute(
            select(reports.c.title, reports.c.status, reports.c.created_at)
            .where(reports.c.project_id == project_id, reports.c.status == "ready")
            .order_by(reports.c.created_at.desc())
        ).mappings().all()
    out = {k: row[k] for k in _PROJECT_PUBLIC_FIELDS}
    out["locations"] = [dict(l) for l in locations]
    out["reports"] = [dict(r) for r in report_rows]
    return out


# ── Saved locations ──────────────────────────────────────────────────────────
def create_saved_location(user_id, project_id, pincode, name=None, lat=None, lng=None):
    """Upsert-like: a repeat save of the same (project_id, pincode) — the
    existing unique constraint — returns the existing row unchanged instead of
    raising, so re-clicking Save on the map is idempotent rather than an error."""
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["saved_locations"]
    from sqlalchemy import select
    with engine.begin() as conn:
        existing = conn.execute(
            select(locs.c.id).where(locs.c.project_id == project_id, locs.c.pincode == pincode)
        ).first()
        if existing:
            return get_saved_location(existing.id, user_id), False
        now = _now()
        result = conn.execute(
            locs.insert().values(
                project_id=project_id, user_id=user_id, pincode=pincode, name=name,
                lat=lat, lng=lng, status="shortlist", created_at=now, updated_at=now,
            )
        )
        new_id = result.inserted_primary_key[0]
        log_activity(user_id, "location_save", target_type="saved_location", target_id=new_id,
                     metadata={"pincode": pincode}, conn=conn)
    return get_saved_location(new_id, user_id), True


def _accessible_project_ids(user_id):
    """Every project_id this user can read — their own + every project
    belonging to any company they're a member of (Phase D2). Used to scope
    "everything across every project I can see" listings that take no
    single project_id (e.g. Saved Locations' all-projects view)."""
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    from sqlalchemy import select, or_
    org_ids = list_member_org_ids(user_id)
    cond = projects.c.user_id == user_id
    if org_ids:
        cond = or_(cond, projects.c.org_id.in_(org_ids))
    with engine.connect() as conn:
        rows = conn.execute(select(projects.c.id).where(cond)).scalars().all()
    return list(rows)


def list_saved_locations(user_id, project_id=None):
    """Phase D2: project_id given → any org member of that one project can
    read it. No project_id → every location across every project this user
    can see (their own + every company they belong to), not just ones they
    personally saved — a teammate's save shows up here too."""
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["saved_locations"]
    from sqlalchemy import select
    if project_id is not None:
        if get_project(project_id, user_id) is None:
            return []
        conds = [locs.c.project_id == project_id]
    else:
        accessible = _accessible_project_ids(user_id)
        if not accessible:
            return []
        conds = [locs.c.project_id.in_(accessible)]
    with engine.connect() as conn:
        rows = conn.execute(
            select(locs).where(*conds).order_by(locs.c.updated_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def get_saved_location(location_id, user_id):
    """Phase D2: read access via the owning project's role, not a bare
    user_id match — a teammate can look up a location a colleague saved."""
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["saved_locations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(select(locs).where(locs.c.id == location_id)).mappings().first()
    if row is None:
        return None
    row = dict(row)
    if get_project(row["project_id"], user_id) is None:
        return None
    return row


LOCATION_EDITABLE_FIELDS = ("status", "tags", "notes", "allocated_investment")


def update_saved_location(location_id, user_id, **fields):
    """Phase D2: any org member (member/admin/owner) can edit — see
    get_saved_location for the access check this reuses."""
    current = get_saved_location(location_id, user_id)
    if current is None:
        return None
    allowed = {k: v for k, v in fields.items() if k in LOCATION_EDITABLE_FIELDS and v is not None}
    if not allowed:
        return current
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["saved_locations"]
    allowed["updated_at"] = _now()
    with engine.begin() as conn:
        conn.execute(locs.update().where(locs.c.id == location_id).values(**allowed))
    return get_saved_location(location_id, user_id)


def delete_saved_location(location_id, user_id):
    """Phase D2: owner/admin of the owning project's company only (or its
    literal creator) — a plain member can't delete, per policy."""
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["saved_locations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(locs.c.project_id).where(locs.c.id == location_id)
        ).first()
    if row is None:
        return False
    project = _load_project_row(row.project_id)
    if _project_role(project, user_id) not in PROJECT_DELETE_ROLES:
        return False
    with engine.begin() as conn:
        result = conn.execute(locs.delete().where(locs.c.id == location_id))
    return result.rowcount > 0


# ── Activity / credits / reports (read paths) ───────────────────────────────
def list_activity(user_id, limit=50):
    engine = _require_engine()
    tables = _get_tables()
    log = tables["activity_log"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(log).where(log.c.user_id == user_id)
            .order_by(log.c.id.desc()).limit(limit)
        ).mappings().all()
    return [dict(r) for r in rows]


def list_credit_ledger(user_id, limit=50):
    engine = _require_engine()
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(ledger).where(ledger.c.user_id == user_id)
            .order_by(ledger.c.id.desc()).limit(limit)
        ).mappings().all()
    return [dict(r) for r in rows]


def list_reports(user_id):
    """Phase D2: every report across every project this user can see (their
    own + every company they belong to) — a teammate's generated report
    shows up here too."""
    engine = _require_engine()
    tables = _get_tables()
    reports = tables["reports"]
    from sqlalchemy import select
    accessible = _accessible_project_ids(user_id)
    if not accessible:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(reports).where(reports.c.project_id.in_(accessible))
            .order_by(reports.c.created_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def create_report(user_id, project_id, title, format="pdf", status="pending",
                   file_path=None, params=None):
    engine = _require_engine()
    tables = _get_tables()
    reports = tables["reports"]
    now = _now()
    with engine.begin() as conn:
        result = conn.execute(
            reports.insert().values(
                user_id=user_id, project_id=project_id, title=title, format=format,
                status=status, file_path=file_path, params=params,
                created_at=now, completed_at=now if status == "ready" else None,
            )
        )
        new_id = result.inserted_primary_key[0]
    return get_report(new_id, user_id)


def get_report(report_id, user_id):
    """Phase D2: read access via the owning project's role, same convention
    as get_saved_location."""
    engine = _require_engine()
    tables = _get_tables()
    reports = tables["reports"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(select(reports).where(reports.c.id == report_id)).mappings().first()
    if row is None:
        return None
    row = dict(row)
    if get_project(row["project_id"], user_id) is None:
        return None
    return row


def update_report(report_id, user_id, **fields):
    """Phase D2: any org member can update (e.g. the status transitions
    report generation itself makes) — see get_report for the access check."""
    current = get_report(report_id, user_id)
    if current is None:
        return None
    allowed = {k: v for k, v in fields.items() if v is not None}
    if not allowed:
        return current
    if allowed.get("status") == "ready" and "completed_at" not in allowed:
        allowed["completed_at"] = _now()
    engine = _require_engine()
    tables = _get_tables()
    reports = tables["reports"]
    with engine.begin() as conn:
        conn.execute(reports.update().where(reports.c.id == report_id).values(**allowed))
    return get_report(report_id, user_id)


def set_report_share_token(report_id, user_id, token):
    """Phase D2: same access check as update_report — but unlike it,
    explicitly writes NULL when token=None (revoking a share), which
    update_report's "drop None values" filter can't express.

    Pre-existing bug fixed in passing: this never returned the updated row
    (fell through to an implicit None) even though both blueprint callers
    (share_report/unshare_report) assign it straight to a `report` they then
    jsonify — /api/reports/<id>/share and .../unshare had always responded
    with {"report": null}, a real client-visible bug this touched anyway."""
    if get_report(report_id, user_id) is None:
        return None
    engine = _require_engine()
    tables = _get_tables()
    reports = tables["reports"]
    with engine.begin() as conn:
        conn.execute(reports.update().where(reports.c.id == report_id).values(share_token=token))
    return get_report(report_id, user_id)


def get_report_by_share_token(token):
    """Public, unauthenticated lookup for the shareable read-only link — no
    user_id scoping, deliberately: possession of the unguessable token is the
    only credential a viewer has or needs."""
    engine = _require_engine()
    tables = _get_tables()
    reports = tables["reports"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(reports).where(reports.c.share_token == token)
        ).mappings().first()
    return dict(row) if row else None


# ── Customer data upload (Phase 05) ─────────────────────────────────────────
def create_customer_upload(user_id, project_id, filename, format, headers, raw_rows):
    """Phase C5: stamped with the project's org_id at creation — store data
    is shared across a company's projects (unlike connections, kept
    per-project — see the Organizations section for why these two data
    types ended up with different sharing rules)."""
    engine = _require_engine()
    tables = _get_tables()
    uploads = tables["customer_uploads"]
    projects = tables["projects"]
    from sqlalchemy import select
    now = _now()
    with engine.begin() as conn:
        org_id = conn.execute(select(projects.c.org_id).where(projects.c.id == project_id)).scalar()
        result = conn.execute(
            uploads.insert().values(
                user_id=user_id, project_id=project_id, org_id=org_id, filename=filename, format=format,
                status="pending_mapping", headers=headers, raw_rows=raw_rows,
                created_at=now, updated_at=now,
            )
        )
        new_id = result.inserted_primary_key[0]
    return get_customer_upload(new_id, user_id)


def get_customer_upload(upload_id, user_id):
    """Phase D2: read access via the owning project's role — store data is
    company-shared (Phase C5), so a teammate can look up an upload a
    colleague made under a different project in the same company."""
    engine = _require_engine()
    tables = _get_tables()
    uploads = tables["customer_uploads"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(select(uploads).where(uploads.c.id == upload_id)).mappings().first()
    if row is None:
        return None
    row = dict(row)
    if get_project(row["project_id"], user_id) is None:
        return None
    return row


def list_customer_uploads(user_id, project_id=None):
    """Phase C5: given a project_id, returns every upload belonging to that
    project's whole company (org_id), not just this one project or this one
    user — store data is shared across a company's projects. Ownership of
    the *requesting* project is still checked (get_project, user_id-scoped —
    real cross-member access control is Phase D, not built yet); the
    returned rows themselves are org-scoped, which may include uploads a
    teammate made under a different project."""
    engine = _require_engine()
    tables = _get_tables()
    uploads = tables["customer_uploads"]
    from sqlalchemy import select
    if project_id is not None:
        project = get_project(project_id, user_id)
        if project is None or project.get("org_id") is None:
            return []
        clauses = [uploads.c.org_id == project["org_id"]]
    else:
        clauses = [uploads.c.user_id == user_id]
    with engine.connect() as conn:
        rows = conn.execute(
            select(uploads).where(*clauses).order_by(uploads.c.created_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def update_customer_upload(upload_id, user_id, **fields):
    """Same "drop None values" convention as update_report/update_project —
    fine here since every field this is called with (status/mapping/
    quality_report/error) is always set to a real value, never explicitly
    cleared back to NULL. Phase D2: any org member can update (this is also
    how the upload's own commit/geocode pipeline advances its status)."""
    current = get_customer_upload(upload_id, user_id)
    if current is None:
        return None
    allowed = {k: v for k, v in fields.items() if v is not None}
    if not allowed:
        return current
    allowed["updated_at"] = _now()
    engine = _require_engine()
    tables = _get_tables()
    uploads = tables["customer_uploads"]
    with engine.begin() as conn:
        conn.execute(uploads.update().where(uploads.c.id == upload_id).values(**allowed))
    return get_customer_upload(upload_id, user_id)


def delete_customer_upload(upload_id, user_id):
    """Cascades to customer_locations via the FK's ondelete=CASCADE. Phase
    D2: owner/admin of the owning project's company only — a plain member
    can upload/edit but not delete, per policy."""
    current = get_customer_upload(upload_id, user_id)
    if current is None:
        return False
    project = _load_project_row(current["project_id"])
    if _project_role(project, user_id) not in PROJECT_DELETE_ROLES:
        return False
    engine = _require_engine()
    tables = _get_tables()
    uploads = tables["customer_uploads"]
    with engine.begin() as conn:
        result = conn.execute(uploads.delete().where(uploads.c.id == upload_id))
    return result.rowcount > 0


def create_customer_locations_bulk(user_id, project_id, upload_id, rows):
    """rows: list of dicts with keys store_name/raw_address/pincode/lat/lng/
    geocode_status/revenue/rent/capex/extra_fields (all optional except
    geocode_status). Returns the count inserted. Phase C5: stamped with the
    project's org_id, same reasoning as create_customer_upload.

    Phase H1: raw_address/revenue/rent/capex are real financial/PII data
    about a customer's own business — encrypted at rest via
    _customer_data_crypto, mandatory (not best-effort) same as
    _token_crypto.py's OAuth tokens: a missing CUSTOMER_DATA_KEY must fail
    this loudly, not silently persist plaintext. The legacy plaintext
    columns are left NULL for every row written through this path; they
    only still hold real data on rows inserted before this encryption
    existed, kept there until a separate, explicitly-approved backfill."""
    if not rows:
        return 0
    import _customer_data_crypto as _cdc
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    projects = tables["projects"]
    from sqlalchemy import select
    now = _now()

    def _enc(v):
        return _cdc.encrypt(str(v)) if v is not None else None

    with engine.begin() as conn:
        org_id = conn.execute(select(projects.c.org_id).where(projects.c.id == project_id)).scalar()
        values = [
            dict(
                user_id=user_id, project_id=project_id, org_id=org_id, upload_id=upload_id,
                store_name=r.get("store_name"), raw_address=None,
                raw_address_encrypted=_enc(r.get("raw_address")),
                pincode=r.get("pincode"), lat=r.get("lat"), lng=r.get("lng"),
                geocode_status=r.get("geocode_status", "pending"),
                revenue=None, rent=None, capex=None,
                revenue_encrypted=_enc(r.get("revenue")), rent_encrypted=_enc(r.get("rent")),
                capex_encrypted=_enc(r.get("capex")),
                extra_fields=r.get("extra_fields"),
                created_at=now, updated_at=now,
            )
            for r in rows
        ]
        conn.execute(locs.insert(), values)
    return len(values)


def _decrypt_customer_location_row(row):
    """Shared by list_customer_locations/list_pending_geocode_locations —
    the one choke point every consumer (forecast/expansion modelling,
    CustomerData.tsx's own display, the geocode job) reads through, so none
    of them need to know encryption is involved at all. Prefers the
    encrypted column when present (every row written since H1 shipped);
    falls back to the legacy plaintext column for rows written before it
    (until a separate backfill migrates them) — never both at once, since
    the write path always nulls out whichever side it isn't using."""
    import _customer_data_crypto as _cdc
    out = dict(row)
    if out.get("raw_address_encrypted"):
        out["raw_address"] = _cdc.decrypt(out["raw_address_encrypted"])
    for field in ("revenue", "rent", "capex"):
        enc = out.get(f"{field}_encrypted")
        if enc:
            out[field] = float(_cdc.decrypt(enc))
    return out


def backfill_encrypt_customer_locations(batch_size=500):
    """Phase H1 — one-time (and safely re-runnable) backfill: encrypts
    revenue/rent/capex/raw_address on every row written before this feature
    existed. Idempotent — only selects rows where a plaintext value is
    present AND its _encrypted twin is still NULL, so a row already migrated
    (or one that never had a value at all) is never touched again on re-run.

    Deliberately does NOT null out the legacy plaintext columns — that's a
    separate, later, even-more-cautious step for once there's real
    confidence the encrypted columns are correct and the key is durably
    backed up; losing CUSTOMER_DATA_KEY before that point must not mean
    losing the data, only the encrypted copy of it."""
    import _customer_data_crypto as _cdc
    if not _cdc.enabled():
        raise RuntimeError("CUSTOMER_DATA_KEY is not configured — cannot backfill encryption without it")
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    from sqlalchemy import select, or_, and_

    to_migrate = or_(
        and_(locs.c.revenue.is_not(None), locs.c.revenue_encrypted.is_(None)),
        and_(locs.c.rent.is_not(None), locs.c.rent_encrypted.is_(None)),
        and_(locs.c.capex.is_not(None), locs.c.capex_encrypted.is_(None)),
        and_(locs.c.raw_address.is_not(None), locs.c.raw_address_encrypted.is_(None)),
    )
    counts = {"rows_seen": 0, "revenue": 0, "rent": 0, "capex": 0, "raw_address": 0}
    while True:
        with engine.begin() as conn:
            rows = conn.execute(
                select(locs.c.id, locs.c.revenue, locs.c.rent, locs.c.capex, locs.c.raw_address,
                       locs.c.revenue_encrypted, locs.c.rent_encrypted, locs.c.capex_encrypted,
                       locs.c.raw_address_encrypted)
                .where(to_migrate).limit(batch_size)
            ).all()
            if not rows:
                break
            for row in rows:
                counts["rows_seen"] += 1
                values = {}
                if row.revenue is not None and row.revenue_encrypted is None:
                    values["revenue_encrypted"] = _cdc.encrypt(str(row.revenue))
                    counts["revenue"] += 1
                if row.rent is not None and row.rent_encrypted is None:
                    values["rent_encrypted"] = _cdc.encrypt(str(row.rent))
                    counts["rent"] += 1
                if row.capex is not None and row.capex_encrypted is None:
                    values["capex_encrypted"] = _cdc.encrypt(str(row.capex))
                    counts["capex"] += 1
                if row.raw_address is not None and row.raw_address_encrypted is None:
                    values["raw_address_encrypted"] = _cdc.encrypt(row.raw_address)
                    counts["raw_address"] += 1
                if values:
                    conn.execute(locs.update().where(locs.c.id == row.id).values(**values))
    return counts


def list_customer_locations(user_id, project_id=None):
    """Phase C5: org-scoped when a project_id is given — see
    list_customer_uploads' docstring, same reasoning applies here."""
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    from sqlalchemy import select
    if project_id is not None:
        project = get_project(project_id, user_id)
        if project is None or project.get("org_id") is None:
            return []
        clauses = [locs.c.org_id == project["org_id"]]
    else:
        clauses = [locs.c.user_id == user_id]
    with engine.connect() as conn:
        rows = conn.execute(
            select(locs).where(*clauses).order_by(locs.c.created_at.desc())
        ).mappings().all()
    return [_decrypt_customer_location_row(r) for r in rows]


def count_unresolved_locations(upload_id, user_id):
    """Rows from this upload with no resolved pincode (geocode_status in
    failed/unresolvable/still-pending) — these can't be joined to the signals
    dataset, so they're silently excluded from forecast/expansion/intelligence.
    Surfaced so that exclusion is visible instead of silent. Phase D2:
    access-checked via the upload itself (not a separate user_id filter on
    the locations, since a bulk upload's rows always share the upload's
    single creator anyway) — needed once list_customer_uploads started
    surfacing a teammate's uploads too, or this would silently under-count
    (0) for anyone but the original uploader."""
    if get_customer_upload(upload_id, user_id) is None:
        return 0
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    from sqlalchemy import select, func
    with engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(locs).where(
                locs.c.upload_id == upload_id, locs.c.pincode.is_(None),
            )
        ).scalar_one()


def list_pending_geocode_locations(upload_id, user_id):
    """Phase D2: same access-via-upload reasoning as count_unresolved_locations."""
    if get_customer_upload(upload_id, user_id) is None:
        return []
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(locs).where(locs.c.upload_id == upload_id, locs.c.geocode_status == "pending")
        ).mappings().all()
    return [_decrypt_customer_location_row(r) for r in rows]


_CUSTOMER_LOCATION_ENCRYPTED_FIELDS = ("revenue", "rent", "capex", "raw_address")


def update_customer_location(location_id, user_id, **fields):
    """Phase D2: any org member can update — access via the owning project's role.

    Phase H1: defensive, not just documentation — the only real caller today
    (the geocode job) never passes revenue/rent/capex/raw_address, but this
    is a generic **fields setter, and a future caller easily could. Any of
    those four gets transparently redirected to its _encrypted column
    instead of ever writing the legacy plaintext one, so this function can't
    silently reintroduce a plaintext write no matter what a future call site
    passes."""
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(select(locs.c.project_id).where(locs.c.id == location_id)).first()
    if row is None or get_project(row.project_id, user_id) is None:
        return None
    allowed = {k: v for k, v in fields.items() if v is not None}
    if not allowed:
        return None
    if any(f in allowed for f in _CUSTOMER_LOCATION_ENCRYPTED_FIELDS):
        import _customer_data_crypto as _cdc
        for f in _CUSTOMER_LOCATION_ENCRYPTED_FIELDS:
            if f in allowed:
                allowed[f"{f}_encrypted"] = _cdc.encrypt(str(allowed.pop(f)))
    allowed["updated_at"] = _now()
    with engine.begin() as conn:
        conn.execute(locs.update().where(locs.c.id == location_id).values(**allowed))


def delete_customer_location(location_id, user_id):
    """Phase D2: owner/admin of the owning project's company only."""
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(select(locs.c.project_id).where(locs.c.id == location_id)).first()
    if row is None:
        return False
    project = _load_project_row(row.project_id)
    if _project_role(project, user_id) not in PROJECT_DELETE_ROLES:
        return False
    with engine.begin() as conn:
        result = conn.execute(locs.delete().where(locs.c.id == location_id))
    return result.rowcount > 0


# ── OAuth connections (Phase 05B) ────────────────────────────────────────────
def set_analytics_consent(project_id, user_id):
    """Records the DPA/consent acceptance timestamp on the project itself —
    one click covers whichever providers get connected under it, since it's a
    single business relationship, not a per-provider agreement. Returns the
    timestamp so the connect flow can gate on "was this set" without a second
    query racing the write."""
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    now = _now()
    with engine.begin() as conn:
        result = conn.execute(
            projects.update()
            .where(projects.c.id == project_id, projects.c.user_id == user_id)
            .values(analytics_consent_at=now, updated_at=now)
        )
    return now if result.rowcount > 0 else None


def has_analytics_consent(project_id, user_id):
    project = get_project(project_id, user_id)
    return bool(project and project.get("analytics_consent_at"))




def upsert_oauth_connection(project_id, user_id, provider, external_account_email, scopes,
                             access_token_encrypted, refresh_token_encrypted, token_expiry):
    """Insert-or-replace on (project_id, provider) — reconnecting (e.g. after a
    revoked refresh token) should overwrite the old row, not accumulate a second
    one, hence the unique constraint + explicit update-if-exists here rather than
    a DB-level ON CONFLICT (kept portable across the Postgres/SQLite dialects
    this module already supports, same as grant_credits' plain select-then-write).

    Phase C5: also stamps org_id (from the project) and writes/updates this
    project's selection row so it immediately uses the connection it just
    created — connecting always makes it your active choice for this
    provider, even if you were previously using a company teammate's."""
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    selections = tables["project_connection_selections"]
    projects = tables["projects"]
    from sqlalchemy import select
    now = _now()
    with engine.begin() as conn:
        org_id = conn.execute(select(projects.c.org_id).where(projects.c.id == project_id)).scalar()
        existing = conn.execute(
            select(conns.c.id).where(conns.c.project_id == project_id, conns.c.provider == provider)
        ).first()
        values = dict(
            user_id=user_id, org_id=org_id, status="connected",
            external_account_email=external_account_email,
            scopes=scopes, access_token_encrypted=access_token_encrypted,
            last_error=None, updated_at=now,
        )
        # A re-auth may come back without a refresh_token (Google only issues one
        # on first consent unless prompt=consent forces a fresh one, which the
        # authorize URL always sets — but don't clobber a good token with None
        # if that ever changes).
        if refresh_token_encrypted is not None:
            values["refresh_token_encrypted"] = refresh_token_encrypted
        if token_expiry is not None:
            values["token_expiry"] = token_expiry
        if existing:
            connection_id = existing.id
            conn.execute(conns.update().where(conns.c.id == connection_id).values(**values))
        else:
            values.update(project_id=project_id, provider=provider, connected_at=now)
            result = conn.execute(conns.insert().values(**values))
            connection_id = result.inserted_primary_key[0]
        conn.execute(
            selections.delete().where(selections.c.project_id == project_id, selections.c.provider == provider)
        )
        conn.execute(
            selections.insert().values(
                project_id=project_id, provider=provider, oauth_connection_id=connection_id, created_at=now,
            )
        )
    return get_connection_for_project(project_id, user_id, provider)


def get_connection_for_project(project_id, user_id, provider, include_tokens=False):
    """Resolves via project_connection_selections only (Phase C5) — every
    connection gets an explicit selection row the moment it's created or
    switched to (see upsert_oauth_connection / select_org_connection_for_project),
    so there's no separate legacy/implicit fallback path to keep in sync."""
    if get_project(project_id, user_id) is None:
        return None
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    selections = tables["project_connection_selections"]
    from sqlalchemy import select
    cols = list(conns.c) if include_tokens else [
        c for c in conns.c if c.name not in ("access_token_encrypted", "refresh_token_encrypted")
    ]
    with engine.connect() as conn:
        row = conn.execute(
            select(*cols)
            .select_from(selections.join(conns, conns.c.id == selections.c.oauth_connection_id))
            .where(selections.c.project_id == project_id, selections.c.provider == provider)
        ).mappings().first()
    return dict(row) if row else None


def list_org_connections(project_id, user_id, provider=None):
    """The company's whole pool of connections for a provider (or all
    providers) — every project's Connections page can offer this as
    "use a different one", not just what it created itself. Each row is
    annotated with which project originally created it, for display."""
    project = get_project(project_id, user_id)
    if project is None or project.get("org_id") is None:
        return []
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    projects = tables["projects"]
    from sqlalchemy import select
    cols = [c for c in conns.c if c.name not in ("access_token_encrypted", "refresh_token_encrypted")]
    clauses = [conns.c.org_id == project["org_id"]]
    if provider is not None:
        clauses.append(conns.c.provider == provider)
    with engine.connect() as conn:
        rows = conn.execute(
            select(*cols, projects.c.name.label("created_by_project_name"))
            .select_from(conns.join(projects, projects.c.id == conns.c.project_id))
            .where(*clauses)
            .order_by(conns.c.connected_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def select_org_connection_for_project(project_id, user_id, provider, oauth_connection_id):
    """Points this project at an existing connection from its company's pool
    instead of its own. Refuses (returns None) if the connection isn't
    actually in the same org — never trust a bare id across a company
    boundary, even though only a signed-in member of *some* company could
    have supplied one at all."""
    project = get_project(project_id, user_id)
    if project is None or project.get("org_id") is None:
        return None
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    selections = tables["project_connection_selections"]
    from sqlalchemy import select
    now = _now()
    with engine.begin() as conn:
        conn_org_id = conn.execute(
            select(conns.c.org_id).where(conns.c.id == oauth_connection_id, conns.c.provider == provider)
        ).scalar()
        if conn_org_id is None or conn_org_id != project["org_id"]:
            return None
        conn.execute(
            selections.delete().where(selections.c.project_id == project_id, selections.c.provider == provider)
        )
        conn.execute(
            selections.insert().values(
                project_id=project_id, provider=provider, oauth_connection_id=oauth_connection_id, created_at=now,
            )
        )
    return get_connection_for_project(project_id, user_id, provider)


def update_oauth_connection_ref(connection_id, external_ref):
    """Stores the chosen GA4 property id / GSC site URL after the user picks one
    from the list fetched with the connection's access token. Keyed by the
    connection's own id (Phase C5) rather than (project_id, provider) — every
    project sharing this connection sees the same chosen property, which is
    the correct behaviour for one real underlying GA4/GSC account."""
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    with engine.begin() as conn:
        conn.execute(
            conns.update().where(conns.c.id == connection_id)
            .values(external_ref=external_ref, updated_at=_now())
        )


def mark_oauth_connection_tokens(connection_id, access_token_encrypted, token_expiry):
    """Refresh-only update — leaves refresh_token/external_ref/status untouched."""
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    with engine.begin() as conn:
        conn.execute(
            conns.update().where(conns.c.id == connection_id)
            .values(access_token_encrypted=access_token_encrypted, token_expiry=token_expiry,
                    status="connected", last_error=None, updated_at=_now())
        )


def mark_oauth_connection_error(connection_id, error_message):
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    with engine.begin() as conn:
        conn.execute(
            conns.update().where(conns.c.id == connection_id)
            .values(status="error", last_error=error_message[:500], updated_at=_now())
        )


def disconnect_project_connection(project_id, user_id, provider):
    """Removes this project's use of a connection for this provider.

    If nothing else in the company is using that same connection afterward,
    it's a real DELETE (not a status flip — the DPDP framing this phase was
    built under commits to an actual delete on disconnect, since a lingering
    encrypted-token row is still personal/business data at rest even once
    unusable) and tokens are returned for revocation at Google. If another
    project is still using it (real company-level sharing), it's left alone
    for them — this project just goes back to "not connected", no tokens
    returned (there's nothing to revoke, the connection lives on).

    Phase D2: owner/admin only, even for the unlink-only case — disconnecting
    is destructive-flavored (may revoke real tokens), same delete tier as
    deleting a project/location/upload."""
    project = _load_project_row(project_id)
    if _project_role(project, user_id) not in PROJECT_DELETE_ROLES:
        return None
    conn_row = get_connection_for_project(project_id, user_id, provider, include_tokens=True)
    if conn_row is None:
        return None
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    selections = tables["project_connection_selections"]
    from sqlalchemy import select, func
    with engine.begin() as conn:
        conn.execute(
            selections.delete().where(selections.c.project_id == project_id, selections.c.provider == provider)
        )
        remaining = conn.execute(
            select(func.count(selections.c.id)).where(selections.c.oauth_connection_id == conn_row["id"])
        ).scalar()
        if remaining > 0:
            return {"unlinked_only": True}
        conn.execute(conns.delete().where(conns.c.id == conn_row["id"]))
    return {
        "access_token_encrypted": conn_row.get("access_token_encrypted"),
        "refresh_token_encrypted": conn_row.get("refresh_token_encrypted"),
    }


def backfill_connection_selections():
    """One-time (and safely re-runnable) migration for connections created
    before Phase C5 introduced project_connection_selections: gives every
    oauth_connections row without one an explicit selection linking it back
    to the project that originally created it — exactly the behavior those
    projects already had, just made explicit instead of implicit. Also
    stamps org_id on any connection that predates that column. Returns a
    summary dict."""
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    selections = tables["project_connection_selections"]
    projects = tables["projects"]
    from sqlalchemy import select
    counts = {"org_id_stamped": 0, "selections_created": 0}
    with engine.connect() as conn:
        rows = conn.execute(select(conns.c.id, conns.c.project_id, conns.c.provider, conns.c.org_id)).mappings().all()
    now = _now()
    for r in rows:
        with engine.begin() as conn:
            if r["org_id"] is None:
                proj_org_id = conn.execute(select(projects.c.org_id).where(projects.c.id == r["project_id"])).scalar()
                if proj_org_id is not None:
                    conn.execute(conns.update().where(conns.c.id == r["id"]).values(org_id=proj_org_id))
                    counts["org_id_stamped"] += 1
            existing = conn.execute(
                select(selections.c.id).where(
                    selections.c.project_id == r["project_id"], selections.c.provider == r["provider"]
                )
            ).first()
            if existing is None:
                conn.execute(
                    selections.insert().values(
                        project_id=r["project_id"], provider=r["provider"],
                        oauth_connection_id=r["id"], created_at=now,
                    )
                )
                counts["selections_created"] += 1
    return counts


# ── API keys (Track 1 — B2B data-API product) ───────────────────────────────
def create_api_key(user_id, key_hash, key_prefix, label=None):
    engine = _require_engine()
    tables = _get_tables()
    keys = tables["api_keys"]
    with engine.begin() as conn:
        result = conn.execute(
            keys.insert().values(
                user_id=user_id, key_hash=key_hash, key_prefix=key_prefix, label=label,
                created_at=_now(), usage_count_today=0,
            )
        )
        new_id = result.inserted_primary_key[0]
    return get_api_key(new_id, user_id)


def get_api_key(key_id, user_id):
    engine = _require_engine()
    tables = _get_tables()
    keys = tables["api_keys"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(keys).where(keys.c.id == key_id, keys.c.user_id == user_id)
        ).mappings().first()
    if not row:
        return None
    d = dict(row)
    d.pop("key_hash", None)  # never returned past this module — see list_api_keys
    return d


def list_api_keys(user_id):
    """Never includes key_hash — the raw key is shown exactly once at creation
    (blueprints/api_keys.py) and isn't recoverable after that; every response
    from this module strips it, same convention as
    analytics_connections.py's _connection_public() for OAuth tokens."""
    engine = _require_engine()
    tables = _get_tables()
    keys = tables["api_keys"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(keys).where(keys.c.user_id == user_id).order_by(keys.c.created_at.desc())
        ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d.pop("key_hash", None)
        out.append(d)
    return out


def get_api_key_by_hash(key_hash):
    """Auth lookup for an incoming request (see _api_keys.py's resolve()).
    Resolves to the key owner's LIVE plan, not a tier frozen on the key
    itself — a plan upgrade elevates every existing key immediately. Returns
    None for an unknown or revoked key."""
    engine = _require_engine()
    tables = _get_tables()
    keys = tables["api_keys"]
    users = tables["users"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(keys.c.id, keys.c.user_id)
            .where(keys.c.key_hash == key_hash, keys.c.revoked_at.is_(None))
        ).mappings().first()
        if not row:
            return None
        user = conn.execute(
            select(users.c.plan).where(users.c.id == row["user_id"])
        ).mappings().first()
    if not user:
        return None
    return {"key_id": row["id"], "user_id": row["user_id"], "plan": user["plan"]}


def revoke_api_key(key_id, user_id):
    """Soft delete (revoked_at, not a real DELETE) — unlike OAuth tokens
    (delete_oauth_connection above), a revoked API key carries no secret
    worth scrubbing (only its hash is ever stored) and keeping the row lets
    the usage history stay visible in the UI after revocation."""
    engine = _require_engine()
    tables = _get_tables()
    keys = tables["api_keys"]
    with engine.begin() as conn:
        result = conn.execute(
            keys.update()
            .where(keys.c.id == key_id, keys.c.user_id == user_id, keys.c.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
    return result.rowcount > 0


def touch_api_key_usage(key_id):
    """Best-effort visibility counter, not quota enforcement — never let this
    fail or slow down the API response it's counting. One atomic UPDATE
    (not a Python read-then-write) so the daily reset-and-increment is
    race-safe regardless of worker count, even though this app runs
    single-process today."""
    engine = _require_engine()
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE api_keys SET "
                "usage_count_today = CASE WHEN usage_reset_at = CURRENT_DATE "
                "THEN usage_count_today + 1 ELSE 1 END, "
                "usage_reset_at = CURRENT_DATE, last_used_at = :now "
                "WHERE id = :id"
            ), {"now": _now(), "id": key_id})
    except Exception:
        pass
