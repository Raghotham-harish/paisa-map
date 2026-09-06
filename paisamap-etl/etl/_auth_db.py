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
    from sqlalchemy import (MetaData, Table, Column, Text, Integer, Float,
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
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
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
        Column("upload_id", Integer, ForeignKey("customer_uploads.id", ondelete="CASCADE"), nullable=False),
        Column("store_name", Text),
        Column("raw_address", Text),
        Column("pincode", Text),  # no FK — same convention as saved_locations.pincode
        Column("lat", Float),
        Column("lng", Float),
        Column("geocode_status", Text, nullable=False, server_default="pending"),
        Column("revenue", Float),
        Column("rent", Float),
        Column("capex", Float),
        Column("extra_fields", JSONType),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
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

    _tables = {
        "organizations": organizations, "users": users, "org_members": org_members,
        "projects": projects, "saved_locations": saved_locations, "reports": reports,
        "credits_ledger": credits_ledger, "activity_log": activity_log,
        "customer_uploads": customer_uploads, "customer_locations": customer_locations,
        "oauth_connections": oauth_connections,
        "orders": orders, "invoices": invoices,
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
        tables["projects"], tables["saved_locations"], tables["reports"],
        tables["credits_ledger"], tables["activity_log"],
        tables["customer_uploads"], tables["customer_locations"],
        tables["oauth_connections"],
        tables["orders"], tables["invoices"],
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


PROJECT_EDITABLE_FIELDS = ("name", "description", "business_type", "target_segment",
                           "avg_ticket", "website_url", "industry", "signals",
                           "target_pincodes", "catchment_km", "total_investment",
                           "outcome_goal", "time_horizon_months")


# ── Projects ─────────────────────────────────────────────────────────────────
def create_project(user_id, name, description=None, **fields):
    """`fields` accepts any of PROJECT_EDITABLE_FIELDS (business_type,
    target_segment, avg_ticket, website_url, and the wizard fields industry/
    signals/target_pincodes/catchment_km/total_investment/outcome_goal/
    time_horizon_months) — None values are dropped so the column keeps its
    default rather than being written as NULL explicitly."""
    extra = {k: v for k, v in fields.items()
             if k in PROJECT_EDITABLE_FIELDS and k != "name" and v is not None}
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    now = _now()
    with engine.begin() as conn:
        result = conn.execute(
            projects.insert().values(
                user_id=user_id, name=name, description=description,
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


def list_projects(user_id):
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(projects).where(projects.c.user_id == user_id)
            .order_by(projects.c.updated_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def get_project(project_id, user_id):
    """Ownership-scoped — returns None if the project doesn't exist or isn't owned
    by user_id (never distinguishes the two to the caller, so a 404 doesn't leak
    which other users have which project ids)."""
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(projects).where(projects.c.id == project_id, projects.c.user_id == user_id)
        ).mappings().first()
    return dict(row) if row else None


def update_project(project_id, user_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in PROJECT_EDITABLE_FIELDS and v is not None}
    if not allowed:
        return get_project(project_id, user_id)
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    allowed["updated_at"] = _now()
    with engine.begin() as conn:
        conn.execute(
            projects.update()
            .where(projects.c.id == project_id, projects.c.user_id == user_id)
            .values(**allowed)
        )
    return get_project(project_id, user_id)


def delete_project(project_id, user_id):
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    with engine.begin() as conn:
        result = conn.execute(
            projects.delete().where(projects.c.id == project_id, projects.c.user_id == user_id)
        )
    return result.rowcount > 0


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


def list_saved_locations(user_id, project_id=None):
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["saved_locations"]
    from sqlalchemy import select
    conds = [locs.c.user_id == user_id]
    if project_id is not None:
        conds.append(locs.c.project_id == project_id)
    with engine.connect() as conn:
        rows = conn.execute(
            select(locs).where(*conds).order_by(locs.c.updated_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def get_saved_location(location_id, user_id):
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["saved_locations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(locs).where(locs.c.id == location_id, locs.c.user_id == user_id)
        ).mappings().first()
    return dict(row) if row else None


LOCATION_EDITABLE_FIELDS = ("status", "tags", "notes")


def update_saved_location(location_id, user_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in LOCATION_EDITABLE_FIELDS and v is not None}
    if not allowed:
        return get_saved_location(location_id, user_id)
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["saved_locations"]
    allowed["updated_at"] = _now()
    with engine.begin() as conn:
        conn.execute(
            locs.update()
            .where(locs.c.id == location_id, locs.c.user_id == user_id)
            .values(**allowed)
        )
    return get_saved_location(location_id, user_id)


def delete_saved_location(location_id, user_id):
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["saved_locations"]
    with engine.begin() as conn:
        result = conn.execute(
            locs.delete().where(locs.c.id == location_id, locs.c.user_id == user_id)
        )
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
    engine = _require_engine()
    tables = _get_tables()
    reports = tables["reports"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(reports).where(reports.c.user_id == user_id)
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
    """Ownership-scoped, same convention as get_project/get_saved_location."""
    engine = _require_engine()
    tables = _get_tables()
    reports = tables["reports"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(reports).where(reports.c.id == report_id, reports.c.user_id == user_id)
        ).mappings().first()
    return dict(row) if row else None


def update_report(report_id, user_id, **fields):
    allowed = {k: v for k, v in fields.items() if v is not None}
    if not allowed:
        return get_report(report_id, user_id)
    if allowed.get("status") == "ready" and "completed_at" not in allowed:
        allowed["completed_at"] = _now()
    engine = _require_engine()
    tables = _get_tables()
    reports = tables["reports"]
    with engine.begin() as conn:
        conn.execute(
            reports.update()
            .where(reports.c.id == report_id, reports.c.user_id == user_id)
            .values(**allowed)
        )
    return get_report(report_id, user_id)


def set_report_share_token(report_id, user_id, token):
    """Ownership-scoped, like update_report — but unlike it, explicitly writes
    NULL when token=None (revoking a share), which update_report's "drop None
    values" filter can't express."""
    engine = _require_engine()
    tables = _get_tables()
    reports = tables["reports"]
    with engine.begin() as conn:
        conn.execute(
            reports.update()
            .where(reports.c.id == report_id, reports.c.user_id == user_id)
            .values(share_token=token)
        )
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
    engine = _require_engine()
    tables = _get_tables()
    uploads = tables["customer_uploads"]
    now = _now()
    with engine.begin() as conn:
        result = conn.execute(
            uploads.insert().values(
                user_id=user_id, project_id=project_id, filename=filename, format=format,
                status="pending_mapping", headers=headers, raw_rows=raw_rows,
                created_at=now, updated_at=now,
            )
        )
        new_id = result.inserted_primary_key[0]
    return get_customer_upload(new_id, user_id)


def get_customer_upload(upload_id, user_id):
    engine = _require_engine()
    tables = _get_tables()
    uploads = tables["customer_uploads"]
    from sqlalchemy import select
    with engine.connect() as conn:
        row = conn.execute(
            select(uploads).where(uploads.c.id == upload_id, uploads.c.user_id == user_id)
        ).mappings().first()
    return dict(row) if row else None


def list_customer_uploads(user_id, project_id=None):
    engine = _require_engine()
    tables = _get_tables()
    uploads = tables["customer_uploads"]
    from sqlalchemy import select
    clauses = [uploads.c.user_id == user_id]
    if project_id is not None:
        clauses.append(uploads.c.project_id == project_id)
    with engine.connect() as conn:
        rows = conn.execute(
            select(uploads).where(*clauses).order_by(uploads.c.created_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def update_customer_upload(upload_id, user_id, **fields):
    """Same "drop None values" convention as update_report/update_project —
    fine here since every field this is called with (status/mapping/
    quality_report/error) is always set to a real value, never explicitly
    cleared back to NULL."""
    allowed = {k: v for k, v in fields.items() if v is not None}
    if not allowed:
        return get_customer_upload(upload_id, user_id)
    allowed["updated_at"] = _now()
    engine = _require_engine()
    tables = _get_tables()
    uploads = tables["customer_uploads"]
    with engine.begin() as conn:
        conn.execute(
            uploads.update()
            .where(uploads.c.id == upload_id, uploads.c.user_id == user_id)
            .values(**allowed)
        )
    return get_customer_upload(upload_id, user_id)


def delete_customer_upload(upload_id, user_id):
    """Cascades to customer_locations via the FK's ondelete=CASCADE."""
    engine = _require_engine()
    tables = _get_tables()
    uploads = tables["customer_uploads"]
    with engine.begin() as conn:
        result = conn.execute(
            uploads.delete().where(uploads.c.id == upload_id, uploads.c.user_id == user_id)
        )
    return result.rowcount > 0


def create_customer_locations_bulk(user_id, project_id, upload_id, rows):
    """rows: list of dicts with keys store_name/raw_address/pincode/lat/lng/
    geocode_status/revenue/rent/capex/extra_fields (all optional except
    geocode_status). Returns the count inserted."""
    if not rows:
        return 0
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    now = _now()
    values = [
        dict(
            user_id=user_id, project_id=project_id, upload_id=upload_id,
            store_name=r.get("store_name"), raw_address=r.get("raw_address"),
            pincode=r.get("pincode"), lat=r.get("lat"), lng=r.get("lng"),
            geocode_status=r.get("geocode_status", "pending"),
            revenue=r.get("revenue"), rent=r.get("rent"), capex=r.get("capex"),
            extra_fields=r.get("extra_fields"),
            created_at=now, updated_at=now,
        )
        for r in rows
    ]
    with engine.begin() as conn:
        conn.execute(locs.insert(), values)
    return len(values)


def list_customer_locations(user_id, project_id=None):
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    from sqlalchemy import select
    clauses = [locs.c.user_id == user_id]
    if project_id is not None:
        clauses.append(locs.c.project_id == project_id)
    with engine.connect() as conn:
        rows = conn.execute(
            select(locs).where(*clauses).order_by(locs.c.created_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def list_pending_geocode_locations(upload_id, user_id):
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(locs).where(
                locs.c.upload_id == upload_id, locs.c.user_id == user_id,
                locs.c.geocode_status == "pending",
            )
        ).mappings().all()
    return [dict(r) for r in rows]


def update_customer_location(location_id, user_id, **fields):
    allowed = {k: v for k, v in fields.items() if v is not None}
    if not allowed:
        return None
    allowed["updated_at"] = _now()
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    with engine.begin() as conn:
        conn.execute(
            locs.update()
            .where(locs.c.id == location_id, locs.c.user_id == user_id)
            .values(**allowed)
        )


def delete_customer_location(location_id, user_id):
    engine = _require_engine()
    tables = _get_tables()
    locs = tables["customer_locations"]
    with engine.begin() as conn:
        result = conn.execute(
            locs.delete().where(locs.c.id == location_id, locs.c.user_id == user_id)
        )
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


def list_oauth_connections(project_id, user_id):
    """Ownership-scoped via a join against projects rather than trusting a
    bare project_id — same reasoning as every other project-scoped list here."""
    if get_project(project_id, user_id) is None:
        return []
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    from sqlalchemy import select
    cols = [c for c in conns.c if c.name not in ("access_token_encrypted", "refresh_token_encrypted")]
    with engine.connect() as conn:
        rows = conn.execute(select(*cols).where(conns.c.project_id == project_id)).mappings().all()
    return [dict(r) for r in rows]


def get_oauth_connection(project_id, provider, user_id, include_tokens=False):
    if get_project(project_id, user_id) is None:
        return None
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    from sqlalchemy import select
    cols = list(conns.c) if include_tokens else [
        c for c in conns.c if c.name not in ("access_token_encrypted", "refresh_token_encrypted")
    ]
    with engine.connect() as conn:
        row = conn.execute(
            select(*cols).where(conns.c.project_id == project_id, conns.c.provider == provider)
        ).mappings().first()
    return dict(row) if row else None


def upsert_oauth_connection(project_id, user_id, provider, external_account_email, scopes,
                             access_token_encrypted, refresh_token_encrypted, token_expiry):
    """Insert-or-replace on (project_id, provider) — reconnecting (e.g. after a
    revoked refresh token) should overwrite the old row, not accumulate a second
    one, hence the unique constraint + explicit update-if-exists here rather than
    a DB-level ON CONFLICT (kept portable across the Postgres/SQLite dialects
    this module already supports, same as grant_credits' plain select-then-write)."""
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    from sqlalchemy import select
    now = _now()
    with engine.begin() as conn:
        existing = conn.execute(
            select(conns.c.id).where(conns.c.project_id == project_id, conns.c.provider == provider)
        ).first()
        values = dict(
            user_id=user_id, status="connected", external_account_email=external_account_email,
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
            conn.execute(conns.update().where(conns.c.id == existing.id).values(**values))
        else:
            values.update(project_id=project_id, provider=provider, connected_at=now)
            conn.execute(conns.insert().values(**values))
    return get_oauth_connection(project_id, provider, user_id)


def update_oauth_connection_ref(project_id, provider, user_id, external_ref):
    """Stores the chosen GA4 property id / GSC site URL after the user picks one
    from the list fetched with the connection's access token."""
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    with engine.begin() as conn:
        conn.execute(
            conns.update()
            .where(conns.c.project_id == project_id, conns.c.provider == provider)
            .values(external_ref=external_ref, updated_at=_now())
        )
    return get_oauth_connection(project_id, provider, user_id)


def mark_oauth_connection_tokens(project_id, provider, access_token_encrypted, token_expiry):
    """Refresh-only update — leaves refresh_token/external_ref/status untouched."""
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    with engine.begin() as conn:
        conn.execute(
            conns.update()
            .where(conns.c.project_id == project_id, conns.c.provider == provider)
            .values(access_token_encrypted=access_token_encrypted, token_expiry=token_expiry,
                    status="connected", last_error=None, updated_at=_now())
        )


def mark_oauth_connection_error(project_id, provider, error_message):
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    with engine.begin() as conn:
        conn.execute(
            conns.update()
            .where(conns.c.project_id == project_id, conns.c.provider == provider)
            .values(status="error", last_error=error_message[:500], updated_at=_now())
        )


def delete_oauth_connection(project_id, provider, user_id):
    """A real DELETE, not a status flip — the DPDP framing this phase was built
    under (see the Phase 05B review) commits to an actual delete on disconnect,
    not just token revocation, since a lingering encrypted-token row is still
    personal/business data at rest even if it can no longer be used."""
    if get_project(project_id, user_id) is None:
        return None
    engine = _require_engine()
    tables = _get_tables()
    conns = tables["oauth_connections"]
    from sqlalchemy import select
    with engine.begin() as conn:
        row = conn.execute(
            select(conns.c.access_token_encrypted, conns.c.refresh_token_encrypted)
            .where(conns.c.project_id == project_id, conns.c.provider == provider)
        ).first()
        conn.execute(
            conns.delete().where(conns.c.project_id == project_id, conns.c.provider == provider)
        )
    return dict(row._mapping) if row else None
