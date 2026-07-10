"""
expand_pincodes_mh_tier2.py — Expand PaisaMap into Maharashtra tier-2/3 cities.

Adds 24 pincodes (+3 each) in Pune, Nagpur, Nashik, Chhatrapati Sambhajinagar
(Aurangabad), Kolhapur, Solapur, Amravati and Nanded — so the Maharashtra
side of the map isn't just Mumbai/Thane anymore.

Data sources, by column:
  bank_branches_per_lakh : REAL. Derived from data/raw/rbi_branch_counts_mh.csv
                            (parsed from the RBI branch master — see
                            etl/parse_rbi_branch_master.py) — actual public-
                            sector-bank branch counts per pincode, scaled to a
                            district anchor using Census 2011 district
                            population and a documented PSU-market-share
                            assumption (see DISTRICT_ANCHORS below) so the
                            figure sits on the same "all scheduled commercial
                            banks" basis as the existing Delhi/Mumbai/
                            Bengaluru anchors in rbi_bsr_district_2023.csv.
  rate_per_sqft, deposits_per_capita, radiance_mean,
  cars_per_1000 / car_2w_ratio / luxury_share / ev_share :
                            ESTIMATED. No live source for these at pincode
                            level for these cities (no RTO cache coverage,
                            no property-portal API). Figures below are
                            order-of-magnitude estimates reflecting known
                            regional tiers (Pune > Nagpur/Nashik > Aurangabad/
                            Kolhapur > Solapur/Amravati/Nanded), the same
                            "documented estimate, not live-fetched" practice
                            already used throughout this pipeline (see
                            expand_pincodes.py). Treat these two proxy
                            columns as lower-confidence than bank_branches.
  premium_poi_per_km2    : REAL — live Overpass query, same as everywhere else.
  filers_per_capita      : Derived via the same metro-rate formula as
                            fetch_itr.py / expand_pincodes.py, anchored off
                            the existing Mumbai property-rate median.

Usage:
  python3 etl/expand_pincodes_mh_tier2.py
"""

import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW  = ROOT / "data" / "raw"

# ── Census 2011 district population (lakh) — same convention as
#    pincode_district_map.csv's district_pop_lakh column. ───────────────────
DISTRICT_POP_LAKH = {
    "PUNE":                      94.29,
    "NAGPUR":                    46.54,
    "NASIK":                     61.07,
    "CHHATRAPATI SAMBHAJINAGAR": 37.01,   # Aurangabad district, renamed 2023
    "KOLHAPUR":                  38.76,
    "SOLAPUR":                   43.18,
    "AMRAVATI":                  28.88,
    "NANDED":                    33.57,
}

# Documented estimate of PSU banks' share of ALL scheduled-commercial-bank
# branches in each city tier (private banks concentrate far more heavily in
# metros; PSU banks retain much stronger relative presence in tier-2/3 towns
# per RBI financial-inclusion branch-expansion mandates). Not independently
# verified per-district — used only to put the real PSU branch count on the
# same "all banks" basis as the pre-existing district anchors.
PSU_SHARE = {
    "PUNE": 0.45, "NAGPUR": 0.55, "NASIK": 0.55,
    "CHHATRAPATI SAMBHAJINAGAR": 0.60, "KOLHAPUR": 0.60,
    "SOLAPUR": 0.68, "AMRAVATI": 0.68, "NANDED": 0.68,
}

NEW_PINCODES = [
    # ── Pune (924 real PSU branches / district) ───────────────────────────
    dict(pc="411001", name="Pune Camp / GPO",        lat=18.5196, lng=73.8553,
         district="PUNE", real_branches=44, rate=11000, dep=380000, nl=42.0,
         veh=32, c2w=0.42, lux=0.050, ev=0.045),
    dict(pc="411004", name="Deccan Gymkhana",         lat=18.5195, lng=73.8410,
         district="PUNE", real_branches=30, rate=14000, dep=420000, nl=44.0,
         veh=34, c2w=0.44, lux=0.058, ev=0.048),
    dict(pc="411014", name="Kalyani Nagar",           lat=18.5580, lng=73.9012,
         district="PUNE", real_branches=31, rate=13000, dep=400000, nl=43.0,
         veh=35, c2w=0.45, lux=0.062, ev=0.052),

    # ── Nagpur (488 real PSU branches / district) ─────────────────────────
    dict(pc="440001", name="Nagpur Sadar / Civil Lines", lat=21.1590, lng=79.0870,
         district="NAGPUR", real_branches=35, rate=6500, dep=230000, nl=30.0,
         veh=20, c2w=0.30, lux=0.025, ev=0.035),
    dict(pc="440010", name="Ramdaspeth",               lat=21.1392, lng=79.0698,
         district="NAGPUR", real_branches=30, rate=7500, dep=260000, nl=31.0,
         veh=22, c2w=0.32, lux=0.030, ev=0.036),
    dict(pc="440015", name="Trimurti Nagar",           lat=21.1280, lng=79.0270,
         district="NAGPUR", real_branches=21, rate=6800, dep=235000, nl=28.0,
         veh=19, c2w=0.29, lux=0.023, ev=0.033),

    # ── Nashik (389 real PSU branches / district) ─────────────────────────
    dict(pc="422009", name="College Road / Gangapur Road", lat=19.9975, lng=73.7649,
         district="NASIK", real_branches=19, rate=7000, dep=250000, nl=29.0,
         veh=19, c2w=0.33, lux=0.024, ev=0.034),
    dict(pc="422001", name="Nashik City Center",       lat=19.9975, lng=73.7898,
         district="NASIK", real_branches=18, rate=5500, dep=200000, nl=27.0,
         veh=17, c2w=0.30, lux=0.019, ev=0.030),
    dict(pc="422003", name="Panchavati",                lat=20.0176, lng=73.7910,
         district="NASIK", real_branches=17, rate=5000, dep=185000, nl=26.0,
         veh=16, c2w=0.29, lux=0.017, ev=0.028),

    # ── Chhatrapati Sambhajinagar / Aurangabad (201 real PSU branches) ────
    dict(pc="431001", name="Aurangabad City Center (Kranti Chowk)", lat=19.8762, lng=75.3433,
         district="CHHATRAPATI SAMBHAJINAGAR", real_branches=40, rate=5500, dep=195000, nl=26.0,
         veh=17, c2w=0.28, lux=0.018, ev=0.028),
    dict(pc="431005", name="Osmanpura / Garkheda",     lat=19.8550, lng=75.3320,
         district="CHHATRAPATI SAMBHAJINAGAR", real_branches=25, rate=5000, dep=180000, nl=24.0,
         veh=15, c2w=0.26, lux=0.015, ev=0.025),
    dict(pc="431003", name="CIDCO Aurangabad",         lat=19.9020, lng=75.3450,
         district="CHHATRAPATI SAMBHAJINAGAR", real_branches=18, rate=4800, dep=170000, nl=23.0,
         veh=14, c2w=0.25, lux=0.014, ev=0.024),

    # ── Kolhapur (231 real PSU branches / district) ───────────────────────
    dict(pc="416001", name="Kolhapur City Center",     lat=16.7050, lng=74.2433,
         district="KOLHAPUR", real_branches=17, rate=5000, dep=195000, nl=25.0,
         veh=18, c2w=0.30, lux=0.020, ev=0.025),
    dict(pc="416008", name="Rajarampuri",               lat=16.6920, lng=74.2320,
         district="KOLHAPUR", real_branches=14, rate=5800, dep=220000, nl=26.0,
         veh=19, c2w=0.31, lux=0.023, ev=0.027),
    dict(pc="416115", name="Ichalkaranji",              lat=16.6910, lng=74.4600,
         district="KOLHAPUR", real_branches=14, rate=4000, dep=160000, nl=22.0,
         veh=15, c2w=0.27, lux=0.016, ev=0.020),

    # ── Solapur (232 real PSU branches / district) ────────────────────────
    dict(pc="413001", name="Solapur City Center",       lat=17.6599, lng=75.9064,
         district="SOLAPUR", real_branches=19, rate=4200, dep=170000, nl=23.0,
         veh=14, c2w=0.24, lux=0.015, ev=0.020),
    dict(pc="413002", name="Solapur Railway Lines",     lat=17.6610, lng=75.9070,
         district="SOLAPUR", real_branches=15, rate=4000, dep=160000, nl=22.0,
         veh=13, c2w=0.23, lux=0.014, ev=0.019),
    dict(pc="413304", name="Pandharpur",                 lat=17.6792, lng=75.3316,
         district="SOLAPUR", real_branches=17, rate=3200, dep=130000, nl=19.0,
         veh=10, c2w=0.20, lux=0.010, ev=0.014),

    # ── Amravati (211 real PSU branches / district) ───────────────────────
    dict(pc="444601", name="Amravati City Center",      lat=20.9374, lng=77.7796,
         district="AMRAVATI", real_branches=24, rate=3800, dep=155000, nl=21.0,
         veh=13, c2w=0.22, lux=0.012, ev=0.018),
    dict(pc="444602", name="Amravati Camp",              lat=20.9320, lng=77.7850,
         district="AMRAVATI", real_branches=10, rate=4000, dep=165000, nl=22.0,
         veh=14, c2w=0.23, lux=0.013, ev=0.019),
    dict(pc="444906", name="Warud",                       lat=21.4600, lng=78.2760,
         district="AMRAVATI", real_branches=10, rate=2800, dep=110000, nl=17.0,
         veh=9,  c2w=0.18, lux=0.008, ev=0.012),

    # ── Nanded (119 real PSU branches / district) ─────────────────────────
    dict(pc="431601", name="Nanded Vazirabad",           lat=19.1383, lng=77.3210,
         district="NANDED", real_branches=19, rate=4000, dep=160000, nl=21.0,
         veh=13, c2w=0.22, lux=0.012, ev=0.018),
    dict(pc="431605", name="Nanded CIDCO / Taroda",      lat=19.1200, lng=77.2950,
         district="NANDED", real_branches=13, rate=3600, dep=145000, nl=20.0,
         veh=12, c2w=0.21, lux=0.011, ev=0.016),
    dict(pc="431602", name="Nanded Shivaji Nagar",       lat=19.1500, lng=77.3050,
         district="NANDED", real_branches=9,  rate=3800, dep=150000, nl=20.0,
         veh=12, c2w=0.21, lux=0.011, ev=0.017),
]

# ── ITR formula (mirrors fetch_itr.py / expand_pincodes.py) ─────────────────
METRO_RATE     = {"MH": 0.260}
CITY_EXPONENT  = {"MH": 0.60}

# ── Overpass POI query (identical to expand_pincodes.py) ────────────────────
OVERPASS  = "https://overpass-api.de/api/interpreter"
RADIUS_M  = 2000
AREA_KM2  = math.pi * (RADIUS_M / 1000) ** 2

POI_Q = """[out:json][timeout:25];
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


def fetch_poi(lat, lng, retries=3):
    for attempt in range(retries):
        try:
            q    = POI_Q.format(r=RADIUS_M, lat=lat, lng=lng)
            data = urllib.parse.urlencode({"data": q}).encode()
            req  = urllib.request.Request(
                OVERPASS, data=data,
                headers={"User-Agent": "PaisaMap-ETL/2.0",
                         "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read())
            return round(int(j["elements"][0]["tags"]["total"]) / AREA_KM2, 1)
        except Exception as e:
            print(f"    WARN (attempt {attempt+1}): {e}")
            time.sleep(5 * (attempt + 1))
    return None


def district_anchor(district: str) -> float:
    """Real PSU branches/lakh for the district, scaled to an all-banks basis."""
    total_real = sum(r["real_branches"] for r in NEW_PINCODES if r["district"] == district)
    # NOTE: total_real above is only the SUM of our *selected* pincodes, not the
    # true district total (924 for Pune etc., quoted in the header comment) —
    # the real district total is used for context/validation, not the anchor
    # calc below, since the district-wide count already lives in
    # data/raw/rbi_branch_counts_mh.csv and can be cross-checked there.
    pop = DISTRICT_POP_LAKH[district]
    share = PSU_SHARE[district]
    psu_counts = pd.read_csv(RAW / "rbi_branch_counts_mh.csv", dtype={"pincode": str})
    district_total_real = psu_counts.loc[psu_counts["district"] == district, "psu_branch_count"].sum()
    return round((district_total_real / pop) / share, 1)


def minmax_scalar(val: float, vals: list) -> float:
    v_min, v_max = min(vals), max(vals)
    scalar = 0.60 + 0.80 * (val - v_min) / max(v_max - v_min, 0.1)
    return max(0.60, min(1.40, scalar))


def main():
    existing = pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str})
    existing_set = set(existing["pincode"])
    rows = [r for r in NEW_PINCODES if r["pc"] not in existing_set]
    skipped = len(NEW_PINCODES) - len(rows)
    if skipped:
        print(f"  · Skipping {skipped} pincodes already in dataset")
    print(f"  Adding {len(rows)} new pincodes  (current total: {len(existing_set)})\n")

    # ── Real bank_branches_per_lakh, by district anchor × within-district share ──
    anchors = {d: district_anchor(d) for d in DISTRICT_POP_LAKH}
    print("District anchors (implied all-bank branches/lakh, from real PSU data):")
    for d, a in anchors.items():
        print(f"  {d:<28} {a}")

    branches_by_district = {}
    for d in DISTRICT_POP_LAKH:
        vals = [r["real_branches"] for r in rows if r["district"] == d]
        branches_by_district[d] = vals

    # ── City property-rate median for ITR scaling (Mumbai-anchored, as elsewhere) ──
    prop_df = pd.read_csv(RAW / "property_rates.csv", dtype={"pincode": str}).set_index("pincode")
    mumbai_pcs = [p for p in existing["pincode"] if p.startswith("40")]
    mh_median = prop_df.reindex(mumbai_pcs)["rate_per_sqft"].dropna().median()
    print(f"\nMumbai property-rate median (ITR anchor): ₹{int(mh_median):,}/sqft")

    # ── POI density queries ──────────────────────────────────────────────────
    print(f"\nFetching POI density for {len(rows)} pincodes from Overpass…")
    poi_map = {}
    for i, r in enumerate(rows):
        pc = r["pc"]
        print(f"  [{i+1:>2}/{len(rows)}] {pc} {r['name']:<32}", end=" ", flush=True)
        density = fetch_poi(r["lat"], r["lng"])
        if density is None:
            density = round(r["nl"] * 0.38, 1)
            print(f"→ {density} (estimated — Overpass failed)")
        else:
            print(f"→ {density} poi/km²")
        poi_map[pc] = density
        time.sleep(3)

    # ── Build rows ────────────────────────────────────────────────────────────
    print("\nBuilding rows…")
    out_rows = []
    for r in rows:
        pc, district = r["pc"], r["district"]
        vals = branches_by_district[district]
        scalar = minmax_scalar(r["real_branches"], vals) if len(vals) >= 2 else 1.0
        bbl = round(anchors[district] * scalar, 1)

        base = METRO_RATE["MH"]
        exp  = CITY_EXPONENT["MH"]
        filers = round(base * (r["rate"] / mh_median) ** exp, 4)

        out_rows.append({
            "pincode":               pc,
            "name":                  r["name"],
            "lat":                   r["lat"],
            "lng":                   r["lng"],
            "rate_per_sqft":         r["rate"],
            "deposits_per_capita":   r["dep"],
            "bank_branches_per_lakh": bbl,
            "radiance_mean":         r["nl"],
            "premium_poi_per_km2":   poi_map.get(pc, round(r["nl"] * 0.38, 1)),
            "filers_per_capita":     filers,
            "cars_per_1000":         r["veh"],
            "car_2w_ratio":          r["c2w"],
            "luxury_share":          r["lux"],
            "ev_share":              r["ev"],
            "district":              district,
        })

    new_df = pd.DataFrame(out_rows).set_index("pincode")

    # ── Append to all CSV files ─────────────────────────────────────────────
    def extend(fname, cols):
        old = pd.read_csv(RAW / fname, dtype={"pincode": str}).set_index("pincode")
        upd = pd.concat([old, new_df[cols]])
        upd.to_csv(RAW / fname)
        print(f"  ✓ {fname:<35} {len(upd)} rows")

    print("\nUpdating raw CSVs…")
    pd.concat([
        pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str}).set_index("pincode"),
        new_df[["lat", "lng"]]
    ]).to_csv(RAW / "pincode_coords.csv")
    print(f"  ✓ pincode_coords.csv  {len(existing_set)+len(rows)} rows")

    pd.concat([
        pd.read_csv(RAW / "pincode_names.csv", dtype={"pincode": str}).set_index("pincode"),
        new_df[["name"]]
    ]).to_csv(RAW / "pincode_names.csv")
    print(f"  ✓ pincode_names.csv   {len(existing_set)+len(rows)} rows")

    extend("property_rates.csv",  ["rate_per_sqft"])
    extend("bank_deposits.csv",   ["deposits_per_capita", "bank_branches_per_lakh"])
    extend("nightlights.csv",     ["radiance_mean"])
    extend("poi_density.csv",     ["premium_poi_per_km2"])
    extend("itr_filers.csv",      ["filers_per_capita"])

    old_veh = pd.read_csv(RAW / "vehicle_density.csv", dtype={"pincode": str}).set_index("pincode")
    pd.concat([old_veh, new_df[["cars_per_1000"]]]).to_csv(RAW / "vehicle_density.csv")
    print(f"  ✓ vehicle_density.csv  {len(old_veh)+len(rows)} rows")

    old_rto = pd.read_csv(RAW / "rto_enhanced.csv", dtype={"pincode": str}).set_index("pincode")
    pd.concat([
        old_rto,
        new_df.rename(columns={"cars_per_1000": "lmv_per_1000"})[
            ["lmv_per_1000", "car_2w_ratio", "luxury_share", "ev_share"]]
    ]).to_csv(RAW / "rto_enhanced.csv")
    print(f"  ✓ rto_enhanced.csv     {len(old_rto)+len(rows)} rows")

    # Register in pincode_district_map.csv so future fetch_rbi_bsr.py runs pick these up
    dm_path = REF = ROOT / "data" / "reference" / "pincode_district_map.csv"
    dm = pd.read_csv(dm_path, dtype={"pincode": str}).set_index("pincode")
    district_title = {
        "PUNE": "Pune", "NAGPUR": "Nagpur", "NASIK": "Nashik",
        "CHHATRAPATI SAMBHAJINAGAR": "Chhatrapati Sambhajinagar",
        "KOLHAPUR": "Kolhapur", "SOLAPUR": "Solapur",
        "AMRAVATI": "Amravati", "NANDED": "Nanded",
    }
    for pc, row in new_df.iterrows():
        dm.loc[pc] = {
            "name":              row["name"],
            "district":          district_title[row["district"]],
            "state_code":        "MH",
            "state_name":        "Maharashtra",
            "district_pop_lakh": DISTRICT_POP_LAKH[row["district"]],
        }
    dm.to_csv(dm_path)
    print(f"  ✓ pincode_district_map.csv  {len(dm)} rows")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n✓ Pincode expansion complete: {len(existing_set)} → {len(existing_set)+len(rows)}")
    print("\nNew pincodes summary:")
    for _, row in new_df.sort_values(["district", "pincode"]).iterrows():
        print(f"  {row.name}  {row['name']:<30} ₹{int(row['rate_per_sqft']):>6,}/sqft  "
              f"branches/lakh={row['bank_branches_per_lakh']:>5.1f}  "
              f"poi={row['premium_poi_per_km2']:>5.1f}  itr={row['filers_per_capita']:.3f}")


if __name__ == "__main__":
    main()
