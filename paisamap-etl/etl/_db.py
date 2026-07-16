"""
_db.py — shared Postgres connector for PaisaMap's "core tables": pincodes
(PPI/income/spend, replacing ppi_ml_refined.csv / ppi_map_data.csv as the
system of record) and enrichment_log (the audit trail, replacing
enrichment_log.csv).

This is a silent no-op everywhere DATABASE_URL isn't set — every caller in
enrich_single.py / batch_enrich_hces.py / server.py writes the CSVs exactly
as before and *additionally* calls into here. Nothing breaks in an
environment that hasn't set up the database yet, including this dev
machine. Once the DB has been dual-written-to in production long enough to
trust, the CSV writes can be dropped and reads cut over.

Set DATABASE_URL to enable, e.g.:
  postgresql+psycopg2://paisamap:PASSWORD@localhost:5432/paisamap   (prod)
  sqlite:///paisamap_dev.db                                        (local testing only)
"""

import os
import threading
from datetime import datetime, timezone

_engine = None
_metadata = None
_tables = None
_lock = threading.Lock()


def _get_engine():
    global _engine
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    if _engine is None:
        with _lock:
            if _engine is None:
                from sqlalchemy import create_engine
                _engine = create_engine(url, pool_pre_ping=True, future=True)
    return _engine


def _get_tables():
    global _metadata, _tables
    if _tables is not None:
        return _tables
    from sqlalchemy import (MetaData, Table, Column, Text, Integer, Float,
                             DateTime, UniqueConstraint)
    _metadata = MetaData()
    pincodes = Table(
        "pincodes", _metadata,
        Column("pincode", Text, primary_key=True),
        Column("name", Text, nullable=False),
        Column("lat", Float, nullable=False),
        Column("lng", Float, nullable=False),
        Column("ppi_ml", Integer, nullable=False),
        Column("ppi_original", Float, nullable=True),
        Column("est_monthly_income_hh", Float, nullable=False),
        Column("est_monthly_spend_hh", Float, nullable=True),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    enrichment_log = Table(
        "enrichment_log", _metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("ts", DateTime(timezone=True), nullable=False),
        Column("pincode", Text, nullable=False),
        Column("name", Text, nullable=True),
        Column("lat", Float, nullable=True),
        Column("lng", Float, nullable=True),
        Column("source", Text, nullable=False),
        Column("ppi", Integer, nullable=True),
        Column("income", Float, nullable=True),
        UniqueConstraint("ts", "pincode", "source", name="uq_enrichment_log_event"),
    )
    _tables = {"pincodes": pincodes, "enrichment_log": enrichment_log}
    return _tables


def enabled() -> bool:
    return _get_engine() is not None


def init_schema():
    """Create tables if they don't exist yet. No-op if DATABASE_URL unset."""
    engine = _get_engine()
    if engine is None:
        return
    tables = _get_tables()
    _metadata.create_all(engine, tables=list(tables.values()))


def _upsert_stmt(table_name, values, conflict_cols, update_cols):
    tables = _get_tables()
    table = tables[table_name]
    engine = _get_engine()
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    stmt = dialect_insert(table).values(**values)
    if update_cols:
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict_cols,
            set_={c: getattr(stmt.excluded, c) for c in update_cols},
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
    return stmt


def upsert_pincode(pincode, name, lat, lng, ppi_ml, ppi_original,
                    income, spend, updated_at=None):
    """Insert or update one pincode row. Silently no-ops if DB isn't configured."""
    engine = _get_engine()
    if engine is None:
        return
    values = dict(
        pincode=str(pincode), name=name, lat=float(lat), lng=float(lng),
        ppi_ml=int(ppi_ml),
        ppi_original=(float(ppi_original) if ppi_original not in (None, "", "None") else None),
        est_monthly_income_hh=float(income),
        est_monthly_spend_hh=(float(spend) if spend not in (None, "", "None") else None),
        updated_at=updated_at or datetime.now(timezone.utc),
    )
    stmt = _upsert_stmt(
        "pincodes", values, conflict_cols=["pincode"],
        update_cols=["name", "lat", "lng", "ppi_ml", "ppi_original",
                     "est_monthly_income_hh", "est_monthly_spend_hh", "updated_at"],
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def bulk_upsert_pincodes(rows):
    """rows: iterable of dicts with the same keys as upsert_pincode's kwargs (minus updated_at, defaulted)."""
    engine = _get_engine()
    if engine is None or not rows:
        return 0
    n = 0
    with engine.begin() as conn:
        for r in rows:
            values = dict(
                pincode=str(r["pincode"]), name=r["name"],
                lat=float(r["lat"]), lng=float(r["lng"]),
                ppi_ml=int(r["ppi_ml"]),
                ppi_original=(float(r["ppi_original"])
                              if r.get("ppi_original") not in (None, "", "None") else None),
                est_monthly_income_hh=float(r["est_monthly_income_hh"]),
                est_monthly_spend_hh=(float(r["est_monthly_spend_hh"])
                                      if r.get("est_monthly_spend_hh") not in (None, "", "None") else None),
                updated_at=datetime.now(timezone.utc),
            )
            stmt = _upsert_stmt(
                "pincodes", values, conflict_cols=["pincode"],
                update_cols=["name", "lat", "lng", "ppi_ml", "ppi_original",
                             "est_monthly_income_hh", "est_monthly_spend_hh", "updated_at"],
            )
            conn.execute(stmt)
            n += 1
    return n


def _parse_ts(ts):
    if isinstance(ts, datetime):
        return ts
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def insert_log(ts, pincode, name, lat, lng, source, ppi, income):
    """Append one enrichment event. Silently no-ops if DB isn't configured."""
    engine = _get_engine()
    if engine is None:
        return
    values = dict(
        ts=_parse_ts(ts), pincode=str(pincode), name=name,
        lat=(float(lat) if lat not in (None, "", "None") else None),
        lng=(float(lng) if lng not in (None, "", "None") else None),
        source=source,
        ppi=(int(float(ppi)) if ppi not in (None, "", "None") else None),
        income=(float(str(income).replace(",", "")) if income not in (None, "", "None") else None),
    )
    stmt = _upsert_stmt("enrichment_log", values,
                         conflict_cols=["ts", "pincode", "source"], update_cols=None)
    with engine.begin() as conn:
        conn.execute(stmt)


def bulk_insert_logs(rows):
    """rows: iterable of dicts with keys ts, pincode, name, lat, lng, source, ppi, income."""
    engine = _get_engine()
    if engine is None or not rows:
        return 0
    n = 0
    with engine.begin() as conn:
        for r in rows:
            try:
                values = dict(
                    ts=_parse_ts(r["ts"]), pincode=str(r["pincode"]), name=r.get("name"),
                    lat=(float(r["lat"]) if r.get("lat") not in (None, "", "None") else None),
                    lng=(float(r["lng"]) if r.get("lng") not in (None, "", "None") else None),
                    source=r["source"],
                    ppi=(int(float(r["ppi"])) if r.get("ppi") not in (None, "", "None") else None),
                    income=(float(str(r["income"]).replace(",", ""))
                            if r.get("income") not in (None, "", "None") else None),
                )
            except (ValueError, KeyError):
                continue
            stmt = _upsert_stmt("enrichment_log", values,
                                 conflict_cols=["ts", "pincode", "source"], update_cols=None)
            conn.execute(stmt)
            n += 1
    return n


def counts():
    """Row counts for both tables — used by the /api/db_status health check."""
    engine = _get_engine()
    if engine is None:
        return None
    from sqlalchemy import text
    with engine.connect() as conn:
        p = conn.execute(text("SELECT COUNT(*) FROM pincodes")).scalar()
        l = conn.execute(text("SELECT COUNT(*) FROM enrichment_log")).scalar()
    return {"pincodes": p, "enrichment_log": l}
