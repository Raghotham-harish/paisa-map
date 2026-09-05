"""
_signals_data.py — shared pincode+signal data loading, extracted from server.py so
blueprints/intelligence.py can reuse the exact same join /api/export already relies
on without a circular import (a blueprint can't import back from the server.py that
registers it). server.py's /api/export now imports from here too — this is a pure
refactor of existing logic, not new behavior.

Also adds two new read-only loaders for Phase 2's scoring/benchmark work:
load_geography() (pincode -> district/state, for benchmarking against a state or
district average) and load_diagnostics() (the ML ensemble's precomputed per-pincode
anomaly flags + global feature importances, for "explain this score" — real material
that already exists in paisamap-etl/data/output/ml_diagnostics.json but nothing in
the API layer surfaced before Phase 2).
"""

import csv
import json
import math
from pathlib import Path

# Guarded exactly like server.py's own original import of this module — _db.py
# only imports sqlalchemy lazily inside functions, so this should never actually
# raise, but insulating the rest of this module (coerce/haversine_km/etc., which
# have nothing to do with the DB) from a hypothetical _db import failure is free.
try:
    import _db  # sibling module in the same etl/ directory
except ImportError:
    _db = None

ETL_ROOT  = Path(__file__).resolve().parent.parent
ETL_RAW   = ETL_ROOT / "data" / "raw"
ETL_OUT   = ETL_ROOT / "data" / "output"
REFERENCE = ETL_ROOT / "data" / "reference"

GEOGRAPHY_CSV     = REFERENCE / "pincode_district_state_india.csv"
DIAGNOSTICS_JSON  = ETL_OUT / "ml_diagnostics.json"

# ── Export: PPI/income/spend joined with every pincode-level raw signal ────────
EXPORT_CORE_FIELDS = ["pincode", "name", "lat", "lng", "ppi_ml", "ppi_original",
                       "est_monthly_income_hh", "est_monthly_spend_hh"]
EXPORT_SIGNAL_FILES = [
    ("property_rates.csv",      ["rate_per_sqft"]),
    ("bank_deposits.csv",       ["bank_branches_per_lakh", "deposits_per_capita"]),
    ("financial_inclusion.csv", ["sfb_branches", "coop_branches", "rrb_branches",
                                  "fin_branches_total", "fin_density_per_km2"]),
    ("itr_filers.csv",          ["filers_per_capita"]),
    ("nightlights.csv",         ["radiance_mean"]),
    ("poi_density.csv",         ["premium_poi_per_km2"]),
    ("rto_enhanced.csv",        ["lmv_per_1000", "car_2w_ratio", "luxury_share", "ev_share"]),
    ("vehicle_density.csv",     ["cars_per_1000"]),
    ("upi_activity.csv",        ["upi_txn_value_per_capita"]),
    ("education.csv",           ["schools_per_lakh"]),
    ("commercial.csv",          ["msme_per_lakh"]),
    ("agriculture.csv",         ["cropping_intensity_pct"]),
    ("industrial.csv",          ["factories_per_lakh"]),
    ("economic.csv",            ["nsdp_per_capita"]),
]
EXPORT_ALL_COLUMNS = EXPORT_CORE_FIELDS + [c for _, cols in EXPORT_SIGNAL_FILES for c in cols]

# Human-readable labels — copied verbatim from index.html's signal switcher (the
# map's own JS, not reachable from Python or from the separate workspace/ React
# app) so anything the backend surfaces about a signal uses the same wording a
# user already sees on the map, rather than a second, drifting set of names.
SIGNAL_LABELS = {
    "ppi_ml": "Purchasing Power Index (PPI)",
    "est_monthly_income_hh": "Avg income",
    "est_monthly_spend_hh": "Avg spend",
    "rate_per_sqft": "Property rate /sqft",
    "bank_branches_per_lakh": "Bank branches /lakh",
    "deposits_per_capita": "Deposits per capita",
    "sfb_branches": "SFB branches",
    "coop_branches": "Co-op branches",
    "rrb_branches": "RRB branches",
    "fin_branches_total": "Total fin. branches",
    "fin_density_per_km2": "Fin. density /km²",
    "upi_txn_value_per_capita": "UPI txn value /capita",
    "filers_per_capita": "ITR filers /capita",
    "msme_per_lakh": "MSMEs /lakh",
    "factories_per_lakh": "Factories /lakh",
    "nsdp_per_capita": "NSDP per capita",
    "cropping_intensity_pct": "Cropping intensity",
    "radiance_mean": "Night-lights radiance",
    "premium_poi_per_km2": "Premium POI density",
    "schools_per_lakh": "Schools /lakh",
    "cars_per_1000": "Cars /1000",
    "lmv_per_1000": "LMVs /1000",
    "car_2w_ratio": "Car:2W ratio",
    "luxury_share": "Luxury vehicle share",
    "ev_share": "EV share",
}


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p = math.pi / 180
    dlat, dlng = (lat2 - lat1) * p, (lng2 - lng1) * p
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin(dlng / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(max(0, a)))


def coerce(v):
    """CSV values are always strings — turn numeric-looking ones back into numbers for JSON/XLSX.

    NaN/Infinity must become None here, not pass through as a float: a DB float column
    (e.g. ppi_original, unset for most rows) can come back as an actual NaN rather than
    None, and Python's json.dumps happily emits that as a bare `NaN` token by default —
    which isn't valid JSON. Browsers' strict JSON.parse throws on it, which silently
    killed the *entire* /api/export payload for every caller (confirmed live: every
    signal outside the "Nationwide coverage" group — bank branches, property rate, ITR
    filers, MSMEs, factories, NSDP, cropping intensity, night-lights, POI density, every
    vehicle signal — read 0% coverage on the map despite being fully populated server-side,
    because loadSignalData()'s res.json() was failing and getting swallowed on every load)."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if math.isnan(f) or math.isinf(f):
        return None
    return int(f) if f.is_integer() else f


def load_ppi_signals_rows():
    """Core PPI/income/spend (DB if configured, else ppi_ml_refined.csv) joined
    with every pincode-level raw signal file (still CSV-only — out of scope
    for the DB migration's first pass). Returns (rows_dict, source_label)."""
    rows = {}
    source = "csv"
    db_rows = _db.fetch_pincodes() if (_db is not None and _db.enabled()) else None
    if db_rows is not None:
        source = "database"
        for r in db_rows:
            pc = r.get("pincode")
            if pc:
                rows[pc] = {k: r.get(k, "") for k in EXPORT_CORE_FIELDS}
    else:
        core_path = ETL_OUT / "ppi_ml_refined.csv"
        if core_path.exists():
            with open(core_path, newline="") as f:
                for r in csv.DictReader(f):
                    pc = r.get("pincode")
                    if pc:
                        rows[pc] = {k: r.get(k, "") for k in EXPORT_CORE_FIELDS}

    for fname, cols in EXPORT_SIGNAL_FILES:
        fpath = ETL_RAW / fname
        if not fpath.exists():
            continue
        with open(fpath, newline="") as f:
            for r in csv.DictReader(f):
                pc = r.get("pincode")
                if pc not in rows:
                    continue
                for c in cols:
                    rows[pc][c] = r.get(c, "")
    return rows, source


_geography_cache = None


def load_geography():
    """pincode -> {district, state_name, state_code}, cached in memory after first
    read. Same source file ml_refinement.py itself uses for district grouping, so
    a "benchmark vs your state/district" feature agrees with what the model already
    considers a location's peer group."""
    global _geography_cache
    if _geography_cache is not None:
        return _geography_cache
    geo = {}
    if GEOGRAPHY_CSV.exists():
        with open(GEOGRAPHY_CSV, newline="") as f:
            for r in csv.DictReader(f):
                pc = r.get("pincode")
                if pc:
                    geo[pc] = {
                        "district": r.get("district") or None,
                        "state_name": r.get("state_name") or None,
                        "state_code": r.get("state_code") or None,
                    }
    _geography_cache = geo
    return geo


_diagnostics_cache = None


def load_diagnostics():
    """The ML ensemble's precomputed diagnostics (model_a.feature_importance,
    anomalies keyed by pincode) — written once per weekly refit
    (paisamap-etl/data/output/ml_diagnostics.json), read-only here. Returns None if
    the file doesn't exist (e.g. a fresh checkout before any refit has run)."""
    global _diagnostics_cache
    if _diagnostics_cache is not None:
        return _diagnostics_cache
    if not DIAGNOSTICS_JSON.exists():
        return None
    with open(DIAGNOSTICS_JSON) as f:
        _diagnostics_cache = json.load(f)
    return _diagnostics_cache
