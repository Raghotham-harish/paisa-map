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

    _tables = {
        "organizations": organizations, "users": users, "org_members": org_members,
        "projects": projects, "saved_locations": saved_locations, "reports": reports,
        "credits_ledger": credits_ledger, "activity_log": activity_log,
        "customer_uploads": customer_uploads, "customer_locations": customer_locations,
    }
    return _tables


def init_schema():
    """Create all 8 tables if they don't exist yet. Raises if DATABASE_URL unset —
    callers (init_auth_schema.py) are expected to check enabled() first and print
    a friendly message rather than let this raise."""
    engine = _require_engine()
    tables = _get_tables()
    # organizations before users (users.org_id references it) — no real cycle since
    # organizations.owner_user_id is left unconstrained at the SQLAlchemy level.
    _metadata.create_all(engine, tables=[
        tables["organizations"], tables["users"], tables["org_members"],
        tables["projects"], tables["saved_locations"], tables["reports"],
        tables["credits_ledger"], tables["activity_log"],
        tables["customer_uploads"], tables["customer_locations"],
    ])


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
                           "avg_ticket", "website_url")


# ── Projects ─────────────────────────────────────────────────────────────────
def create_project(user_id, name, description=None, business_type=None,
                    target_segment=None, avg_ticket=None, website_url=None):
    engine = _require_engine()
    tables = _get_tables()
    projects = tables["projects"]
    now = _now()
    with engine.begin() as conn:
        result = conn.execute(
            projects.insert().values(
                user_id=user_id, name=name, description=description,
                business_type=business_type, target_segment=target_segment,
                avg_ticket=avg_ticket, website_url=website_url,
                created_at=now, updated_at=now,
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
