"""
enrich_single.py — On-demand single-pincode enrichment.

Usage:
  python3 etl/enrich_single.py <pincode> <lat> <lng> [<name>]

Adds one new pincode to all raw CSVs (using POI-density + state priors),
then re-runs pipeline.py + ml_refinement.py and copies the updated
ppi_map_data.csv to the app's data directory.

Called by server.py /api/enrich for "You are here" pin enrichment.
"""

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from _filelock import write_lock
import _db

ROOT = Path(__file__).resolve().parents[1]
REF  = ROOT / "data" / "reference"
RAW  = ROOT / "data" / "raw"
OUT  = ROOT / "data" / "output"
APP  = ROOT.parent           # /paisa-map/

STATE_NAMES = {
    "DL": "Delhi",             "MH": "Maharashtra",       "KA": "Karnataka",
    "HR": "Haryana",           "TS": "Telangana",         "AP": "Andhra Pradesh",
    "TN": "Tamil Nadu",        "GJ": "Gujarat",           "WB": "West Bengal",
    "PB": "Punjab",            "RJ": "Rajasthan",         "MP": "Madhya Pradesh",
    "KL": "Kerala",            "UP": "Uttar Pradesh",     "HP": "Himachal Pradesh",
    "CH": "Chandigarh",        "JK": "Jammu & Kashmir",   "CG": "Chhattisgarh",
    "OD": "Odisha",            "AS": "Assam",             "BR": "Bihar",
    "JH": "Jharkhand",         "GA": "Goa",               "MN": "Manipur",
    "ML": "Meghalaya",         "TR": "Tripura",           "NL": "Nagaland",
    "AR": "Arunachal Pradesh", "MZ": "Mizoram",           "SK": "Sikkim",
}

# ── Pincode → state (first two digits of Indian pincode) ─────────────────────
PREFIX_STATE: dict[str, str] = {
    "11": "DL", "12": "HR",    # 11x = Delhi, 12x = Haryana (Gurgaon/Faridabad)
    "13": "HP", "14": "PB", "15": "PB", "16": "CH", "17": "HP",
    "18": "JK", "19": "JK",
    "20": "UP", "21": "UP", "22": "UP", "24": "UP",
    "25": "UP", "26": "UP", "27": "UP", "28": "UP",
    "30": "RJ", "31": "RJ", "32": "RJ", "33": "RJ", "34": "RJ",
    "36": "GJ", "37": "GJ", "38": "GJ", "39": "GJ",
    "40": "MH", "41": "MH", "42": "MH", "43": "MH", "44": "MH",
    "45": "MP", "46": "MP", "47": "MP", "48": "MP", "49": "CG",
    "50": "TS", "51": "AP", "52": "AP", "53": "AP",
    "56": "KA", "57": "KA", "58": "KA", "59": "KA",
    "60": "TN", "61": "TN", "62": "TN", "63": "TN", "64": "TN",
    "67": "KL", "68": "KL", "69": "KL",
    "70": "WB", "71": "WB", "72": "WB", "73": "WB", "74": "WB",
    "75": "OD", "76": "OD", "77": "OD",
    "78": "AS",
    "80": "BR", "81": "BR", "82": "JH", "83": "JH", "84": "BR", "85": "BR",
}

def state_from_pincode(pc: str) -> str:
    return PREFIX_STATE.get(str(pc)[:2], "XX")

# ── City-level priors for estimating signals ──────────────────────────────────
# Recalibrated 2026-06-18 from actual raw signal medians (DL=22, MH=25, KA=23, HR=2 pincodes).
# Data-derived states use actual medians directly.
# Non-data states apply a 60% correction towards DL ratios for rate/dep/itr/nl/c2w/lux;
# ev corrected upward (all three data states showed underestimation); veh kept per-state.
CITY_PRIORS: dict[str, dict] = {
    # ── Data-derived (actual medians from raw CSVs) ───────────────────────────
    "DL": dict(rate=18010, dep=399318, nl=38.0, itr=0.1836, c2w=0.319, lux=0.0424, ev=0.061,  veh=43.2),
    "MH": dict(rate=27000, dep=570000, nl=46.0, itr=0.2465, c2w=0.477, lux=0.1392, ev=0.0429, veh=20.1),
    "KA": dict(rate=12000, dep=320000, nl=36.0, itr=0.1760, c2w=0.358, lux=0.0759, ev=0.0963, veh=74.1),
    "HR": dict(rate=19000, dep=550000, nl=40.0, itr=0.2120, c2w=1.650, lux=0.1250, ev=0.060,  veh=70.0),
    # ── Corrected (60% DL-anchor correction applied to old hardcoded values) ──
    "TS": dict(rate=6684,  dep=231802, nl=33.5, itr=0.1513, c2w=0.298, lux=0.0458, ev=0.0392, veh=38),
    "AP": dict(rate=4876,  dep=179847, nl=27.2, itr=0.1261, c2w=0.269, lux=0.0393, ev=0.0261, veh=32),
    "TN": dict(rate=6920,  dep=239795, nl=30.8, itr=0.1597, c2w=0.227, lux=0.0393, ev=0.0392, veh=36),
    "GJ": dict(rate=5977,  dep=215816, nl=29.9, itr=0.1765, c2w=0.319, lux=0.0524, ev=0.0523, veh=40),
    "WB": dict(rate=5662,  dep=195833, nl=32.6, itr=0.1177, c2w=0.156, lux=0.0327, ev=0.0261, veh=28),
    "PB": dict(rate=6291,  dep=211819, nl=27.2, itr=0.1513, c2w=0.354, lux=0.0458, ev=0.0261, veh=44),
    "RJ": dict(rate=5190,  dep=177449, nl=25.4, itr=0.1177, c2w=0.283, lux=0.0393, ev=0.0261, veh=36),
    "MP": dict(rate=4404,  dep=155068, nl=21.8, itr=0.1093, c2w=0.255, lux=0.0327, ev=0.0261, veh=30),
    "KL": dict(rate=5662,  dep=195034, nl=23.6, itr=0.1681, c2w=0.198, lux=0.0327, ev=0.0392, veh=32),
    "UP": dict(rate=6448,  dep=187840, nl=27.2, itr=0.1429, c2w=0.298, lux=0.0393, ev=0.0392, veh=36),
}

# Fallback if state unknown — national median derived from data states
_DEFAULT_PRIOR = dict(rate=7000, dep=220000, nl=28.0, itr=0.14,
                      c2w=0.32, lux=0.045, ev=0.038, veh=34)

# ── Overpass POI query ────────────────────────────────────────────────────────
OVERPASS = "https://overpass-api.de/api/interpreter"
RADIUS_M = 2000
AREA_KM2 = math.pi * (RADIUS_M / 1000) ** 2

_POI_Q = """[out:json][timeout:25];
(
  nwr["shop"="mall"](around:{r},{lat},{lng});
  nwr["shop"="department_store"](around:{r},{lat},{lng});
  nwr["shop"="supermarket"](around:{r},{lat},{lng});
  nwr["amenity"="bank"](around:{r},{lat},{lng});
  nwr["shop"="jewelry"](around:{r},{lat},{lng});
  nwr["leisure"="fitness_centre"](around:{r},{lat},{lng});
  nwr["amenity"="school"](around:{r},{lat},{lng});
  nwr["amenity"="hospital"](around:{r},{lat},{lng});
  nwr["amenity"="fuel"](around:{r},{lat},{lng});
  nwr["amenity"="pharmacy"](around:{r},{lat},{lng});
);
out count;"""


def fetch_poi(lat: float, lng: float, retries: int = 3):
    for attempt in range(retries):
        try:
            q    = _POI_Q.format(r=RADIUS_M, lat=lat, lng=lng)
            data = urllib.parse.urlencode({"data": q}).encode()
            req  = urllib.request.Request(
                OVERPASS, data=data,
                headers={"User-Agent": "PaisaMap-Enrich/1.0",
                         "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read())
            total = int(j["elements"][0]["tags"]["total"])
            return round(total / AREA_KM2, 1)
        except Exception as e:
            print(f"  WARN Overpass attempt {attempt+1}: {e}", flush=True)
            time.sleep(6 * (attempt + 1))
    return None


def scale_from_poi(poi: float, prior: dict, city_poi_median: float = 15.0) -> dict:
    """Estimate all proxy signals from POI density relative to city prior."""
    # POI density is the best real-time signal; scale others proportionally.
    # Use dampened power scaling (exponent < 1) to avoid wild extrapolation.
    ratio = max(0.15, min(5.0, poi / max(city_poi_median, 0.5)))
    # fin_density scales with POI density (financial inclusion tracks economic activity)
    # National median ~0.04 SFB+COOP+RRB branches/km² from 73-pincode dataset
    fin_national_median = 0.04
    return {
        "rate_per_sqft":       round(prior["rate"]   * ratio ** 0.80),
        "deposits_per_capita": round(prior["dep"]    * ratio ** 0.75),
        "radiance_mean":       round(prior["nl"]     * ratio ** 0.25, 1),
        "filers_per_capita":   round(prior["itr"]    * ratio ** 0.55, 4),
        "cars_per_1000":       round(prior["veh"]    * ratio ** 0.35, 1),
        "car_2w_ratio":        prior["c2w"],
        "luxury_share":        prior["lux"],
        "ev_share":            prior["ev"],
        "premium_poi_per_km2": poi,
        "fin_density_per_km2": round(fin_national_median * ratio ** 0.50, 4),
    }


def detect_district(lat: float, lng: float):
    """Nominatim reverse geocode → (district_name, locality_name). Returns ('','') on failure."""
    try:
        url = (
            f"https://nominatim.openstreetmap.org/reverse"
            f"?lat={lat}&lon={lng}&format=json&addressdetails=1&zoom=10"
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": "PaisaMap-Enrich/1.0", "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=10) as r:
            addr = json.loads(r.read()).get("address", {})
        district = (addr.get("county") or addr.get("state_district") or
                    addr.get("district") or "").strip()
        locality  = (addr.get("city") or addr.get("town") or addr.get("suburb") or district).strip()
        return district, locality
    except Exception as e:
        print(f"  WARN district lookup failed: {e}", flush=True)
        return "", ""


def register_in_district_map(pc: str, name: str, lat: float, lng: float, state: str) -> None:
    """
    Add new pin-dropped pincode to pincode_district_map.csv so the next
    Phase 1 ETL run (VAHAN/RBI) automatically covers this pincode.
    """
    map_file = REF / "pincode_district_map.csv"
    if not map_file.exists():
        return
    df = pd.read_csv(map_file, dtype={"pincode": str}).set_index("pincode")
    if pc in df.index:
        return

    time.sleep(1.2)  # Nominatim 1 req/s
    district, locality = detect_district(lat, lng)
    if not district:
        print(f"  WARN could not detect district for {pc} — skipping district map", flush=True)
        return

    # Pull district population from VAHAN reference if available
    pop_lakh = 10.0
    try:
        vahan = pd.read_csv(REF / "vahan_district_2024.csv")
        kw = district.lower().split()[0]
        match = vahan[vahan["district"].str.lower().str.contains(kw, na=False)]
        if not match.empty and "district_pop_lakh" in vahan.columns:
            pop_lakh = float(match.iloc[0]["district_pop_lakh"])
    except Exception:
        pass

    df.loc[pc] = {
        "name":             name or locality or pc,
        "district":         district,
        "state_code":       state,
        "state_name":       STATE_NAMES.get(state, state),
        "district_pop_lakh": pop_lakh,
    }
    df.to_csv(map_file)
    print(f"  Registered {pc} → {district}, {STATE_NAMES.get(state, state)}", flush=True)


def main():
    if len(sys.argv) < 4:
        print("Usage: python3 enrich_single.py <pincode> <lat> <lng> [<name>]")
        sys.exit(1)

    pc   = sys.argv[1].strip()
    lat  = float(sys.argv[2])
    lng  = float(sys.argv[3])
    name = sys.argv[4].strip() if len(sys.argv) > 4 else pc

    print(f"\n=== Enriching {pc} — {name} ({lat:.4f}, {lng:.4f}) ===\n")

    # ── Guard: already fully enriched (in ML output)? ────────────────────────
    coords = pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str})
    already_in_raw = pc in set(coords["pincode"])
    ppi_path = OUT / "ppi_ml_refined.csv"
    if already_in_raw and ppi_path.exists():
        ml_df = pd.read_csv(ppi_path, dtype={"pincode": str}).set_index("pincode")
        if pc in ml_df.index:
            row = ml_df.loc[pc]
            print(f"  {pc} already in ML output — nothing to do")
            print(f"\n  PPI (ML): {int(row['ppi_ml'])}  income: ₹{int(row['est_monthly_income_hh']):,}/mo")
            sys.exit(0)
    if already_in_raw:
        print(f"  {pc} in raw CSVs but not in ML output — skipping CSV append, re-running pipeline")

    # ── Determine state ───────────────────────────────────────────────────────
    state  = state_from_pincode(pc)
    prior  = CITY_PRIORS.get(state, _DEFAULT_PRIOR)
    if not already_in_raw:
        print(f"  State: {state}  prior: ₹{prior['rate']:,}/sqft")

    # ── Fetch POI density (our best live signal) ──────────────────────────────
    # already_in_raw is only checked against pincode_coords.csv (above), so a
    # pincode can be "already in raw" there while missing from poi_density.csv
    # specifically — e.g. a pre-existing row lost to an unlocked concurrent
    # write before write_lock() existed. Don't assume the other file has it too.
    poi_in_density = False
    if already_in_raw:
        poi_density_df = pd.read_csv(RAW / "poi_density.csv", dtype={"pincode": str}).set_index("pincode")
        poi_in_density = pc in poi_density_df.index

    if not (already_in_raw and poi_in_density):
        if already_in_raw:
            print(f"  WARN: {pc} in raw CSVs but missing from poi_density.csv — re-fetching POI", flush=True)
        else:
            print(f"  Querying Overpass POI density…", flush=True)
    poi = fetch_poi(lat, lng) if not (already_in_raw and poi_in_density) else None

    # Fallback / skip when already_in_raw
    if already_in_raw and poi_in_density:
        poi = poi_density_df.at[pc, "premium_poi_per_km2"]
    elif poi is None:
        poi = round(prior["nl"] * 0.38, 1)
        print(f"  WARN: Overpass failed — using estimated POI={poi}")
    else:
        print(f"  POI density: {poi} poi/km²")

    # ── Compute city POI median from existing data ────────────────────────────
    poi_df = pd.read_csv(RAW / "poi_density.csv", dtype={"pincode": str})
    city_pcs = [p for p in poi_df["pincode"] if PREFIX_STATE.get(str(p)[:2]) == state]
    city_poi_med = (poi_df.set_index("pincode")
                    .reindex(city_pcs)["premium_poi_per_km2"]
                    .dropna().median())
    if pd.isna(city_poi_med) or city_poi_med < 1:
        city_poi_med = 15.0
    if not already_in_raw:
        print(f"  City POI median: {city_poi_med:.1f} poi/km²")

    # ── Scale all signals ─────────────────────────────────────────────────────
    signals = scale_from_poi(poi, prior, city_poi_med)
    if not already_in_raw:
        print(f"  Estimated rate: ₹{signals['rate_per_sqft']:,}/sqft  "
              f"ITR: {signals['filers_per_capita']:.3f}  "
              f"cars: {signals['cars_per_1000']:.1f}/1k")

    # ── Append to all raw CSVs (only if not already present) ─────────────────
    def _append(fname, col, val):
        df = pd.read_csv(RAW / fname, dtype={"pincode": str}).set_index("pincode")
        if pc not in df.index:
            df.loc[pc, col] = val
            df.to_csv(RAW / fname)
        return df

    # Everything below reads-then-writes the shared raw/output CSVs. Hold one
    # cross-process lock for the whole cycle — server.py can have up to ~15
    # of these subprocesses running concurrently from a single pin-drop
    # (bulkEnrichNearby + prefetchHexNeighbors), plus the nightly cron. A
    # lock only around individual to_csv() calls wouldn't help: a process
    # that already read a stale snapshot before acquiring the lock would
    # still write that stale snapshot back and erase whatever the other
    # process just committed. So lock first, then read.
    with write_lock():
        print("\n  Updating raw CSVs…")

        coords_df = pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str}).set_index("pincode")
        coords_df.loc[pc] = {"lat": lat, "lng": lng}
        coords_df.to_csv(RAW / "pincode_coords.csv")

        names_df = (pd.read_csv(RAW / "pincode_names.csv", dtype={"pincode": str}).set_index("pincode")
                    if (RAW / "pincode_names.csv").exists() else pd.DataFrame())
        names_df.loc[pc] = {"name": name}
        names_df.to_csv(RAW / "pincode_names.csv")

        _append("property_rates.csv",      "rate_per_sqft",         signals["rate_per_sqft"])
        _append("bank_deposits.csv",       "deposits_per_capita",   signals["deposits_per_capita"])
        _append("nightlights.csv",         "radiance_mean",         signals["radiance_mean"])
        _append("poi_density.csv",         "premium_poi_per_km2",   signals["premium_poi_per_km2"])
        _append("itr_filers.csv",          "filers_per_capita",     signals["filers_per_capita"])
        _append("vehicle_density.csv",     "cars_per_1000",         signals["cars_per_1000"])
        if (RAW / "financial_inclusion.csv").exists():
            _append("financial_inclusion.csv", "fin_density_per_km2",   signals["fin_density_per_km2"])

        # rto_enhanced — 4 columns
        rto_df = pd.read_csv(RAW / "rto_enhanced.csv", dtype={"pincode": str}).set_index("pincode")
        if pc not in rto_df.index:
            rto_df.loc[pc, "lmv_per_1000"]  = signals["cars_per_1000"]
            rto_df.loc[pc, "car_2w_ratio"]  = signals["car_2w_ratio"]
            rto_df.loc[pc, "luxury_share"]  = signals["luxury_share"]
            rto_df.loc[pc, "ev_share"]      = signals["ev_share"]
            rto_df.to_csv(RAW / "rto_enhanced.csv")

        n_total = len(pd.read_csv(RAW / "pincode_coords.csv"))
        print(f"  Dataset: {n_total} pincodes")

        # Auto-register in district map so Phase 1 ETL picks up this pincode next run
        if not already_in_raw:
            register_in_district_map(pc, name, lat, lng, state)

        # ── Spatial interpolation from the stable ML baseline ─────────────────
        # We do NOT re-train the ML model (that would destabilise all 72 pincodes).
        # Instead we use inverse-distance weighting over the 5 nearest trusted
        # pincodes to estimate PPI and income for this new pincode.
        print("\n  Computing PPI via spatial interpolation from ML baseline…")
        ml_df = pd.read_csv(OUT / "ppi_ml_refined.csv", dtype={"pincode": str}).set_index("pincode")

        def haversine(lat1, lng1, lat2s, lng2s):
            R = 6371.0
            p = math.pi / 180
            dlat = (lat2s - lat1) * p
            dlng = (lng2s - lng1) * p
            a = (dlat / 2).apply(lambda x: math.sin(x) ** 2) + \
                math.cos(lat1 * p) * \
                (lat2s * p).apply(math.cos) * \
                (dlng / 2).apply(lambda x: math.sin(x) ** 2)
            return (2 * R * a.apply(lambda x: math.asin(math.sqrt(max(0, x))))).round(3)

        dists = haversine(lat, lng, ml_df["lat"], ml_df["lng"])

        # Use same-state neighbours preferentially; fall back to global if too few
        same_state = ml_df[[state_from_pincode(idx) == state for idx in ml_df.index]]
        pool = same_state if len(same_state) >= 3 else ml_df
        pool_dists = dists.reindex(pool.index)

        k = min(5, len(pool))
        nearest = pool_dists.nsmallest(k)
        inv_w = 1.0 / nearest.clip(lower=0.5)   # cap minimum distance at 0.5 km
        ppi_ml_new    = round(float((pool.loc[nearest.index, "ppi_ml"] * inv_w).sum() / inv_w.sum()))
        income_ml_new = round(float((pool.loc[nearest.index, "est_monthly_income_hh"] * inv_w).sum() / inv_w.sum()), -2)
        spend_ml_new  = round(float((pool.loc[nearest.index, "est_monthly_spend_hh"] * inv_w).sum() / inv_w.sum()), -2)

        nearest_names = pool.loc[nearest.index, "name"].tolist()
        print(f"  Nearest: {nearest_names}")
        print(f"  Interpolated PPI: {ppi_ml_new}  income: ₹{int(income_ml_new):,}/mo")

        # Append single row to the stable ML output (no re-train)
        ml_df.loc[pc] = {
            "name":                  name,
            "lat":                   lat,
            "lng":                   lng,
            "ppi_ml":                ppi_ml_new,
            "ppi_original":          None,
            "est_monthly_income_hh": income_ml_new,
            "est_monthly_spend_hh":  spend_ml_new,
        }
        ml_df.sort_values("ppi_ml", ascending=False).to_csv(OUT / "ppi_ml_refined.csv")

        # Dual-write to the database (no-op unless DATABASE_URL is set — see _db.py).
        # CSV stays the source of truth until the DB has been proven in production.
        try:
            _db.upsert_pincode(pc, name, lat, lng, ppi_ml_new, None,
                                income_ml_new, spend_ml_new)
        except Exception as e:
            print(f"  WARN: DB dual-write failed (CSV write already succeeded): {e}", flush=True)

        # ── Copy output to app directory ───────────────────────────────────────
        print("  Updating app data…")
        names_all  = pd.read_csv(RAW / "pincode_names.csv",   dtype={"pincode": str}).set_index("pincode")
        coords_all = pd.read_csv(RAW / "pincode_coords.csv",  dtype={"pincode": str}).set_index("pincode")

        app_out = APP / "data" / "output"
        app_out.mkdir(parents=True, exist_ok=True)

        combined = pd.DataFrame({
            "name":   names_all["name"].reindex(ml_df.index).combine_first(ml_df.get("name")),
            "lat":    coords_all["lat"].reindex(ml_df.index).combine_first(ml_df.get("lat")),
            "lng":    coords_all["lng"].reindex(ml_df.index).combine_first(ml_df.get("lng")),
            "ppi":    ml_df["ppi_ml"],
            "income": ml_df["est_monthly_income_hh"],
        })
        combined.index.name = "pincode"
        combined.sort_values("ppi", ascending=False).to_csv(app_out / "ppi_map_data.csv")

    # ── Print result ──────────────────────────────────────────────────────────
    if pc in ml_df.index:
        ppi    = int(ml_df.loc[pc, "ppi_ml"])
        income = int(ml_df.loc[pc, "est_monthly_income_hh"])
        rank   = int((ml_df["ppi_ml"] >= ppi).sum())
        print(f"\n  ✓ {name} ({pc})")
        print(f"    PPI (ML): {ppi}   rank #{rank}/{len(ml_df)}")
        print(f"    Est. household income: ₹{income:,}/mo")
    else:
        print(f"\n  ✓ Enrichment complete — {pc} added to dataset")


if __name__ == "__main__":
    main()
