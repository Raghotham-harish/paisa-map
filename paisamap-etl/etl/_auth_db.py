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

import calendar
import os
import threading
from datetime import datetime, timedelta, timezone

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
                             DateTime, ForeignKey, UniqueConstraint, CheckConstraint, Index)
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
        # Billing-v2 stage 2a: the PAYING ACCOUNT this company's credits are
        # drawn from. NULL = the company pays for itself (every company today).
        # An agency links its client companies to the agency's own company, so
        # one wallet serves them all while usage stays attributable per client.
        # Exactly one level deep: a payer never has a payer of its own.
        Column("billing_org_id", Integer, ForeignKey("organizations.id")),
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
        # Billing-v2 stage 2a: the wallet (paying company) this row belongs to —
        # org_id's payer at write time, stored so history stays put if a
        # company is later re-linked. NULL only on rows predating this column
        # (backfill_organizations stamps them).
        Column("billing_org_id", Integer, ForeignKey("organizations.id")),
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
        # Billing-v2 stage 2a, additive: which company was billed, and which
        # price book the buyer saw (see _pricing.PRICE_BOOK_VERSION). Nothing
        # reads these yet; they're stamped at order creation so a later
        # company-level cutover and any price experiment have clean history.
        Column("org_id", Integer, ForeignKey("organizations.id")),
        # The wallet the purchase was recorded against ("Buying for: ..."). For
        # now this only RECORDS the buyer's choice — balances are still per-user.
        Column("billing_org_id", Integer, ForeignKey("organizations.id")),
        Column("price_book_version", Text),
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

    # Billing-v2 budgets: a cap, in credits, on what one COMPANY may spend from
    # its wallet per period (kind='cap'), and an optional floor a wallet keeps
    # back for its own company (kind='reserve', org_id = wallet_org_id). The
    # PERIOD TYPE is stored, never dates — recurring windows are computed from
    # it at check time (see budget_window). `wallet_org_id` records who set the
    # cap: a row whose wallet is no longer the company's payer is ignored.
    credit_budgets = Table(
        "credit_budgets", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("wallet_org_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("org_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("kind", Text, nullable=False, server_default="cap"),
        Column("amount", Integer, nullable=False),
        Column("period", Text, nullable=False, server_default="billing_cycle"),
        Column("starts_at", DateTime(timezone=True)),   # one_off / until_date: when counting began
        Column("ends_at", DateTime(timezone=True)),     # until_date: when the budget lapses
        Column("created_by", Integer),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        # Alert de-duplication: the window start and highest level (80 / 100)
        # already announced, so each threshold emails at most once per window.
        Column("notified_window_start", DateTime(timezone=True)),
        Column("notified_level", Integer),
        UniqueConstraint("org_id", "kind", name="uq_credit_budgets_org_kind"),
        CheckConstraint("amount >= 0", name="ck_credit_budgets_amount"),
    )

    # A per-PERSON allowance inside a company: what one member may spend in the
    # company's name per period. Set by an owner/admin of that company (or of
    # the wallet that pays for it) and never above the company's own budget —
    # the company cap still applies on top, so the lower of the two binds.
    credit_member_budgets = Table(
        "credit_member_budgets", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("org_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("amount", Integer, nullable=False),
        Column("period", Text, nullable=False, server_default="billing_cycle"),
        Column("starts_at", DateTime(timezone=True)),
        Column("ends_at", DateTime(timezone=True)),
        Column("created_by", Integer),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("org_id", "user_id", name="uq_credit_member_budgets_org_user"),
        CheckConstraint("amount >= 0", name="ck_credit_member_budgets_amount"),
    )

    # A paying company's REQUEST to pay for another company's credits. It is
    # addressed to an EMAIL (the person who should decide), never to a specific
    # company: the recipient picks which company they own to link when approving,
    # and nothing about who has an account is revealed to the requester.
    credit_link_requests = Table(
        "credit_link_requests", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("payer_org_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("requested_by", Integer, nullable=False),
        Column("target_email", Text, nullable=False),          # lower-cased
        Column("note", Text),
        Column("status", Text, nullable=False, server_default="pending"),   # pending|approved|declined|cancelled
        Column("target_org_id", Integer),                       # set on approval
        Column("resolved_by", Integer),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
        Column("resolved_at", DateTime(timezone=True)),
        Index("ix_credit_link_requests_email", "target_email"),
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
        "credit_budgets": credit_budgets,
        "credit_member_budgets": credit_member_budgets,
        "credit_link_requests": credit_link_requests,
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
        tables["api_keys"], tables["credit_budgets"], tables["credit_member_budgets"],
        tables["credit_link_requests"],
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
    ("orders", "org_id", "INTEGER"),
    ("orders", "billing_org_id", "INTEGER"),
    ("orders", "price_book_version", "TEXT"),
    ("organizations", "billing_org_id", "INTEGER"),
    ("credits_ledger", "billing_org_id", "INTEGER"),
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
def _primary_org_id(conn, user_id):
    """users.org_id — the caller's default company (set at signup / C3
    backfill). None if they somehow have none yet."""
    tables = _get_tables()
    from sqlalchemy import select
    return conn.execute(
        select(tables["users"].c.org_id).where(tables["users"].c.id == user_id)
    ).scalar()


def _payer_org_id(conn, org_id):
    """The wallet (paying company) for `org_id`: its billing_org_id if it has a
    payer, else itself. None in -> None out."""
    if org_id is None:
        return None
    tables = _get_tables()
    from sqlalchemy import select
    orgs = tables["organizations"]
    payer = conn.execute(select(orgs.c.billing_org_id).where(orgs.c.id == org_id)).scalar()
    return payer if payer is not None else org_id


def get_payer_org_id(org_id):
    engine = _require_engine()
    with engine.connect() as conn:
        return _payer_org_id(conn, org_id)


# ── Billing scope (billing-v2 stage 2b) ────────────────────────────────────
# BILLING_SCOPE=user   (default) — exactly the legacy behaviour: a credit
#                      balance is one per-user chain, a user's plan is users.plan.
# BILLING_SCOPE=wallet — a balance is the WALLET's (the paying company's) sum,
#                      shared by every company drawing from it and every member
#                      of those companies; a user's effective plan is the best
#                      of their own plan and their companies' plans.
# Read at call time, so flipping it is one env change + a restart, and flipping
# it back is lossless for single-user wallets (see the runbook for pooled ones).
# Ledger rows keep a per-user balance_after chain in BOTH modes so rollback
# never reads a different number than legacy would have.
def billing_scope():
    return "wallet" if (os.environ.get("BILLING_SCOPE") or "").strip().lower() == "wallet" else "user"


def _wallet_for(conn, user_id, org_id=None):
    """The wallet a balance/spend applies to: the payer of the company being
    worked in (org_id), else of the user's primary company. None if the user
    has no company at all (then callers fall back to the per-user chain)."""
    oid = org_id if org_id is not None else _primary_org_id(conn, user_id)
    return _payer_org_id(conn, oid)


def _wallet_balance(conn, wallet_id, user_id):
    """Sum of every ledger row in this wallet. Rows are matched on
    COALESCE(billing_org_id, org_id) so a row stamped with a company but not
    yet a wallet still counts; rows with NO company at all count only toward
    their own user (temporary safety net — wallet_mode_preflight() insists
    there are none before the switch is flipped)."""
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    from sqlalchemy import select, func, or_, and_
    cond = or_(func.coalesce(ledger.c.billing_org_id, ledger.c.org_id) == wallet_id,
               and_(ledger.c.org_id.is_(None), ledger.c.user_id == user_id))
    return int(conn.execute(
        select(func.coalesce(func.sum(ledger.c.delta), 0)).where(cond)).scalar() or 0)


def _user_chain_balance(conn, user_id):
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    from sqlalchemy import select
    return conn.execute(
        select(ledger.c.balance_after).where(ledger.c.user_id == user_id)
        .order_by(ledger.c.id.desc()).limit(1)).scalar() or 0


def grant_credits(user_id, amount, reason, ref_type=None, ref_id=None, conn=None, org_id=None):
    """Append a credits_ledger row and return the new balance. If `conn` is given,
    runs inside the caller's transaction (used by the signup flow to grant the
    bonus atomically with the user creation); otherwise opens its own.

    Every row is stamped with the company (`org_id`, the caller's primary unless
    passed) and its wallet (`billing_org_id`). BILLING_SCOPE=user returns the
    per-user balance as always; =wallet returns the wallet's balance after."""
    tables = _get_tables()
    ledger = tables["credits_ledger"]

    def _do(c):
        oid = org_id if org_id is not None else _primary_org_id(c, user_id)
        wallet = _payer_org_id(c, oid)
        prev = _user_chain_balance(c, user_id)          # per-user chain, both modes
        c.execute(ledger.insert().values(
            user_id=user_id, org_id=oid, billing_org_id=wallet,
            delta=amount, reason=reason, ref_type=ref_type, ref_id=ref_id,
            balance_after=prev + amount, created_at=_now(),
        ))
        if billing_scope() == "wallet" and wallet is not None:
            return _wallet_balance(c, wallet, user_id)
        return prev + amount

    if conn is not None:
        return _do(conn)
    engine = _require_engine()
    with engine.begin() as c:
        return _do(c)


def get_credit_balance(user_id, org_id=None):
    """user scope: the caller's per-user balance (org_id ignored). wallet scope:
    the balance of the wallet behind `org_id` (the company being worked in) or,
    if omitted, behind the caller's primary company. Callers pass an org_id
    only after checking the user belongs to it."""
    engine = _require_engine()
    with engine.connect() as conn:
        if billing_scope() == "wallet":
            wallet = _wallet_for(conn, user_id, org_id)
            if wallet is not None:
                return _wallet_balance(conn, wallet, user_id)
        # Clamped at 0: legacy data is never negative, but a teammate who spent
        # from a shared wallet has a negative per-user chain, which would show
        # as a negative balance if the scope were ever rolled back to "user".
        return max(0, _user_chain_balance(conn, user_id))


def get_credit_view(user_id, org_id=None):
    """What a USER is allowed to be shown about credits — distinct from
    get_credit_balance(), which is the exact number the server decides with.

    Returns {"balance": int | None, "paid_by": {"org_id", "name"} | None,
    "budget": <the company's budget status> | None, "member_budget": <this
    person's own allowance in it> | None} — both are the company's / the person's
    OWN limits and usage, safe to show (they never reveal the wallet).
    A wallet's balance belongs to the paying company: someone who works in a
    client company but is NOT a member of the company that pays for it (e.g. a
    client's own staff, on an agency-paid company) sees balance=None and who
    pays, never the payer's total. Members of the paying company see the
    number. Under the legacy per-user scope it is always the user's own balance."""
    engine = _require_engine()
    tables = _get_tables()
    with engine.connect() as conn:
        if billing_scope() != "wallet":
            return {"balance": max(0, _user_chain_balance(conn, user_id)), "paid_by": None, "budget": None, "member_budget": None}
        oid = org_id if org_id is not None else _primary_org_id(conn, user_id)
        wallet = _payer_org_id(conn, oid)
        if wallet is None:
            return {"balance": max(0, _user_chain_balance(conn, user_id)), "paid_by": None, "budget": None, "member_budget": None}
        paid_by = None
        if wallet != oid:
            name = conn.execute(_org_name_query(tables, wallet)).scalar()
            paid_by = {"org_id": wallet, "name": name}
        balance = _wallet_balance(conn, wallet, user_id)
        budget = _budget_status(conn, oid, wallet)
        member_budget = _member_budget_status(conn, oid, user_id, wallet)
    if get_org_role(wallet, user_id) is None:
        balance = None
    return {"balance": balance, "paid_by": paid_by, "budget": budget, "member_budget": member_budget}


def _org_name_query(tables, org_id):
    from sqlalchemy import select
    orgs = tables["organizations"]
    return select(orgs.c.name).where(orgs.c.id == org_id)


def resolve_purchase_wallet(user_id, requested_org_id=None):
    """Which company/wallet a purchase is recorded against ("Buying for: ...").
    No company requested -> the buyer's primary company (exactly today's
    behaviour). A company requested -> the buyer must be its owner or admin
    (a plain member may spend a company's credits but not charge for it).
    Returns {"org_id": <company>, "billing_org_id": <its wallet>} or None if
    not permitted. Recording only for now: balances are still per-user."""
    engine = _require_engine()
    with engine.connect() as conn:
        if requested_org_id is None:
            oid = _primary_org_id(conn, user_id)
        else:
            oid = requested_org_id
    if oid is None:
        return {"org_id": None, "billing_org_id": None}
    if requested_org_id is not None and get_org_role(oid, user_id) not in ("owner", "admin"):
        return None
    return {"org_id": oid, "billing_org_id": get_payer_org_id(oid)}


def set_org_payer(org_id, actor_user_id, payer_org_id):
    """Link a company under a paying account (or unlink with payer_org_id=None).
    Rules — each one prevents a way to end up with credits nobody can trace:
      * actor must own `org_id`, and be owner/admin of the payer;
      * a company can't pay for itself via a link, and a payer can't have a
        payer (one level only — no chains, so no cycles);
      * a company that already pays for others can't itself be moved under one.
    Only FUTURE ledger rows use the new wallet; history keeps the wallet it was
    written under. Returns {"status": "ok"} or {"error": ...}."""
    engine = _require_engine()
    tables = _get_tables()
    orgs = tables["organizations"]
    from sqlalchemy import select, func
    if get_org_role(org_id, actor_user_id) != "owner":
        return {"error": "not_found"}
    if payer_org_id is not None:
        if payer_org_id == org_id:
            return {"error": "invalid_payer"}
        if get_org_role(payer_org_id, actor_user_id) not in ("owner", "admin"):
            return {"error": "not_found"}
    with engine.begin() as conn:
        err = _apply_payer(conn, org_id, payer_org_id)
        if err:
            return err
    return {"status": "ok"}


def _apply_payer(conn, org_id, payer_org_id):
    """The rules and the write for linking `org_id` under `payer_org_id` (or
    unlinking with None), on the caller's transaction. Who is ALLOWED to do it
    is the caller's business (an owner acting directly, or an approved request);
    what may never happen is here, once. None on success, else {"error": ...}."""
    tables = _get_tables()
    orgs = tables["organizations"]
    from sqlalchemy import select, func
    if payer_org_id is not None:
        if payer_org_id == org_id:
            return {"error": "invalid_payer"}
        if conn.execute(select(orgs.c.billing_org_id).where(orgs.c.id == payer_org_id)).scalar() is not None:
            return {"error": "payer_has_payer"}
        if conn.execute(select(func.count()).select_from(orgs)
                        .where(orgs.c.billing_org_id == org_id)).scalar():
            return {"error": "already_a_payer"}
        # A company holding credits in its OWN wallet can't be linked: its
        # rows would keep pointing at the old wallet and the credits would
        # be stranded. Spend them down, or merge deliberately.
        ledger = tables["credits_ledger"]
        own = conn.execute(
            select(func.coalesce(func.sum(ledger.c.delta), 0))
            .where(func.coalesce(ledger.c.billing_org_id, ledger.c.org_id) == org_id)).scalar() or 0
        if own != 0:
            return {"error": "company_has_credits"}
    conn.execute(orgs.update().where(orgs.c.id == org_id).values(billing_org_id=payer_org_id))
    # A cap belongs to whoever set it as payer: a new payer (or none) starts
    # clean, and re-linking to the old payer later doesn't resurrect it.
    if _has_budget_table(conn):
        conn.execute(tables["credit_budgets"].delete().where(
            tables["credit_budgets"].c.org_id == org_id, tables["credit_budgets"].c.kind == "cap"))
    return None


def link_extra_companies_to_primary():
    """ONE-TIME, deliberate migration for the credits cutover. Before wallets,
    a user's credits followed the USER across every company they created, so
    their extra companies effectively shared one pool. To keep that true, link
    every extra company under its owner's primary company AND move its ledger
    rows into that wallet (a merge — same person, same pool as before).
    Idempotent: only touches unlinked extra companies, and skips any company
    that itself pays for others. Returns counts."""
    engine = _require_engine()
    tables = _get_tables()
    orgs, users, ledger = tables["organizations"], tables["users"], tables["credits_ledger"]
    from sqlalchemy import select, func, update
    counts = {"linked": 0, "ledger_rows_moved": 0, "skipped_pays_for_others": 0}
    with engine.begin() as conn:
        rows = conn.execute(
            select(orgs.c.id, users.c.org_id.label("primary_id"))
            .select_from(orgs.join(users, users.c.id == orgs.c.owner_user_id))
            .where(orgs.c.billing_org_id.is_(None), users.c.org_id.is_not(None),
                   orgs.c.id != users.c.org_id)).all()
        for r in rows:
            if conn.execute(select(func.count()).select_from(orgs)
                            .where(orgs.c.billing_org_id == r.id)).scalar():
                counts["skipped_pays_for_others"] += 1
                continue
            payer = _payer_org_id(conn, r.primary_id)
            conn.execute(update(orgs).where(orgs.c.id == r.id).values(billing_org_id=payer))
            moved = conn.execute(
                update(ledger)
                .where(func.coalesce(ledger.c.billing_org_id, ledger.c.org_id) == r.id)
                .values(billing_org_id=payer))
            counts["linked"] += 1
            counts["ledger_rows_moved"] += moved.rowcount
    return counts


def credit_wallet_parity():
    """Like credit_org_parity() but grouped by WALLET — the unit a cutover
    actually changes. A wallet "matches" when its ledger sum equals the sum of
    the latest per-user balances of every user with rows in it: true when each
    user's credits live in one wallet, false when one user's credits are split
    across wallets (that user's visible balance would change)."""
    engine = _require_engine()
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    from sqlalchemy import select, func
    wallet = func.coalesce(ledger.c.billing_org_id, ledger.c.org_id)
    with engine.connect() as conn:
        sums = {r.w: int(r.s or 0) for r in conn.execute(
            select(wallet.label("w"), func.sum(ledger.c.delta).label("s"))
            .where(wallet.is_not(None)).group_by(wallet))}
        pairs = conn.execute(select(wallet.label("w"), ledger.c.user_id)
                             .where(wallet.is_not(None)).distinct()).all()
        latest = {}
        for uid in {u for _, u in pairs}:
            latest[uid] = _user_chain_balance(conn, uid)
    per_wallet = {}
    for w, uid in pairs:
        per_wallet.setdefault(w, {})[uid] = latest[uid]
    out, bad = [], []
    for w, total in sorted(sums.items()):
        members_bal = per_wallet.get(w, {})
        ok = sum(members_bal.values()) == total
        if not ok:
            bad.append(w)
        out.append({"wallet_id": w, "wallet_sum": total, "member_user_balances": members_bal, "match": ok})
    return {"wallets": out, "mismatches": bad}


def org_delete_blocker(org_id):
    """Why a company can't be deleted, or None. A company that pays for others
    would orphan them (and lose the row that serialises their spends); one with
    billing history would leave orphaned ledger/order records."""
    engine = _require_engine()
    tables = _get_tables()
    orgs, ledger, orders = tables["organizations"], tables["credits_ledger"], tables["orders"]
    from sqlalchemy import select, func, or_
    with engine.connect() as conn:
        if conn.execute(select(func.count()).select_from(orgs).where(orgs.c.billing_org_id == org_id)).scalar():
            return "pays_for_companies"
        if conn.execute(select(func.count()).select_from(ledger)
                        .where(or_(ledger.c.org_id == org_id, ledger.c.billing_org_id == org_id))).scalar() or \
           conn.execute(select(func.count()).select_from(orders)
                        .where(or_(orders.c.org_id == org_id, orders.c.billing_org_id == org_id))).scalar():
            return "has_billing_history"
    return None


def get_wallet_credit_balance(billing_org_id):
    """Sum of every ledger row belonging to this wallet. Read-only, unused by
    any decision yet — what a wallet balance would be once balances move off
    the per-user chain."""
    engine = _require_engine()
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    from sqlalchemy import select, func
    with engine.connect() as conn:
        total = conn.execute(
            select(func.coalesce(func.sum(ledger.c.delta), 0)).where(ledger.c.billing_org_id == billing_org_id)
        ).scalar()
    return int(total or 0)


def usage_by_company(billing_org_id):
    """Credits SPENT per company against one wallet — the "usage per client"
    view an agency re-bills from. Spends only (negative deltas); purchases and
    grants are excluded. Largest first."""
    engine = _require_engine()
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    orgs = tables["organizations"]
    from sqlalchemy import select, func
    with engine.connect() as conn:
        rows = conn.execute(
            select(ledger.c.org_id, orgs.c.name, func.sum(-ledger.c.delta).label("used"))
            .select_from(ledger.join(orgs, orgs.c.id == ledger.c.org_id))
            .where(ledger.c.billing_org_id == billing_org_id, ledger.c.delta < 0)
            .group_by(ledger.c.org_id, orgs.c.name)
            .order_by(func.sum(-ledger.c.delta).desc())
        ).all()
    return [{"org_id": r.org_id, "name": r.name, "credits_used": int(r.used)} for r in rows]


def get_org_credit_balance(org_id):
    """Sum of every ledger row stamped with this company. NOT used for any
    decision yet (get_credit_balance, the per-user chain, is still what
    spend/checks use) — this is the number a company-level balance would be,
    kept here so it can be compared against the per-user one before any cutover."""
    engine = _require_engine()
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    from sqlalchemy import select, func
    with engine.connect() as conn:
        total = conn.execute(
            select(func.coalesce(func.sum(ledger.c.delta), 0)).where(ledger.c.org_id == org_id)
        ).scalar()
    return int(total or 0)


def credit_org_parity():
    """Read-only audit for the credits cutover: is every ledger row
    company-attributed, and would a company-level balance match what users
    have today? Returns
      {"null_org_rows": N,             # rows still lacking org_id — re-run
                                       # backfill_organizations() to stamp them
       "orgs": [{"org_id", "org_sum", "member_user_balances": {uid: bal},
                 "user_sum", "match": bool}, ...],
       "mismatches": [org_id, ...]}
    An org "matches" when its stamped ledger sum equals the sum of its ledger
    users' latest per-user balances (true for the normal one-user-one-company
    case). A mismatch is exactly the case a cutover has to decide about —
    e.g. one user's credits spread across several companies."""
    engine = _require_engine()
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    from sqlalchemy import select, func
    with engine.connect() as conn:
        null_rows = conn.execute(
            select(func.count()).select_from(ledger).where(ledger.c.org_id.is_(None))
        ).scalar() or 0
        null_wallet_rows = conn.execute(
            select(func.count()).select_from(ledger)
            .where(ledger.c.billing_org_id.is_(None), ledger.c.org_id.is_not(None))
        ).scalar() or 0
        org_sums = {r.org_id: int(r.s or 0) for r in conn.execute(
            select(ledger.c.org_id, func.sum(ledger.c.delta).label("s"))
            .where(ledger.c.org_id.is_not(None)).group_by(ledger.c.org_id))}
        # users who have any row stamped to each org
        pairs = conn.execute(
            select(ledger.c.org_id, ledger.c.user_id).where(ledger.c.org_id.is_not(None)).distinct()
        ).all()
        latest = {}
        for uid in {u for _, u in pairs}:
            latest[uid] = conn.execute(
                select(ledger.c.balance_after).where(ledger.c.user_id == uid)
                .order_by(ledger.c.id.desc()).limit(1)
            ).scalar() or 0
    per_org = {}
    for org_id, uid in pairs:
        per_org.setdefault(org_id, {})[uid] = latest[uid]
    orgs_out, mismatches = [], []
    for org_id, total in sorted(org_sums.items()):
        members = per_org.get(org_id, {})
        user_sum = sum(members.values())
        ok = (user_sum == total)
        if not ok:
            mismatches.append(org_id)
        orgs_out.append({"org_id": org_id, "org_sum": total,
                         "member_user_balances": members, "user_sum": user_sum, "match": ok})
    return {"null_org_rows": int(null_rows), "null_wallet_rows": int(null_wallet_rows),
            "orgs": orgs_out, "mismatches": mismatches}


# ── Budgets (billing-v2) ─────────────────────────────────────────────────────
# A budget caps what one COMPANY may spend from its wallet in a period. It is
# set by an owner/admin of the company that PAYS (the wallet), in credits, and
# enforced in the same transaction as the wallet's balance check, so two
# simultaneous spends can't both slip under a cap. Warn at BUDGET_WARN_PCT, then
# a hard stop — never a surprise bill. Only enforced under BILLING_SCOPE=wallet.
BUDGET_WARN_PCT = 80
BUDGET_MAX_CREDITS = 10_000_000
BUDGET_PERIODS = ("billing_cycle", "calendar_month", "weekly", "quarterly", "one_off", "until_date")
IST = timezone(timedelta(hours=5, minutes=30))   # India has no DST: a fixed offset is exact


class SpendNotAllowedError(Exception):
    """Raised by spend_credits() in wallet scope when a budget rule stops the
    spend. `reason` is one of:
      budget_required  the spender is OUTSIDE the paying company (a client's own
                       staff on an agency-paid company) and no active budget
                       exists for the company — otherwise one client's people
                       could drain the agency's credits for every other client;
      budget_exceeded  this spend would take the company past its budget;
      wallet_reserve   this spend would dip into the credits the paying company
                       has kept back for its own use.
    `info` carries the budget status for budget_exceeded."""
    def __init__(self, reason="budget_required", info=None):
        self.reason = reason
        self.info = info or {}
        super().__init__(reason)


def _add_months(dt, n):
    """dt shifted by n calendar months, clamping the day (Jan 31 + 1 -> Feb 28)."""
    y, m = divmod(dt.month - 1 + n, 12)
    year, month = dt.year + y, m + 1
    return dt.replace(year=year, month=month, day=min(dt.day, calendar.monthrange(year, month)[1]))


def budget_window(period, now, starts_at=None, ends_at=None, cycle_anchor=None):
    """(start, end) of the budget window containing `now`, both aware UTC
    datetimes; end is None for an open-ended one_off. Calendar periods follow
    the IST calendar (a month/quarter starts at 00:00 IST on the 1st, a week on
    Monday). billing_cycle follows the wallet owner's subscription anniversary
    when there is one (`cycle_anchor`), and until subscriptions exist falls
    back to the calendar month — the caller stores the TYPE, never the dates,
    so it starts following real renewals the day they exist. No rollover: each
    window counts only its own spend."""
    now = _ensure_aware(now)
    local = now.astimezone(IST)
    if period == "one_off":
        return _ensure_aware(starts_at), None
    if period == "until_date":
        return _ensure_aware(starts_at), _ensure_aware(ends_at)
    if period == "billing_cycle" and cycle_anchor is not None:
        anchor = _ensure_aware(cycle_anchor).astimezone(IST)
        k = max(0, (local.year - anchor.year) * 12 + local.month - anchor.month)
        if _add_months(anchor, k) > local:
            k = max(0, k - 1)
        start, end = _add_months(anchor, k), _add_months(anchor, k + 1)
    elif period == "weekly":
        start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
    elif period == "quarterly":
        start = local.replace(month=((local.month - 1) // 3) * 3 + 1, day=1,
                              hour=0, minute=0, second=0, microsecond=0)
        end = _add_months(start, 3)
    else:   # calendar_month, and billing_cycle with no subscription yet
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = _add_months(start, 1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _cycle_anchor(conn, wallet_id):
    """When the wallet owner's subscription renews (an aware datetime) — the
    anchor a 'billing_cycle' budget follows. There are no subscriptions yet, so
    None: budgets fall back to the calendar month. This is the ONE place to
    change when subscriptions ship."""
    return None


def _role_in(conn, org_id, user_id):
    members = _get_tables()["org_members"]
    from sqlalchemy import select
    return conn.execute(select(members.c.role).where(
        members.c.org_id == org_id, members.c.user_id == user_id)).scalar()


def _budget_used(conn, org_id, wallet_id, start, end, user_id=None):
    """Credits this company (or, with `user_id`, one person in it) spent from
    this wallet inside [start, end)."""
    ledger = _get_tables()["credits_ledger"]
    from sqlalchemy import select, func
    cond = [ledger.c.org_id == org_id, ledger.c.delta < 0,
            func.coalesce(ledger.c.billing_org_id, ledger.c.org_id) == wallet_id,
            ledger.c.created_at >= start]
    if user_id is not None:
        cond.append(ledger.c.user_id == user_id)
    if end is not None:
        cond.append(ledger.c.created_at < end)
    return int(conn.execute(select(func.coalesce(func.sum(-ledger.c.delta), 0)).where(*cond)).scalar() or 0)


def _has_budget_table(conn, name="credit_budgets"):
    """False until apply_budgets_table.py has run on this database. The
    housekeeping deletes below (relink, company delete) run in EVERY scope, so
    they must not fail on a deploy that reaches the server before the table."""
    from sqlalchemy import inspect
    return inspect(conn).has_table(name)


def _budget_row(conn, org_id, kind):
    b = _get_tables()["credit_budgets"]
    from sqlalchemy import select
    return conn.execute(select(b).where(b.c.org_id == org_id, b.c.kind == kind)).mappings().first()


def _status_from_row(conn, row, wallet_id, org_id, user_id=None, now=None, scope="company"):
    """The status dict for one budget row (a company cap, or with `user_id` a
    person's allowance inside the company). `active` is False once an
    until_date budget has lapsed — a lapsed budget behaves as no budget."""
    now = now or _now()
    period = row["period"]
    anchor = _cycle_anchor(conn, wallet_id) if period == "billing_cycle" else None
    start, end = budget_window(period, now, row["starts_at"], row["ends_at"], anchor)
    used = _budget_used(conn, org_id, wallet_id, start, end, user_id)
    amount = int(row["amount"])
    pct = 100 if amount <= 0 else int(used * 100 // amount)
    active = end is None or now < end
    return {
        "scope": scope,
        "amount": amount, "period": period,
        # what the window really follows — billing_cycle is the calendar month until subscriptions exist
        "resolved_period": "calendar_month" if (period == "billing_cycle" and anchor is None) else period,
        "used": used, "remaining": max(0, amount - used), "pct": pct,
        "warn": active and pct >= BUDGET_WARN_PCT, "exhausted": used >= amount,
        "active": active,
        "window_start": start.isoformat(), "window_end": end.isoformat() if end else None,
    }


def _budget_status(conn, org_id, wallet_id, now=None):
    """The company's cap as a dict, or None if there isn't one (none set, or set
    by someone who no longer pays for this company)."""
    row = _budget_row(conn, org_id, "cap")
    if row is None or row["wallet_org_id"] != wallet_id:
        return None
    return _status_from_row(conn, row, wallet_id, org_id, now=now)


def _member_budget_row(conn, org_id, user_id):
    b = _get_tables()["credit_member_budgets"]
    from sqlalchemy import select
    return conn.execute(select(b).where(b.c.org_id == org_id, b.c.user_id == user_id)).mappings().first()


def _member_budget_status(conn, org_id, user_id, wallet_id, now=None):
    """One person's allowance inside a company, or None if they have none."""
    row = _member_budget_row(conn, org_id, user_id)
    if row is None:
        return None
    return _status_from_row(conn, row, wallet_id, org_id, user_id=user_id, now=now, scope="member")


def _reserve_floor(conn, wallet_id):
    row = _budget_row(conn, wallet_id, "reserve")
    return int(row["amount"]) if row is not None and row["wallet_org_id"] == wallet_id else 0


def _spend_block(conn, wallet_id, org_id, user_id, amount, balance):
    """None if this spend may go ahead, else {"reason": ..., ...} (see
    SpendNotAllowedError). Runs on the caller's connection so that, inside
    spend_credits, it shares the wallet-locked transaction with the balance
    check. `balance` is the wallet's balance before the spend."""
    insider = _role_in(conn, wallet_id, user_id) is not None
    status = _budget_status(conn, org_id, wallet_id)
    capped = status is not None and status["active"]
    if not insider and not capped:
        return {"reason": "budget_required"}
    if capped and status["used"] + amount > status["amount"]:
        return {"reason": "budget_exceeded", "info": status}
    # A personal allowance inside the company sits UNDER its cap: both must pass.
    member = _member_budget_status(conn, org_id, user_id, wallet_id)
    if member is not None and member["active"] and member["used"] + amount > member["amount"]:
        return {"reason": "budget_exceeded", "info": member}
    if org_id != wallet_id:
        floor = _reserve_floor(conn, wallet_id)
        if floor and balance - amount < floor:
            return {"reason": "wallet_reserve"}
    return None


def budget_window_text(status):
    """A budget's window in plain words ("this month", "until 30 Sep") for emails."""
    period = status["resolved_period"]
    if period == "until_date" and status.get("window_end"):
        end = datetime.fromisoformat(status["window_end"]).astimezone(IST) - timedelta(minutes=1)
        return f"until {end.day} {end.strftime('%b')}"
    return {"calendar_month": "this month", "weekly": "this week", "quarterly": "this quarter",
            "billing_cycle": "this billing cycle", "one_off": "in total"}.get(period, "this period")


def _budget_alert_recipients(conn, wallet_id, org_id):
    """Emails of the owners/admins who can act on a budget: those of the paying
    company and those of the company itself, de-duplicated, blanks dropped."""
    tables = _get_tables()
    members, users = tables["org_members"], tables["users"]
    from sqlalchemy import select
    rows = conn.execute(
        select(users.c.email).select_from(members.join(users, users.c.id == members.c.user_id))
        .where(members.c.org_id.in_({wallet_id, org_id}), members.c.role.in_(("owner", "admin")))).all()
    seen, out = set(), []
    for (email,) in rows:
        key = (email or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(email.strip())
    return out


def _budget_crossing(conn, org_id, wallet_id):
    """Called inside the spend transaction, after the ledger row is written.
    If the company's budget has newly crossed 80% or reached 100% in its current
    window, records that (so each level alerts at most once per window — and two
    simultaneous spends can't both fire it) and returns the notice to send once
    the transaction commits; else None. Alerts are at-most-once: the in-app
    banner is the fallback if a send is lost."""
    row = _budget_row(conn, org_id, "cap")
    if row is None or row["wallet_org_id"] != wallet_id:
        return None
    st = _status_from_row(conn, row, wallet_id, org_id)
    if not st["active"]:
        return None
    level = 100 if st["exhausted"] else (BUDGET_WARN_PCT if st["pct"] >= BUDGET_WARN_PCT else 0)
    if not level:
        return None
    seen_window = _ensure_aware(row["notified_window_start"])
    already = (row["notified_level"] or 0) if (
        seen_window is not None and seen_window.astimezone(timezone.utc).isoformat() == st["window_start"]) else 0
    if level <= already:
        return None
    b = _get_tables()["credit_budgets"]
    conn.execute(b.update().where(b.c.id == row["id"]).values(
        notified_window_start=datetime.fromisoformat(st["window_start"]), notified_level=level))
    orgs = _get_tables()["organizations"]
    from sqlalchemy import select
    name = conn.execute(select(orgs.c.name).where(orgs.c.id == org_id)).scalar() or "Your company"
    return {"level": level, "org_id": org_id, "org_name": name, "used": st["used"], "amount": st["amount"],
            "pct": st["pct"], "window_text": budget_window_text(st),
            "recipients": _budget_alert_recipients(conn, wallet_id, org_id)}


def _send_budget_notices(notice):
    """Email a budget alert to each recipient. Never raises: a mail problem
    must not touch a spend that already succeeded."""
    try:
        import _email
        url = (os.environ.get("APP_BASE_URL") or "https://paisamaps.com").rstrip("/") + "/workspace/billing"
        for to in notice["recipients"]:
            try:
                _email.send_budget_notice(to, notice["org_name"], notice["level"], notice["used"],
                                          notice["amount"], notice["window_text"], url)
            except Exception:
                pass
    except Exception:
        pass


def _dispatch_budget_notice(notice):
    """Off the request thread, so a slow mail service never slows a spend."""
    threading.Thread(target=_send_budget_notices, args=(notice,), daemon=True).start()


def wallet_spend_block(user_id, org_id=None, amount=0):
    """None if `user_id` may spend `amount` credits working in `org_id`, else
    {"reason": ..., "info": ...}. Legacy scope: never blocked. A read-only
    pre-check for the routes — spend_credits() re-checks atomically."""
    if billing_scope() != "wallet":
        return None
    engine = _require_engine()
    with engine.connect() as conn:
        oid = org_id if org_id is not None else _primary_org_id(conn, user_id)
        wallet = _payer_org_id(conn, oid)
        if wallet is None:
            return None
        return _spend_block(conn, wallet, oid, user_id, max(int(amount), 1),
                            _wallet_balance(conn, wallet, user_id))


def wallet_spend_allowed(user_id, org_id=None, amount=0):
    """(allowed, reason) — the boolean form of wallet_spend_block()."""
    block = wallet_spend_block(user_id, org_id, amount)
    return (True, None) if block is None else (False, block["reason"])


def _parse_budget_end(value):
    """A date ('YYYY-MM-DD', or a datetime) -> the aware UTC instant that day
    ENDS in IST (start of the next day), or None if unusable or already past."""
    from datetime import date
    if isinstance(value, datetime):
        d = value.astimezone(IST).date() if value.tzinfo else value.date()
    elif isinstance(value, date):
        d = value
    elif isinstance(value, str):
        try:
            d = date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    else:
        return None
    end = datetime(d.year, d.month, d.day, tzinfo=IST) + timedelta(days=1)
    end = end.astimezone(timezone.utc)
    return end if end > _now() else None


def _wallet_admin_error(actor_user_id, wallet_org_id):
    """None if the actor is an owner/admin of the wallet company, else the error
    dict: non-members get the same not_found as a company that doesn't exist."""
    role = get_org_role(wallet_org_id, actor_user_id)
    if role is None:
        return {"error": "not_found"}
    if role not in ("owner", "admin"):
        return {"error": "forbidden"}
    return None


def _check_budget_args(amount, period, ends_at):
    """(error dict | None, aware end instant | None) for a budget's amount,
    period and end date — shared by company budgets and personal allowances."""
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= BUDGET_MAX_CREDITS:
        return {"error": "invalid_amount"}, None
    if period not in BUDGET_PERIODS:
        return {"error": "invalid_period"}, None
    end = None
    if period == "until_date":
        end = _parse_budget_end(ends_at)
        if end is None:
            return {"error": "invalid_end_date"}, None
    return None, end


def set_credit_budget(actor_user_id, wallet_org_id, org_id, amount, period="billing_cycle", ends_at=None):
    """Set (or replace) the credit budget on `org_id` — the wallet company
    itself or a company it pays for. Only an owner/admin of the WALLET may.
    period: one of BUDGET_PERIODS; 'until_date' also needs `ends_at` (a future
    date). One-off and until-date budgets count from the moment they're set.
    Returns {"status": "ok", "budget": <status>} or {"error": ...}."""
    err = _wallet_admin_error(actor_user_id, wallet_org_id)
    if err:
        return err
    err, end = _check_budget_args(amount, period, ends_at)
    if err:
        return err
    engine = _require_engine()
    b = _get_tables()["credit_budgets"]
    with engine.begin() as conn:
        if _payer_org_id(conn, org_id) != wallet_org_id:
            return {"error": "not_found"}      # not the wallet itself, nor a company it pays for
        now = _now()
        existing = _budget_row(conn, org_id, "cap")
        starts = None
        if period in ("one_off", "until_date"):
            starts = now
            if existing is not None and existing["wallet_org_id"] == wallet_org_id \
                    and existing["period"] == period and existing["starts_at"] is not None:
                old_end = _ensure_aware(existing["ends_at"])
                if old_end is None or old_end > now:      # keep counting, unless it had lapsed
                    starts = _ensure_aware(existing["starts_at"])
        # A changed budget starts a fresh alert cycle: after raising it, the next
        # 80% / 100% is news again.
        values = dict(wallet_org_id=wallet_org_id, amount=amount, period=period,
                      starts_at=starts, ends_at=end, created_by=actor_user_id, updated_at=now,
                      notified_window_start=None, notified_level=None)
        if existing is None:
            conn.execute(b.insert().values(org_id=org_id, kind="cap", created_at=now, **values))
        else:
            conn.execute(b.update().where(b.c.id == existing["id"]).values(**values))
        return {"status": "ok", "budget": _budget_status(conn, org_id, wallet_org_id)}


def delete_credit_budget(actor_user_id, wallet_org_id, org_id):
    """Remove a company's cap. A company that another company pays for is then
    back to "no budget" — its own staff can't spend until one is set again."""
    err = _wallet_admin_error(actor_user_id, wallet_org_id)
    if err:
        return err
    engine = _require_engine()
    b = _get_tables()["credit_budgets"]
    with engine.begin() as conn:
        if _payer_org_id(conn, org_id) != wallet_org_id:
            return {"error": "not_found"}
        conn.execute(b.delete().where(b.c.org_id == org_id, b.c.kind == "cap"))
    return {"status": "ok"}


def set_wallet_reserve(actor_user_id, wallet_org_id, credits):
    """Credits the wallet keeps back for its OWN company: spends attributed to
    any other company it pays for can't take the balance below this. None or 0
    clears it."""
    err = _wallet_admin_error(actor_user_id, wallet_org_id)
    if err:
        return err
    if credits is not None and (isinstance(credits, bool) or not isinstance(credits, int)
                                or not 0 <= credits <= BUDGET_MAX_CREDITS):
        return {"error": "invalid_amount"}
    engine = _require_engine()
    b = _get_tables()["credit_budgets"]
    with engine.begin() as conn:
        now = _now()
        existing = _budget_row(conn, wallet_org_id, "reserve")
        if not credits:
            if existing is not None:
                conn.execute(b.delete().where(b.c.id == existing["id"]))
        elif existing is None:
            conn.execute(b.insert().values(
                wallet_org_id=wallet_org_id, org_id=wallet_org_id, kind="reserve", amount=credits,
                period="one_off", created_by=actor_user_id, created_at=now, updated_at=now))
        else:
            conn.execute(b.update().where(b.c.id == existing["id"]).values(
                amount=credits, updated_at=now, created_by=actor_user_id))
    return {"status": "ok", "reserve": credits or None}


def list_wallet_budgets(actor_user_id, wallet_org_id):
    """Everything an owner/admin of a paying company needs to manage budgets:
    the wallet's balance and reserve, and for the company itself plus each
    company it pays for — its budget status (or None) and its spend over the
    last 30 days, so a sensible budget is easy to pick."""
    err = _wallet_admin_error(actor_user_id, wallet_org_id)
    if err:
        return err
    engine = _require_engine()
    orgs = _get_tables()["organizations"]
    from sqlalchemy import select
    now = _now()
    with engine.connect() as conn:
        rows = conn.execute(
            select(orgs.c.id, orgs.c.name).where(
                (orgs.c.id == wallet_org_id) | (orgs.c.billing_org_id == wallet_org_id))
            .order_by(orgs.c.id)).all()
        companies = [{
            "org_id": r.id, "name": r.name, "is_wallet": r.id == wallet_org_id,
            "budget": _budget_status(conn, r.id, wallet_org_id, now),
            "used_30d": _budget_used(conn, r.id, wallet_org_id, now - timedelta(days=30), None),
        } for r in rows]
        return {"wallet_org_id": wallet_org_id,
                "balance": _wallet_balance(conn, wallet_org_id, actor_user_id),
                "reserve": _reserve_floor(conn, wallet_org_id) or None,
                "warn_pct": BUDGET_WARN_PCT, "periods": list(BUDGET_PERIODS),
                # False until BILLING_SCOPE=wallet: budgets can be prepared but aren't enforced yet
                "enforced": billing_scope() == "wallet",
                "companies": companies}


def _member_budget_authority(conn, actor_user_id, org_id):
    """(wallet_id, is_wallet_admin, error). Who may manage personal allowances
    in `org_id`: an owner/admin of the company itself, or of the wallet that
    pays for it. Non-members of both get the same not_found as a missing company."""
    wallet = _payer_org_id(conn, org_id)
    if wallet is None or _org_exists(conn, org_id) is False:
        return None, False, {"error": "not_found"}
    role_org = _role_in(conn, org_id, actor_user_id)
    role_wallet = role_org if wallet == org_id else _role_in(conn, wallet, actor_user_id)
    if role_org is None and role_wallet is None:
        return None, False, {"error": "not_found"}
    is_wallet_admin = role_wallet in ("owner", "admin")
    if not is_wallet_admin and role_org not in ("owner", "admin"):
        return None, False, {"error": "forbidden"}
    return wallet, is_wallet_admin, None


def _org_exists(conn, org_id):
    orgs = _get_tables()["organizations"]
    from sqlalchemy import select
    return conn.execute(select(orgs.c.id).where(orgs.c.id == org_id)).first() is not None


def set_member_budget(actor_user_id, org_id, target_user_id, amount, period="billing_cycle", ends_at=None):
    """Give one member of `org_id` a personal spending allowance there. Allowed
    for an owner/admin of the company (a client's own admin, say) or of the
    wallet that pays for it. INSIDE the company's budget: the amount can't
    exceed it, and a company admin who doesn't also run the wallet needs an
    active company budget to work inside (the payer's admins may set one
    without). The company cap still applies on top — the lower binds.
    Returns {"status": "ok", "budget": <status>} or {"error": ...}."""
    err, end = _check_budget_args(amount, period, ends_at)
    engine = _require_engine()
    b = _get_tables()["credit_member_budgets"]
    with engine.begin() as conn:
        wallet, is_wallet_admin, auth_err = _member_budget_authority(conn, actor_user_id, org_id)
        if auth_err:
            return auth_err
        if err:
            return err
        if _role_in(conn, org_id, target_user_id) is None:
            return {"error": "not_a_member"}
        parent = _budget_status(conn, org_id, wallet)
        parent = parent if parent is not None and parent["active"] else None
        if parent is None and not is_wallet_admin:
            return {"error": "company_budget_required"}
        if parent is not None and amount > parent["amount"]:
            return {"error": "exceeds_company_budget"}
        now = _now()
        existing = _member_budget_row(conn, org_id, target_user_id)
        starts = None
        if period in ("one_off", "until_date"):
            starts = now
            if existing is not None and existing["period"] == period and existing["starts_at"] is not None:
                old_end = _ensure_aware(existing["ends_at"])
                if old_end is None or old_end > now:
                    starts = _ensure_aware(existing["starts_at"])
        values = dict(amount=amount, period=period, starts_at=starts, ends_at=end,
                      created_by=actor_user_id, updated_at=now)
        if existing is None:
            conn.execute(b.insert().values(org_id=org_id, user_id=target_user_id, created_at=now, **values))
        else:
            conn.execute(b.update().where(b.c.id == existing["id"]).values(**values))
        return {"status": "ok", "budget": _member_budget_status(conn, org_id, target_user_id, wallet)}


def delete_member_budget(actor_user_id, org_id, target_user_id):
    """Remove a member's personal allowance (their company's budget still applies)."""
    engine = _require_engine()
    b = _get_tables()["credit_member_budgets"]
    with engine.begin() as conn:
        _, _, auth_err = _member_budget_authority(conn, actor_user_id, org_id)
        if auth_err:
            return auth_err
        conn.execute(b.delete().where(b.c.org_id == org_id, b.c.user_id == target_user_id))
    return {"status": "ok"}


def list_member_budgets(actor_user_id, org_id):
    """The people in `org_id` with their personal allowance (or None) and their
    spend over the last 30 days, plus the company budget they sit inside."""
    engine = _require_engine()
    tables = _get_tables()
    members, users = tables["org_members"], tables["users"]
    from sqlalchemy import select
    now = _now()
    with engine.connect() as conn:
        wallet, is_wallet_admin, auth_err = _member_budget_authority(conn, actor_user_id, org_id)
        if auth_err:
            return auth_err
        rows = conn.execute(
            select(users.c.id, users.c.name, users.c.email, members.c.role)
            .select_from(members.join(users, users.c.id == members.c.user_id))
            .where(members.c.org_id == org_id).order_by(members.c.created_at.asc())).all()
        parent = _budget_status(conn, org_id, wallet, now)
        return {
            "org_id": org_id, "company_budget": parent, "enforced": billing_scope() == "wallet",
            # a client's own admin can only work inside a company budget; the payer's admins needn't
            "can_set_without_company_budget": is_wallet_admin,
            "periods": list(BUDGET_PERIODS),
            "members": [{
                "user_id": r.id, "name": r.name, "email": r.email, "role": r.role,
                "budget": _member_budget_status(conn, org_id, r.id, wallet, now),
                "used_30d": _budget_used(conn, org_id, wallet, now - timedelta(days=30), None, r.id),
            } for r in rows],
        }


# ── Link requests: one company asks to pay for another ───────────────────────
LINK_REQUEST_TTL_DAYS = 14
LINK_REQUEST_MAX_PENDING = 10       # open requests per paying company
LINK_REQUEST_MAX_PER_DAY = 20       # requests a paying company may send in 24 hours
LINK_NOTE_MAX = 200


def _clean_note(note):
    if not isinstance(note, str):
        return None
    import re
    note = re.sub(r"[\x00-\x1f\x7f]", " ", note)
    note = re.sub(r"\s+", " ", note).strip()[:LINK_NOTE_MAX]
    return note or None


def _valid_email(value):
    import re
    return isinstance(value, str) and len(value) <= 254 and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is not None


def _admin_emails(conn, *org_ids):
    """Emails of the owners/admins of these companies, de-duplicated."""
    tables = _get_tables()
    members, users = tables["org_members"], tables["users"]
    from sqlalchemy import select
    rows = conn.execute(
        select(users.c.email).select_from(members.join(users, users.c.id == members.c.user_id))
        .where(members.c.org_id.in_(set(org_ids)), members.c.role.in_(("owner", "admin")))).all()
    seen, out = set(), []
    for (email,) in rows:
        key = (email or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(email.strip())
    return out


def _send_link_notices(notices):
    """Each notice: dict(to, kind, payer_name, company_name, actor_name, note).
    Never raises — a mail problem must not undo a change that already happened."""
    try:
        import _email
        url = (os.environ.get("APP_BASE_URL") or "https://paisamaps.com").rstrip("/") + "/workspace/billing"
        for n in notices:
            try:
                _email.send_billing_link_notice(n["to"], n["kind"], n["payer_name"], n["company_name"], url,
                                                n.get("actor_name"), n.get("note"))
            except Exception:
                pass
    except Exception:
        pass


def _dispatch_link_notices(notices):
    if notices:
        threading.Thread(target=_send_link_notices, args=(notices,), daemon=True).start()


def _org_name(conn, org_id):
    orgs = _get_tables()["organizations"]
    from sqlalchemy import select
    return conn.execute(select(orgs.c.name).where(orgs.c.id == org_id)).scalar()


def _payer_admin_error(actor_user_id, payer_org_id):
    role = get_org_role(payer_org_id, actor_user_id)
    if role is None:
        return {"error": "not_found"}
    return None if role in ("owner", "admin") else {"error": "forbidden"}


def create_link_request(actor_user_id, payer_org_id, target_email, note=None):
    """An owner/admin of a company asks the person at `target_email` to let it
    pay for one of their companies. Deliberately UNINFORMATIVE: the answer is
    the same whether or not that email has an account (so this can't be used to
    find out who uses PaisaMap), and an email goes out only if the address
    already has an account (so it can't be used to mail strangers). One open
    request per (company, email) — asking again refreshes it — and per-company
    caps on open and daily requests. Returns {"status": "sent", "request_id": n}."""
    err = _payer_admin_error(actor_user_id, payer_org_id)
    if err:
        return err
    email = target_email.strip().lower() if isinstance(target_email, str) else ""
    if not _valid_email(email):
        return {"error": "invalid_email"}
    note = _clean_note(note)
    engine = _require_engine()
    tables = _get_tables()
    reqs, users = tables["credit_link_requests"], tables["users"]
    from sqlalchemy import select, func
    now = _now()
    notices = []
    with engine.begin() as conn:
        if _payer_org_id(conn, payer_org_id) != payer_org_id:
            return {"error": "payer_has_payer"}         # a company that is itself paid for can't pay for others
        existing = conn.execute(select(reqs.c.id).where(
            reqs.c.payer_org_id == payer_org_id, reqs.c.target_email == email, reqs.c.status == "pending")).first()
        if existing is None:
            open_n = conn.execute(select(func.count()).select_from(reqs).where(
                reqs.c.payer_org_id == payer_org_id, reqs.c.status == "pending",
                reqs.c.expires_at > now)).scalar() or 0
            if open_n >= LINK_REQUEST_MAX_PENDING:
                return {"error": "too_many_pending"}
            day_n = conn.execute(select(func.count()).select_from(reqs).where(
                reqs.c.payer_org_id == payer_org_id, reqs.c.created_at > now - timedelta(days=1))).scalar() or 0
            if day_n >= LINK_REQUEST_MAX_PER_DAY:
                return {"error": "rate_limited"}
        values = dict(requested_by=actor_user_id, note=note, expires_at=now + timedelta(days=LINK_REQUEST_TTL_DAYS))
        if existing is not None:
            conn.execute(reqs.update().where(reqs.c.id == existing.id).values(**values))
            request_id = existing.id
        else:
            request_id = conn.execute(reqs.insert().values(
                payer_org_id=payer_org_id, target_email=email, status="pending", created_at=now, **values)
            ).inserted_primary_key[0]
        known = conn.execute(select(users.c.id).where(func.lower(users.c.email) == email)).first()
        if known is not None:
            actor = conn.execute(select(users.c.name, users.c.email).where(users.c.id == actor_user_id)).first()
            notices.append(dict(to=email, kind="requested", payer_name=_org_name(conn, payer_org_id),
                                company_name="", actor_name=(actor.name or actor.email) if actor else None, note=note))
    _dispatch_link_notices(notices)
    return {"status": "sent", "request_id": request_id}


def _request_dict(row, payer_name=None, requester=None):
    return {
        "id": row["id"], "payer_org_id": row["payer_org_id"], "payer_name": payer_name,
        "requested_by_name": requester, "target_email": row["target_email"], "note": row["note"],
        "status": row["status"], "target_org_id": row["target_org_id"],
        "created_at": _ensure_aware(row["created_at"]).isoformat(),
        "expires_at": _ensure_aware(row["expires_at"]).isoformat(),
        "resolved_at": _ensure_aware(row["resolved_at"]).isoformat() if row["resolved_at"] else None,
    }


def list_incoming_link_requests(user_id):
    """Open requests addressed to this user's email, each with the companies
    they could link (ones they OWN that no one else pays for and that pay for no
    one), and which of those are blocked (holding credits of their own)."""
    user = get_user(user_id)
    if user is None:
        return {"requests": []}
    engine = _require_engine()
    tables = _get_tables()
    reqs, orgs, members, users, ledger = (tables["credit_link_requests"], tables["organizations"],
                                          tables["org_members"], tables["users"], tables["credits_ledger"])
    from sqlalchemy import select, func
    now = _now()
    email = (user["email"] or "").strip().lower()
    with engine.connect() as conn:
        rows = conn.execute(select(reqs).where(
            func.lower(reqs.c.target_email) == email, reqs.c.status == "pending", reqs.c.expires_at > now)
            .order_by(reqs.c.created_at.desc())).mappings().all()
        if not rows:
            return {"requests": []}
        owned = conn.execute(
            select(orgs.c.id, orgs.c.name, orgs.c.billing_org_id)
            .select_from(orgs.join(members, members.c.org_id == orgs.c.id))
            .where(members.c.user_id == user_id, members.c.role == "owner")).all()
        payers = {r[0] for r in conn.execute(select(orgs.c.billing_org_id).where(orgs.c.billing_org_id.is_not(None))).all()}
        eligible = []
        for o in owned:
            if o.billing_org_id is not None or o.id in payers:
                continue
            has = conn.execute(select(func.coalesce(func.sum(ledger.c.delta), 0)).where(
                func.coalesce(ledger.c.billing_org_id, ledger.c.org_id) == o.id)).scalar() or 0
            eligible.append({"org_id": o.id, "name": o.name, "blocked": "company_has_credits" if has != 0 else None})
        out = []
        for r in rows:
            requester = conn.execute(select(users.c.name, users.c.email).where(users.c.id == r["requested_by"])).first()
            d = _request_dict(r, _org_name(conn, r["payer_org_id"]), (requester.name or requester.email) if requester else None)
            d["companies"] = [c for c in eligible if c["org_id"] != r["payer_org_id"]]
            out.append(d)
    return {"requests": out}


def _own_pending_request(conn, user_id, request_id):
    """(row, error) — the pending, unexpired request addressed to this user's
    email. Every mismatch is the same not_found, so ids can't be probed."""
    reqs = _get_tables()["credit_link_requests"]
    from sqlalchemy import select, func
    user_email = (get_user(user_id) or {}).get("email") or ""
    row = conn.execute(select(reqs).where(reqs.c.id == request_id).with_for_update()
                       if conn.dialect.name == "postgresql" else select(reqs).where(reqs.c.id == request_id)).mappings().first()
    if row is None or row["status"] != "pending" or (row["target_email"] or "").lower() != user_email.strip().lower():
        return None, {"error": "not_found"}
    if _ensure_aware(row["expires_at"]) <= _now():
        return None, {"error": "expired"}
    return row, None


def approve_link_request(user_id, request_id, target_org_id):
    """The person the request was addressed to links one of THEIR companies
    (they must own it) under the requesting company. Same safety rules as any
    link (_apply_payer), plus: the requester must still be an owner/admin of the
    paying company — a request outlives a demotion at its peril."""
    engine = _require_engine()
    reqs = _get_tables()["credit_link_requests"]
    if not isinstance(target_org_id, int) or isinstance(target_org_id, bool):
        return {"error": "invalid_company"}
    notices = []
    with engine.begin() as conn:
        row, err = _own_pending_request(conn, user_id, request_id)
        if err:
            return err
        role = _role_in(conn, target_org_id, user_id)
        if role is None:
            return {"error": "not_found"}
        if role != "owner":
            return {"error": "forbidden"}
        if _role_in(conn, row["payer_org_id"], row["requested_by"]) not in ("owner", "admin"):
            conn.execute(reqs.update().where(reqs.c.id == request_id).values(status="cancelled", resolved_at=_now()))
            return {"error": "request_invalid"}
        # A company someone already pays for must be DETACHED first (which tells
        # its payer) — a request must never quietly take it from them.
        orgs = _get_tables()["organizations"]
        from sqlalchemy import select
        if conn.execute(select(orgs.c.billing_org_id).where(orgs.c.id == target_org_id)).scalar() is not None:
            return {"error": "already_linked"}
        err = _apply_payer(conn, target_org_id, row["payer_org_id"])
        if err:
            return err
        conn.execute(reqs.update().where(reqs.c.id == request_id).values(
            status="approved", target_org_id=target_org_id, resolved_by=user_id, resolved_at=_now()))
        payer_name, company_name = _org_name(conn, row["payer_org_id"]), _org_name(conn, target_org_id)
        notices = [dict(to=e, kind="approved", payer_name=payer_name, company_name=company_name)
                   for e in _admin_emails(conn, row["payer_org_id"])]
    _dispatch_link_notices(notices)
    return {"status": "ok", "org_id": target_org_id}


def decline_link_request(user_id, request_id):
    engine = _require_engine()
    reqs = _get_tables()["credit_link_requests"]
    with engine.begin() as conn:
        row, err = _own_pending_request(conn, user_id, request_id)
        if err:
            return err
        conn.execute(reqs.update().where(reqs.c.id == request_id).values(
            status="declined", resolved_by=user_id, resolved_at=_now()))
        payer_name = _org_name(conn, row["payer_org_id"])
        notices = [dict(to=e, kind="declined", payer_name=payer_name, company_name="the company you asked")
                   for e in _admin_emails(conn, row["payer_org_id"])]
    _dispatch_link_notices(notices)
    return {"status": "ok"}


def cancel_link_request(actor_user_id, payer_org_id, request_id):
    """The paying company withdraws an open request."""
    err = _payer_admin_error(actor_user_id, payer_org_id)
    if err:
        return err
    engine = _require_engine()
    reqs = _get_tables()["credit_link_requests"]
    with engine.begin() as conn:
        done = conn.execute(reqs.update().where(
            reqs.c.id == request_id, reqs.c.payer_org_id == payer_org_id, reqs.c.status == "pending")
            .values(status="cancelled", resolved_by=actor_user_id, resolved_at=_now()))
    return {"status": "ok"} if done.rowcount else {"error": "not_found"}


def list_outgoing_link_requests(actor_user_id, payer_org_id):
    """A paying company's requests: the open ones, and the last few decided."""
    err = _payer_admin_error(actor_user_id, payer_org_id)
    if err:
        return err
    engine = _require_engine()
    tables = _get_tables()
    reqs, orgs = tables["credit_link_requests"], tables["organizations"]
    from sqlalchemy import select
    now = _now()
    with engine.connect() as conn:
        rows = conn.execute(select(reqs).where(reqs.c.payer_org_id == payer_org_id)
                            .order_by(reqs.c.created_at.desc()).limit(30)).mappings().all()
        out = []
        for r in rows:
            d = _request_dict(r)
            if r["status"] == "pending" and _ensure_aware(r["expires_at"]) <= now:
                d["status"] = "expired"
            d["target_org_name"] = _org_name(conn, r["target_org_id"]) if r["target_org_id"] else None
            out.append(d)
    return {"requests": out}


def unlink_company(actor_user_id, org_id):
    """EITHER side detaches, immediately: the company's owner, or an owner/admin
    of the company that pays for it. From then on it pays for itself — its own
    wallet starts empty, its old spending stays on the payer's books, and the
    payer's cap on it goes with the link."""
    engine = _require_engine()
    orgs = _get_tables()["organizations"]
    from sqlalchemy import select
    with engine.begin() as conn:
        exists = conn.execute(select(orgs.c.id, orgs.c.billing_org_id).where(orgs.c.id == org_id)).first()
        if exists is None:
            return {"error": "not_found"}
        payer = exists.billing_org_id
        role_org = _role_in(conn, org_id, actor_user_id)
        role_payer = _role_in(conn, payer, actor_user_id) if payer is not None else None
        if role_org is None and role_payer is None:
            return {"error": "not_found"}
        if payer is None:
            return {"error": "not_linked"}
        if role_org != "owner" and role_payer not in ("owner", "admin"):
            return {"error": "forbidden"}
        by_payer = role_payer in ("owner", "admin")
        payer_name, company_name = _org_name(conn, payer), _org_name(conn, org_id)
        _apply_payer(conn, org_id, None)
        # tell the OTHER side
        notices = [dict(to=e, kind="detached", payer_name=payer_name, company_name=company_name)
                   for e in (_admin_emails(conn, org_id) if by_payer and role_org != "owner" else _admin_emails(conn, payer))]
    _dispatch_link_notices(notices)
    return {"status": "ok"}


def billing_link_view(user_id, org_id):
    """What the "who pays" panel shows for a company you belong to: who pays for
    it (if anyone) and whether you may detach it, and — for an owner/admin of a
    paying company — the companies it pays for."""
    engine = _require_engine()
    orgs = _get_tables()["organizations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        role = _role_in(conn, org_id, user_id)
        if role is None:
            return {"error": "not_found"}
        payer = conn.execute(select(orgs.c.billing_org_id).where(orgs.c.id == org_id)).scalar()
        out = {"org_id": org_id, "role": role, "paid_by": None, "can_detach": False,
               "pays_for": [], "can_manage_links": False}
        if payer is not None:
            out["paid_by"] = {"org_id": payer, "name": _org_name(conn, payer)}
            out["can_detach"] = role == "owner"
        if role in ("owner", "admin") and payer is None:
            out["can_manage_links"] = True
            out["pays_for"] = [{"org_id": r.id, "name": r.name} for r in conn.execute(
                select(orgs.c.id, orgs.c.name).where(orgs.c.billing_org_id == org_id).order_by(orgs.c.id))]
    return out


# ── Usage statement: who spent what, on whose wallet ─────────────────────────
STATEMENT_PERIODS = ("this_month", "last_month", "last_30d")


def statement_window(period, now=None):
    """(start, end) aware UTC for a statement period, or None. Months are IST."""
    now = _ensure_aware(now or _now())
    if period == "last_30d":
        return now - timedelta(days=30), now
    if period not in ("this_month", "last_month"):
        return None
    start, end = budget_window("calendar_month", now)
    if period == "this_month":
        return start, end
    return _add_months(start.astimezone(IST), -1).astimezone(timezone.utc), start


def usage_statement(actor_user_id, org_id, period="this_month"):
    """Credits SPENT in a period: per company, per person, per kind of action —
    and nothing about which project or location (that is the client's business).
    For an owner/admin of a paying company (org_id = the wallet): every company
    it paid for, including ones since detached. For an owner/admin of a company
    that someone else pays for: that company only. Purchases are never included."""
    win = statement_window(period)
    if win is None:
        return {"error": "invalid_period"}
    engine = _require_engine()
    tables = _get_tables()
    ledger, orgs, users = tables["credits_ledger"], tables["organizations"], tables["users"]
    from sqlalchemy import select, func
    start, end = win
    with engine.connect() as conn:
        role = _role_in(conn, org_id, actor_user_id)
        if role is None:
            return {"error": "not_found"}
        if role not in ("owner", "admin"):
            return {"error": "forbidden"}
        wallet = _payer_org_id(conn, org_id)
        wallet_scope = wallet == org_id        # a company nobody else pays for: the whole wallet
        where = [ledger.c.delta < 0, ledger.c.created_at >= start, ledger.c.created_at < end]
        if wallet_scope:
            where.append(func.coalesce(ledger.c.billing_org_id, ledger.c.org_id) == org_id)
        else:
            where.append(ledger.c.org_id == org_id)
        rows = conn.execute(
            select(ledger.c.org_id, ledger.c.user_id, ledger.c.reason,
                   func.sum(-ledger.c.delta).label("credits"), func.count().label("n"))
            .where(*where).group_by(ledger.c.org_id, ledger.c.user_id, ledger.c.reason)).all()
        companies = {}
        if wallet_scope:                       # show every company it currently pays for, even at zero
            for r in conn.execute(select(orgs.c.id, orgs.c.name).where(
                    (orgs.c.id == org_id) | (orgs.c.billing_org_id == org_id))):
                companies[r.id] = {"org_id": r.id, "name": r.name, "credits_used": 0, "_people": {}}
        else:
            companies[org_id] = {"org_id": org_id, "name": _org_name(conn, org_id), "credits_used": 0, "_people": {}}
        for r in rows:
            c = companies.get(r.org_id)
            if c is None:
                c = companies[r.org_id] = {"org_id": r.org_id, "name": _org_name(conn, r.org_id) or "(deleted company)",
                                           "credits_used": 0, "_people": {}}
            credits = int(r.credits)
            c["credits_used"] += credits
            person = c["_people"].setdefault(r.user_id, {"user_id": r.user_id, "credits_used": 0, "actions": []})
            person["credits_used"] += credits
            person["actions"].append({"reason": r.reason, "count": int(r.n), "credits": credits})
        names = {u.id: (u.name, u.email) for u in conn.execute(select(users.c.id, users.c.name, users.c.email))} if rows else {}
    out = []
    for c in companies.values():
        people = sorted(c.pop("_people").values(), key=lambda p: -p["credits_used"])
        for p in people:
            p["name"], p["email"] = names.get(p["user_id"], (None, None))
            p["actions"].sort(key=lambda a: -a["credits"])
        c["people"] = people
        out.append(c)
    out.sort(key=lambda c: (-c["credits_used"], c["name"] or ""))
    return {"org_id": org_id, "scope": "wallet" if wallet_scope else "company", "period": period,
            "periods": list(STATEMENT_PERIODS),
            "window_start": start.isoformat(), "window_end": end.isoformat(),
            "total_credits_used": sum(c["credits_used"] for c in out), "companies": out}


class InsufficientCreditsError(Exception):
    """Raised by spend_credits() when the balance is short. The caller (a
    blueprint route) turns this into a 402 Payment Required with the current
    balance and the shortfall, so the frontend can offer a top-up or a one-off
    purchase inline instead of a bare error."""
    def __init__(self, balance, required):
        self.balance = balance
        self.required = required
        super().__init__(f"insufficient credits: have {balance}, need {required}")


def _spend_credits_tx(user_id, amount, reason, ref_type=None, ref_id=None, org_id=None):
    """Debit `amount` credits if the balance covers it, atomically. Raises
    InsufficientCreditsError (balance left unchanged) if not.

    user scope: checks/chains the caller's own balance (locking the latest
    ledger row on Postgres so two concurrent spends can't both read a stale
    balance). wallet scope: checks the WALLET's balance, serialising concurrent
    spends on the wallet company's row (SELECT ... FOR UPDATE on Postgres;
    SQLite is single-writer so it degrades safely). `org_id` is the company the
    spend is attributed to (a project's company when project-scoped)."""
    assert amount > 0, "spend_credits amount must be positive"
    engine = _require_engine()
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    from sqlalchemy import select
    with engine.begin() as conn:
        oid = org_id if org_id is not None else _primary_org_id(conn, user_id)
        wallet = _payer_org_id(conn, oid)
        if billing_scope() == "wallet" and wallet is not None:
            if engine.dialect.name == "postgresql":
                conn.execute(select(tables["organizations"].c.id)
                             .where(tables["organizations"].c.id == wallet).with_for_update())
            balance = _wallet_balance(conn, wallet, user_id)
            # After the wallet lock, in the same transaction: budget/reserve
            # rules see exactly the state this spend will be written against.
            block = _spend_block(conn, wallet, oid, user_id, amount, balance)
            if block is not None:
                raise SpendNotAllowedError(block["reason"], block.get("info"))
            if balance < amount:
                raise InsufficientCreditsError(balance, amount)
            conn.execute(ledger.insert().values(
                user_id=user_id, org_id=oid, billing_org_id=wallet,
                delta=-amount, reason=reason, ref_type=ref_type, ref_id=ref_id,
                balance_after=_user_chain_balance(conn, user_id) - amount, created_at=_now(),
            ))
            return balance - amount, _budget_crossing(conn, oid, wallet)
        query = (select(ledger.c.balance_after).where(ledger.c.user_id == user_id)
                 .order_by(ledger.c.id.desc()).limit(1))
        if engine.dialect.name == "postgresql":
            query = query.with_for_update()
        balance = conn.execute(query).scalar() or 0
        if balance < amount:
            raise InsufficientCreditsError(balance, amount)
        new_balance = balance - amount
        conn.execute(ledger.insert().values(
            user_id=user_id, org_id=oid, billing_org_id=wallet,
            delta=-amount, reason=reason, ref_type=ref_type, ref_id=ref_id,
            balance_after=new_balance, created_at=_now(),
        ))
        return new_balance, None


def spend_credits(user_id, amount, reason, ref_type=None, ref_id=None, org_id=None):
    """Debit `amount` credits if the balance and every budget rule allow it,
    atomically (see _spend_credits_tx). Returns the balance after. If the spend
    takes a company's budget to 80% or 100% for the first time in its window,
    the owners/admins involved are emailed AFTER the transaction commits."""
    balance, notice = _spend_credits_tx(user_id, amount, reason, ref_type, ref_id, org_id)
    if notice is not None:
        try:
            _dispatch_budget_notice(notice)
        except Exception:
            pass      # the spend is committed; an alert problem must never reach the spender
    return balance


# ── Plan ─────────────────────────────────────────────────────────────────────
def get_effective_plan_for_user(user_id):
    """The plan that gates a user's features. user scope: users.plan, exactly
    as before. wallet scope: the BEST of their own plan and the plan of every
    paying company they belong to (a seat in a paid company comes with its
    plan) — it can only raise a user's plan, never lower it. Only legacy plan
    ids ('free'/'pro'/'team') count here; anything else (a v2 id, junk) is
    ignored, fail-closed, until v2 enforcement exists."""
    import _pricing
    user = get_user(user_id)
    own = user["plan"] if user else "free"
    if user is None or billing_scope() != "wallet":
        return own
    engine = _require_engine()
    tables = _get_tables()
    orgs, members = tables["organizations"], tables["org_members"]
    from sqlalchemy import select
    best = own
    with engine.connect() as conn:
        rows = conn.execute(
            select(orgs.c.id, orgs.c.billing_org_id)
            .select_from(members.join(orgs, orgs.c.id == members.c.org_id))
            .where(members.c.user_id == user_id)).all()
        for payer in {r.billing_org_id or r.id for r in rows}:
            plan = conn.execute(select(orgs.c.plan).where(orgs.c.id == payer)).scalar()
            if _pricing.parse_plan(plan)[0] in ("free", "legacy") and \
                    _pricing.plan_rank(plan) > _pricing.plan_rank(best):
                best = plan
    return best


def plan_org_parity():
    """Owners whose users.plan disagrees with their own company's plan.
    Informational, not blocking: org plans are mirrored only by set_user_plan,
    so a plan changed any other way (e.g. a manual database flip) leaves the
    company stale. In wallet scope the higher one wins, so it never downgrades
    anyone — but each row is a place the two vocabularies still disagree."""
    engine = _require_engine()
    tables = _get_tables()
    users, orgs = tables["users"], tables["organizations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(
            select(users.c.id, users.c.plan, orgs.c.id.label("org_id"), orgs.c.plan.label("org_plan"))
            .select_from(users.join(orgs, orgs.c.id == users.c.org_id))
            .where(orgs.c.owner_user_id == users.c.id, users.c.plan != orgs.c.plan)).all()
    return [{"user_id": r.id, "user_plan": r.plan, "org_id": r.org_id, "org_plan": r.org_plan} for r in rows]


def wallet_mode_preflight():
    """Everything that must be true before BILLING_SCOPE=wallet is switched on
    against real data. {"ok": bool, "problems": [...], "info": {...}}. Read-only."""
    engine = _require_engine()
    tables = _get_tables()
    users = tables["users"]
    from sqlalchemy import select, func
    rep = credit_org_parity()
    problems = []
    if rep["null_org_rows"]:
        problems.append(f"{rep['null_org_rows']} ledger rows have no company — run backfill_organizations()")
    if rep["null_wallet_rows"]:
        problems.append(f"{rep['null_wallet_rows']} ledger rows have no wallet — run backfill_organizations()")
    wp = credit_wallet_parity()
    if wp["mismatches"]:
        problems.append("wallet ledger sums differ from the per-user balances for wallets "
                        f"{wp['mismatches']} (one user's credits split across wallets) — "
                        "a wallet-level balance would visibly change for these users; decide each one")
    orgs = tables["organizations"]
    from sqlalchemy import inspect
    for tbl in ("credit_budgets", "credit_member_budgets"):
        if not inspect(engine).has_table(tbl):
            problems.append(f"the {tbl} table doesn't exist — run paisamap-etl/db/apply_budgets_table.py "
                            "(wallet mode reads it on every spend)")
    with engine.connect() as conn:
        no_org = conn.execute(select(func.count()).select_from(users).where(users.c.org_id.is_(None))).scalar() or 0
        extra_unlinked = conn.execute(
            select(func.count()).select_from(orgs.join(users, users.c.id == orgs.c.owner_user_id))
            .where(orgs.c.billing_org_id.is_(None), users.c.org_id.is_not(None),
                   orgs.c.id != users.c.org_id)).scalar() or 0
    if extra_unlinked:
        problems.append(f"{extra_unlinked} extra companies are not linked under their owner's primary "
                        "company — they would start with empty wallets; run link_extra_companies_to_primary()")
    if no_org:
        problems.append(f"{no_org} users have no primary company — run backfill_organizations()")
    return {"ok": not problems, "problems": problems,
            "info": {"plan_mismatches": plan_org_parity(), "companies_with_credits": len(rep["orgs"])}}


def set_user_plan(user_id, plan):
    """First-ever writer of users.plan post-signup (upsert_user only ever sets
    it to 'free' at creation). No plan-history table this phase — activity_log
    already covers "when did this happen" well enough."""
    assert plan in ("free", "pro", "team")
    engine = _require_engine()
    tables = _get_tables()
    users = tables["users"]
    orgs = tables["organizations"]
    with engine.begin() as conn:
        conn.execute(users.update().where(users.c.id == user_id).values(plan=plan))
        # Billing-v2 stage 2a dual-write: keep the caller's OWN company's plan
        # in step (organizations.plan was only ever set at backfill, so it went
        # stale on every later flip). Only when they own it — buying a plan
        # must never rewrite someone else's company. users.plan stays the
        # enforced value; nothing reads organizations.plan yet.
        org_id = _primary_org_id(conn, user_id)
        if org_id is not None:
            conn.execute(orgs.update()
                         .where(orgs.c.id == org_id, orgs.c.owner_user_id == user_id)
                         .values(plan=plan))
    return get_user(user_id)


# ── Orders (Razorpay) ────────────────────────────────────────────────────────
def create_order(user_id, kind, razorpay_order_id, amount_paise, *, credit_pack_id=None,
                  target_plan=None, project_id=None, report_id=None, meta=None,
                  org_id=None, billing_org_id=None):
    engine = _require_engine()
    tables = _get_tables()
    orders = tables["orders"]
    import _pricing
    with engine.begin() as conn:
        result = conn.execute(
            orders.insert().values(
                user_id=user_id, kind=kind, razorpay_order_id=razorpay_order_id,
                amount_paise=amount_paise, currency="INR", status="created",
                credit_pack_id=credit_pack_id, target_plan=target_plan,
                project_id=project_id, report_id=report_id, meta=meta,
                org_id=org_id if org_id is not None else _primary_org_id(conn, user_id),
                billing_org_id=(billing_org_id if billing_org_id is not None else
                                _payer_org_id(conn, org_id if org_id is not None
                                              else _primary_org_id(conn, user_id))),
                price_book_version=_pricing.PRICE_BOOK_VERSION,
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
    """Creates the org and adds the creator as 'owner' in one transaction.

    An ADDITIONAL company is created linked under the creator's own paying
    account (their primary company's wallet) — extra companies share the
    account's credit pool, they don't start with an empty one (pricing doc:
    the extra-company fee adds a company, not credits). Unlink it with
    set_org_payer(..., None) if it should pay for itself."""
    engine = _require_engine()
    tables = _get_tables()
    orgs = tables["organizations"]
    members = tables["org_members"]
    now = _now()
    with engine.begin() as conn:
        primary = _primary_org_id(conn, user_id)
        payer = _payer_org_id(conn, primary) if primary is not None else None
        result = conn.execute(
            orgs.insert().values(name=name, owner_user_id=user_id, plan="free",
                                 billing_org_id=payer, created_at=now)
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
    from sqlalchemy import or_
    budgets = tables["credit_budgets"]
    with engine.begin() as conn:
        if _has_budget_table(conn):
            conn.execute(budgets.delete().where(or_(budgets.c.org_id == org_id, budgets.c.wallet_org_id == org_id)))
        if _has_budget_table(conn, "credit_member_budgets"):
            conn.execute(tables["credit_member_budgets"].delete().where(
                tables["credit_member_budgets"].c.org_id == org_id))
        if _has_budget_table(conn, "credit_link_requests"):
            conn.execute(tables["credit_link_requests"].delete().where(
                tables["credit_link_requests"].c.payer_org_id == org_id))
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

    # Billing-v2 stage 2a: every stamped ledger row also needs its wallet (the
    # payer of its company, or the company itself when unlinked). Correlated
    # subquery keeps this one statement and dialect-neutral.
    from sqlalchemy import text
    with engine.begin() as conn:
        r = conn.execute(text(
            "UPDATE credits_ledger SET billing_org_id = COALESCE("
            "(SELECT o.billing_org_id FROM organizations o WHERE o.id = credits_ledger.org_id), org_id) "
            "WHERE billing_org_id IS NULL AND org_id IS NOT NULL"))
        counts["ledger_wallets_stamped"] = r.rowcount

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


def list_credit_ledger(user_id, limit=50, org_id=None):
    """user scope: the caller's own rows, unchanged. wallet scope: the rows of
    the wallet behind `org_id` (or the caller's primary company) that the caller
    is allowed to see — their own, plus any in a company they belong to. A
    teammate's activity in a client company you have no access to (project ids,
    company names) is never shown. balance_after is recomputed as the wallet's
    running balance — but only for members of the PAYING company; anyone else
    (a client's own staff) gets None, since that number is the payer's total."""
    engine = _require_engine()
    tables = _get_tables()
    ledger = tables["credits_ledger"]
    members = tables["org_members"]
    from sqlalchemy import select, func, or_, and_
    with engine.connect() as conn:
        if billing_scope() != "wallet" or _wallet_for(conn, user_id, org_id) is None:
            rows = conn.execute(
                select(ledger).where(ledger.c.user_id == user_id)
                .order_by(ledger.c.id.desc()).limit(limit)
            ).mappings().all()
            return [dict(r) for r in rows]
        wallet = _wallet_for(conn, user_id, org_id)
        cond = or_(func.coalesce(ledger.c.billing_org_id, ledger.c.org_id) == wallet,
                   and_(ledger.c.org_id.is_(None), ledger.c.user_id == user_id))
        rows = [dict(r) for r in conn.execute(
            select(ledger).where(cond).order_by(ledger.c.id.desc()).limit(_LEDGER_SCAN)).mappings()]
        mine = {r[0] for r in conn.execute(select(members.c.org_id).where(members.c.user_id == user_id))}
        running = _wallet_balance(conn, wallet, user_id)
    wallet_visible = wallet in mine      # the running total is the PAYER's number
    out = []
    for r in rows:                       # newest -> oldest, over ALL wallet rows
        r["balance_after"] = running if wallet_visible else None
        running -= r["delta"]
        if r["user_id"] == user_id or r["org_id"] in mine:
            out.append(r)
    return out[:limit]


_LEDGER_SCAN = 2000     # newest wallet rows considered when rebuilding the running balance


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
    return {"key_id": row["id"], "user_id": row["user_id"],
            "plan": get_effective_plan_for_user(row["user_id"])}


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
