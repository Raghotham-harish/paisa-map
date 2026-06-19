#!/usr/bin/env python3
"""
batch_enrich_hces.py — Pre-enrich all 628 HCES districts without Overpass.

Strategy (fast path — ~20 min for 628 districts):
  1. Geocode each district via Nominatim → lat, lng, pincode
  2. Skip districts already in ppi_map_data.csv
  3. Compute PPI using IDW from nearest existing ML pincodes (same as
     enrich_single.py's interpolation step)
  4. Apply MPCE adjustment: districts with higher-than-state-median MPCE
     get PPI nudged upward (dampened, exponent 0.35)
  5. Write directly to ppi_ml_refined.csv + ppi_map_data.csv

No Overpass queries → safe to run with many districts.
Nominatim: 1 req/s, polite User-Agent required.

Usage:
  python3 etl/batch_enrich_hces.py                    # all unprocessed districts
  python3 etl/batch_enrich_hces.py --limit 50         # process up to 50 today
  python3 etl/batch_enrich_hces.py --state KARNATAKA  # one state only
  python3 etl/batch_enrich_hces.py --dry-run          # geocode only, don't write
"""

import argparse
import csv
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW  = ROOT / "data" / "raw"
OUT  = ROOT / "data" / "output"
APP  = ROOT.parent / "data" / "output"

BATCH_LOG = OUT / "batch_enrich_log.csv"
BATCH_LOG_FIELDS = ["timestamp", "district", "state", "pincode", "lat", "lng",
                    "ppi", "mpce_combined", "mpce_adj_factor", "status", "note"]

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "PaisaMap-BatchEnrich/1.0 (paisamap.cooterlabs.com)"

# Min seconds between Nominatim requests (rate limit = 1/s)
NOMINATIM_DELAY = 1.1


def geocode_district(district: str, state: str):
    """
    Nominatim forward geocode for an Indian district.
    Returns dict with lat, lng, pincode (may be None) or None on failure.
    """
    # Normalise state names for Nominatim (some HCES state names are Census abbreviations)
    STATE_NOMINATIM = {
        "JAMMU AND KASHMIR": "Jammu and Kashmir",
        "ANDAMAN &NICOBAR": "Andaman and Nicobar Islands",
        "DAMAN  & DIU": "Daman and Diu",
        "DADRA & NAGAR HAVELI": "Dadra and Nagar Haveli",
        "NCT DELHI": "Delhi",
    }
    state_q = STATE_NOMINATIM.get(state, state.title())
    district_q = district.title()

    # Try with full district name first, then just city name
    # Avoid "Chandigarh District District, ..." when name already ends in District
    suffix = "" if district_q.lower().endswith("district") else " District"
    for query in [f"{district_q}{suffix}, {state_q}, India",
                  f"{district_q}, {state_q}, India"]:
        params = urllib.parse.urlencode({
            "q": query, "format": "json", "limit": 1,
            "addressdetails": 1, "countrycodes": "in",
        })
        req = urllib.request.Request(
            f"{NOMINATIM}?{params}",
            headers={"User-Agent": UA, "Accept-Language": "en"}
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    results = json.loads(r.read())
                time.sleep(NOMINATIM_DELAY)
                if results:
                    r0 = results[0]
                    addr = r0.get("address", {})
                    pincode = addr.get("postcode", "").replace(" ", "").strip()
                    if pincode and (len(pincode) != 6 or not pincode.isdigit()):
                        pincode = ""
                    return {
                        "lat": float(r0["lat"]),
                        "lng": float(r0["lon"]),
                        "pincode": pincode,
                        "display": r0.get("display_name", ""),
                    }
                break  # empty results — try next query form
            except Exception as e:
                wait = NOMINATIM_DELAY * (3 ** attempt)
                print(f"    WARN geocode attempt {attempt+1} for '{query}': {e} — retry in {wait:.0f}s")
                time.sleep(wait)

    return None


def reverse_pincode(lat: float, lng: float) -> str:
    """Nominatim reverse geocode → postal code."""
    params = urllib.parse.urlencode({
        "lat": lat, "lon": lng, "format": "json",
        "addressdetails": 1, "zoom": 15,
    })
    req = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/reverse?{params}",
        headers={"User-Agent": UA, "Accept-Language": "en"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            addr = json.loads(r.read()).get("address", {})
        time.sleep(NOMINATIM_DELAY)
        pc = addr.get("postcode", "").replace(" ", "").strip()
        if pc and len(pc) == 6 and pc.isdigit():
            return pc
    except Exception as e:
        print(f"    WARN reverse geocode failed: {e}")
        time.sleep(NOMINATIM_DELAY)
    return ""


def haversine_series(lat: float, lng: float, lats: pd.Series, lngs: pd.Series) -> pd.Series:
    R = 6371.0
    p = math.pi / 180
    dlat = (lats - lat) * p
    dlng = (lngs - lng) * p
    a = (dlat / 2).apply(lambda x: math.sin(x) ** 2) + \
        math.cos(lat * p) * (lats * p).apply(math.cos) * \
        (dlng / 2).apply(lambda x: math.sin(x) ** 2)
    return 2 * R * a.apply(lambda x: math.asin(math.sqrt(max(0, x))))


def interpolate_ppi(lat: float, lng: float, ml_df: pd.DataFrame,
                    state_prefix: str = "") -> tuple[float, float, float]:
    """
    IDW interpolation from k=5 nearest ML pincodes.
    Same logic as enrich_single.py.
    Returns (ppi, monthly_income, monthly_spend).
    """
    # Prefer same-state pincodes
    def _prefix(pc):
        return str(pc)[:2]

    if state_prefix:
        same_state = ml_df[[_prefix(pc) == state_prefix for pc in ml_df.index]]
        pool = same_state if len(same_state) >= 3 else ml_df
    else:
        pool = ml_df

    dists = haversine_series(lat, lng, pool["lat"], pool["lng"])
    k = min(5, len(pool))
    nearest = dists.nsmallest(k)
    inv_w = 1.0 / nearest.clip(lower=0.5)
    wsum = inv_w.sum()

    ppi    = float((pool.loc[nearest.index, "ppi_ml"] * inv_w).sum() / wsum)
    income = float((pool.loc[nearest.index, "est_monthly_income_hh"] * inv_w).sum() / wsum)
    spend  = float((pool.loc[nearest.index, "est_monthly_spend_hh"] * inv_w).sum() / wsum)
    return ppi, income, spend


def mpce_adj_factor(mpce: float, state_median: float) -> float:
    """
    Adjust PPI based on MPCE relative to state median.
    Dampened: ratio^0.35 so a 2× MPCE district only gets ~1.27× PPI boost.
    """
    if state_median <= 0 or mpce <= 0:
        return 1.0
    ratio = mpce / state_median
    return ratio ** 0.35


def load_already_done() -> set:
    """Pincodes and districts already in ppi_ml_refined.csv + batch_enrich_log."""
    done_pincodes = set()
    ml_path = OUT / "ppi_ml_refined.csv"
    if ml_path.exists():
        df = pd.read_csv(ml_path, dtype={"pincode": str})
        done_pincodes = set(df["pincode"].dropna())

    # Also check batch log for districts that were skipped/failed previously
    done_districts = set()
    if BATCH_LOG.exists():
        with open(BATCH_LOG, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "ok":
                    done_districts.add((row["state"], row["district"]))
                    if row.get("pincode"):
                        done_pincodes.add(row["pincode"])

    return done_pincodes, done_districts


def append_batch_log(row: dict):
    need_header = not BATCH_LOG.exists() or BATCH_LOG.stat().st_size == 0
    OUT.mkdir(parents=True, exist_ok=True)
    with open(BATCH_LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BATCH_LOG_FIELDS, extrasaction="ignore")
        if need_header:
            w.writeheader()
        w.writerow(row)


def write_to_app(ml_df: pd.DataFrame):
    """Write ppi_ml_refined.csv and sync ppi_map_data.csv."""
    ml_df.sort_values("ppi_ml", ascending=False).to_csv(OUT / "ppi_ml_refined.csv")

    names_path  = RAW / "pincode_names.csv"
    coords_path = RAW / "pincode_coords.csv"
    names_all   = pd.read_csv(names_path,  dtype={"pincode": str}).set_index("pincode") \
                  if names_path.exists() else pd.DataFrame()
    coords_all  = pd.read_csv(coords_path, dtype={"pincode": str}).set_index("pincode") \
                  if coords_path.exists() else pd.DataFrame()

    combined = pd.DataFrame({
        "name":   names_all["name"].reindex(ml_df.index).combine_first(ml_df.get("name", pd.Series(dtype=str))),
        "lat":    coords_all["lat"].reindex(ml_df.index).combine_first(ml_df.get("lat", pd.Series(dtype=float))),
        "lng":    coords_all["lng"].reindex(ml_df.index).combine_first(ml_df.get("lng", pd.Series(dtype=float))),
        "ppi":    ml_df["ppi_ml"],
        "income": ml_df["est_monthly_income_hh"],
    })
    combined.index.name = "pincode"
    combined.sort_values("ppi", ascending=False).to_csv(OUT / "ppi_map_data.csv")

    # Sync to app data dir
    APP.mkdir(parents=True, exist_ok=True)
    combined.sort_values("ppi", ascending=False).to_csv(APP / "ppi_map_data.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit",   type=int, default=0,  help="Max districts to process (0=all)")
    ap.add_argument("--state",   default="",            help="Filter to one state (uppercase)")
    ap.add_argument("--dry-run", action="store_true",   help="Geocode only, don't write")
    args = ap.parse_args()

    print(f"\nBatch HCES district pre-enrich — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Load reference data
    hces   = pd.read_csv(RAW / "hces_mpce.csv")
    ml_df  = pd.read_csv(OUT / "ppi_ml_refined.csv", dtype={"pincode": str}).set_index("pincode")

    # State median MPCE for adjustment factors
    state_medians = hces.groupby("state")["mpce_combined"].median().to_dict()

    done_pincodes, done_districts = load_already_done()
    print(f"  Already in ML output: {len(done_pincodes)} pincodes")

    # Filter
    districts = hces.copy()
    if args.state:
        districts = districts[districts["state"].str.upper() == args.state.upper()]
    # Skip already-done districts
    districts = districts[~districts.apply(
        lambda r: (r["state"], r["district"]) in done_districts, axis=1
    )]

    if args.limit:
        districts = districts.head(args.limit)

    total = len(districts)
    print(f"  Districts to process: {total}")
    if total == 0:
        print("  Nothing to do.")
        return

    added = 0
    skipped = 0

    for i, row in enumerate(districts.itertuples(), 1):
        district = row.district
        state    = row.state
        mpce     = float(row.mpce_combined)
        print(f"\n[{i}/{total}] {state} / {district}  (MPCE ₹{mpce:,.0f})")

        # 1. Geocode
        geo = geocode_district(district, state)
        if not geo:
            print(f"  SKIP — geocode failed")
            append_batch_log({"timestamp": datetime.now(timezone.utc).isoformat(),
                              "district": district, "state": state, "pincode": "",
                              "lat": "", "lng": "", "ppi": "", "mpce_combined": mpce,
                              "mpce_adj_factor": "", "status": "geocode_fail", "note": ""})
            skipped += 1
            continue

        lat, lng = geo["lat"], geo["lng"]
        pincode  = geo["pincode"]
        print(f"  Geocoded: {lat:.4f}, {lng:.4f}  pc={pincode or '(none)'}")

        # 2. If no pincode from forward geocode, try reverse
        if not pincode:
            pincode = reverse_pincode(lat, lng)
            if pincode:
                print(f"  Reverse geocode pincode: {pincode}")

        # 3. Generate a synthetic pincode if still none
        if not pincode:
            # Use lat/lng fingerprint as fallback key (won't match any real pincode)
            pincode = f"D{abs(hash(f'{lat:.3f}{lng:.3f}')) % 900000 + 100000}"
            print(f"  Synthetic pincode: {pincode}")

        # 4. Skip if pincode already in ML output
        if pincode in done_pincodes:
            print(f"  SKIP — pincode {pincode} already enriched")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  DRY-RUN: would enrich {pincode}")
            continue

        # 5. Compute PPI via IDW + MPCE adjustment
        prefix = str(pincode)[:2] if pincode.isdigit() else ""
        ppi_raw, income, spend = interpolate_ppi(lat, lng, ml_df, prefix)

        s_median = state_medians.get(state, 6000)
        adj = mpce_adj_factor(mpce, s_median)
        ppi_final = round(ppi_raw * adj)
        income_adj = round(income * adj, -2)
        spend_adj  = round(spend  * adj, -2)

        print(f"  IDW PPI: {ppi_raw:.1f}  MPCE adj: ×{adj:.3f}  → PPI {ppi_final}")

        # 6. Append to ML output
        name = f"{district.title()}, {state.title()}"
        ml_df.loc[pincode] = {
            "name":                  name,
            "lat":                   lat,
            "lng":                   lng,
            "ppi_ml":                ppi_final,
            "ppi_original":          None,
            "est_monthly_income_hh": income_adj,
            "est_monthly_spend_hh":  spend_adj,
        }
        done_pincodes.add(pincode)

        # 7. Also update raw coords/names CSVs so enrich_single finds it
        for fname, col, val in [
            ("pincode_coords.csv", None, {"lat": lat, "lng": lng}),
            ("pincode_names.csv",  None, {"name": name}),
        ]:
            p = RAW / fname
            if p.exists():
                df = pd.read_csv(p, dtype={"pincode": str}).set_index("pincode")
                if pincode not in df.index:
                    if col is None:
                        for k, v in val.items():
                            df.loc[pincode, k] = v
                    else:
                        df.loc[pincode, col] = val
                    df.to_csv(p)

        append_batch_log({
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "district":        district,
            "state":           state,
            "pincode":         pincode,
            "lat":             round(lat, 5),
            "lng":             round(lng, 5),
            "ppi":             ppi_final,
            "mpce_combined":   mpce,
            "mpce_adj_factor": round(adj, 4),
            "status":          "ok",
            "note":            f"idw_raw={ppi_raw:.1f}",
        })
        added += 1

    # Write outputs once at end
    if added > 0 and not args.dry_run:
        write_to_app(ml_df)

    print(f"\n{'DRY-RUN — ' if args.dry_run else ''}Done.")
    print(f"  Added:   {added}")
    print(f"  Skipped: {skipped}")
    print(f"  Total in ML: {len(ml_df)} pincodes")


if __name__ == "__main__":
    main()
