"""
expand_pincodes.py — Expand PaisaMap from 38 → 72 pincodes.

New pincodes (+34):
  Delhi/NCR  +11 : Connaught Place, Chandni Chowk, Model Town, Punjabi Bagh,
                   Janakpuri, Dwarka, Rohini, Mayur Vihar Ph1, Vivek Vihar,
                   Gurgaon City (Sec 14), Gurgaon Golf Course Road
  Mumbai     +12 : Malabar Hill, Lower Parel, Worli, Dadar West, Vile Parle West,
                   Juhu, Goregaon West, Malad East, Chembur East, Mulund West,
                   Vashi (Navi Mumbai), Airoli (Navi Mumbai)
  Bengaluru  +11 : Shivajinagar, Chamrajpet, Rajajinagar, Ulsoor, Basavanagudi,
                   RT Nagar, Hebbal, Yelahanka, Brookefield, JP Nagar, Sarjapur Rd

Data sources:
  property_rates  : 99acres / MagicBricks / Square Yards 2024 city price indices
  bank_deposits   : estimated via city deposit/property ratio calibrated on existing data
  nightlights     : estimated from NASA VIIRS urban radiance patterns (urban core vs fringe)
  vehicle density : RTO cache (fetch_rto_enhanced.py) for DL/MH/KA; published
                    Vahan district totals for Gurgaon (HR) and Navi Mumbai
  car_2w / luxury / ev : from same RTO cache; estimated for non-cached RTOs
  poi_density     : live Overpass queries
  itr_filers      : computed inline using same CBDT formula as fetch_itr.py
"""

import json
import math
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW  = ROOT / "data" / "raw"

# ── New pincode definitions ──────────────────────────────────────────────────
# rto=None  → use veh_est/car2w/lux/ev estimates (no cache data for this RTO)
# All property rates: ₹/sqft, 2024 published figures
# All bank_deposits: ₹/capita, estimated from city-level deposit/rate ratio
# All nightlights: radiance_mean, estimated from NASA VIIRS urban core patterns
NEW_PINCODES = [
    # ── Delhi / NCR ──────────────────────────────────────────────────────────
    dict(pc="110001", name="Connaught Place",        lat=28.6329, lng=77.2197,
         state="DL", rto=2,    rate=23000, dep=620000, nl=58.0),
    dict(pc="110006", name="Chandni Chowk",          lat=28.6562, lng=77.2300,
         state="DL", rto=6,    rate=11500, dep=320000, nl=44.0),
    dict(pc="110009", name="Model Town",             lat=28.7143, lng=77.1908,
         state="DL", rto=11,   rate=13500, dep=358000, nl=36.0),
    dict(pc="110026", name="Punjabi Bagh",           lat=28.6686, lng=77.1277,
         state="DL", rto=10,   rate=16000, dep=423000, nl=40.0),
    dict(pc="110058", name="Janakpuri",              lat=28.6282, lng=77.0847,
         state="DL", rto=4,    rate=11500, dep=305000, nl=35.0),
    dict(pc="110075", name="Dwarka",                 lat=28.5921, lng=77.0460,
         state="DL", rto=9,    rate=11000, dep=290000, nl=30.0),
    dict(pc="110085", name="Rohini",                 lat=28.7495, lng=77.0737,
         state="DL", rto=11,   rate=10500, dep=280000, nl=28.0),
    dict(pc="110091", name="Mayur Vihar Phase 1",    lat=28.6085, lng=77.2960,
         state="DL", rto=7,    rate=12000, dep=318000, nl=34.0),
    dict(pc="110032", name="Vivek Vihar",            lat=28.6724, lng=77.3078,
         state="DL", rto=5,    rate=11000, dep=292000, nl=30.0),
    # Gurgaon — Haryana (HR), not in OpenCity cache; estimated from Vahan HR data
    dict(pc="122002", name="Gurgaon City",           lat=28.4738, lng=77.0340,
         state="HR", rto=None, rate=10000, dep=320000, nl=38.0,
         veh=52.0, c2w=1.20, lux=0.082, ev=0.048),
    dict(pc="122022", name="Gurgaon Golf Course Rd", lat=28.4324, lng=77.1005,
         state="HR", rto=None, rate=28000, dep=780000, nl=42.0,
         veh=88.0, c2w=2.10, lux=0.168, ev=0.072),

    # ── Mumbai ───────────────────────────────────────────────────────────────
    dict(pc="400006", name="Malabar Hill",           lat=18.9630, lng=72.8003,
         state="MH", rto=1,    rate=88000, dep=1850000, nl=60.0),
    dict(pc="400013", name="Lower Parel",            lat=18.9937, lng=72.8263,
         state="MH", rto=1,    rate=32000, dep=900000,  nl=56.0),
    dict(pc="400018", name="Worli",                  lat=19.0175, lng=72.8169,
         state="MH", rto=1,    rate=52000, dep=1150000, nl=54.0),
    dict(pc="400028", name="Dadar West",             lat=19.0178, lng=72.8478,
         state="MH", rto=1,    rate=27000, dep=760000,  nl=50.0),
    dict(pc="400054", name="Vile Parle West",        lat=19.0983, lng=72.8487,
         state="MH", rto=2,    rate=26000, dep=720000,  nl=46.0),
    dict(pc="400060", name="Juhu",                   lat=19.0971, lng=72.8263,
         state="MH", rto=2,    rate=55000, dep=1200000, nl=50.0),
    dict(pc="400062", name="Goregaon West",          lat=19.1584, lng=72.8494,
         state="MH", rto=2,    rate=18000, dep=480000,  nl=38.0),
    dict(pc="400097", name="Malad East",             lat=19.1776, lng=72.8681,
         state="MH", rto=47,   rate=13500, dep=360000,  nl=32.0),
    dict(pc="400074", name="Chembur East",           lat=19.0488, lng=72.9302,
         state="MH", rto=3,    rate=17500, dep=445000,  nl=34.0),
    dict(pc="400080", name="Mulund West",            lat=19.1680, lng=72.9510,
         state="MH", rto=3,    rate=16000, dep=400000,  nl=30.0),
    # Navi Mumbai — MH state but under Panvel/Raigad RTO, not in cache
    dict(pc="400614", name="Vashi",                  lat=19.0771, lng=73.0028,
         state="MH", rto=None, rate=12000, dep=330000, nl=35.0,
         veh=38.0, c2w=0.55, lux=0.072, ev=0.030),
    dict(pc="400708", name="Airoli",                 lat=19.1553, lng=72.9984,
         state="MH", rto=None, rate=10000, dep=280000, nl=28.0,
         veh=32.0, c2w=0.60, lux=0.065, ev=0.028),

    # ── Bengaluru ─────────────────────────────────────────────────────────────
    dict(pc="560002", name="Shivajinagar",           lat=12.9836, lng=77.6081,
         state="KA", rto=1,    rate=12000, dep=320000, nl=44.0),
    dict(pc="560003", name="Chamrajpet",             lat=12.9620, lng=77.5632,
         state="KA", rto=2,    rate=10500, dep=278000, nl=36.0),
    dict(pc="560004", name="Rajajinagar",            lat=12.9952, lng=77.5500,
         state="KA", rto=2,    rate=12500, dep=330000, nl=38.0),
    dict(pc="560008", name="Ulsoor",                 lat=12.9743, lng=77.6186,
         state="KA", rto=3,    rate=13500, dep=355000, nl=46.0),
    dict(pc="560029", name="Basavanagudi",           lat=12.9370, lng=77.5763,
         state="KA", rto=5,    rate=12000, dep=315000, nl=36.0),
    dict(pc="560032", name="RT Nagar",               lat=13.0231, lng=77.5907,
         state="KA", rto=1,    rate=10000, dep=265000, nl=32.0),
    dict(pc="560047", name="Hebbal",                 lat=13.0358, lng=77.5953,
         state="KA", rto=1,    rate=8500,  dep=225000, nl=28.0),
    dict(pc="560064", name="Yelahanka",              lat=13.1067, lng=77.5955,
         state="KA", rto=4,    rate=6500,  dep=172000, nl=22.0),
    dict(pc="560066", name="Brookefield",            lat=12.9820, lng=77.7212,
         state="KA", rto=3,    rate=9000,  dep=240000, nl=30.0),
    dict(pc="560078", name="JP Nagar",               lat=12.9044, lng=77.5856,
         state="KA", rto=5,    rate=12000, dep=315000, nl=36.0),
    dict(pc="560103", name="Sarjapur Road",          lat=12.9013, lng=77.6791,
         state="KA", rto=51,   rate=9000,  dep=238000, nl=26.0),
]

# ── ITR parameters (mirrors fetch_itr.py) ────────────────────────────────────
METRO_RATE = {
    "DL": 0.2502,
    "MH": 0.260,
    "KA": 0.220,
    "UP": 0.2312,
    "HR": 0.285,   # Gurgaon metro: ~3× HR state avg (0.093), IT/corporate hub
}
CITY_EXPONENT = {"DL": 0.70, "MH": 0.60, "KA": 0.70, "UP": 0.70, "HR": 0.70}

# ── RTO cache metrics ─────────────────────────────────────────────────────────
LUXURY_MAKERS = {
    "BMW INDIA", "MERCEDES-BENZ", "JAGUAR LAND ROVER", "SKODA AUTO VOLKSWAGEN",
    "VOLVO AUTO INDIA", "VOLVO CAR", "FERRARI", "LAMBORGHINI", "PORSCHE",
    "ROLLS-ROYCE", "BENTLEY", "MASERATI", "ASTON MARTIN",
}
RTO_POP = {
    ("DL", 1): 887978,   ("DL", 2): 142004,   ("DL", 3): 2731929,
    ("DL", 4): 1271621,  ("DL", 5): 2241624,  ("DL", 6): 291160,
    ("DL", 7): 854673,   ("DL", 8): 1828270,  ("DL", 9): 1146479,
    ("DL",10): 1271622,  ("DL",11): 1828269,  ("DL",12): 1146479,
    ("DL",13): 854673,
    ("MH", 1): 3085411,  ("MH", 2): 3700000,
    ("MH", 3): 3500000,  ("MH",47): 2156962,
    ("KA", 1): 1202694,  ("KA", 2): 1202694,  ("KA", 3): 1202694,
    ("KA", 4): 1202694,  ("KA", 5): 1202694,  ("KA",41): 1202694,
    ("KA",50): 1202694,  ("KA",51): 1202694,
}


def build_rto_signals():
    """Build per-(State,RTO) vehicle metrics from the cached OpenCity data."""
    cache = pd.read_csv(RAW / "rto_raw_cache.csv.gz", dtype={"State": str, "RTO": int})

    cat = cache[cache["Metric"] == "Registration Category"].copy()
    cat["Name"] = cat["Name"].str.strip().str.upper()
    lmv   = cat[cat["Name"] == "LIGHT MOTOR VEHICLE"].groupby(["State","RTO"])["Count"].sum()
    tw    = cat[cat["Name"] == "TWO WHEELER(NT)"].groupby(["State","RTO"])["Count"].sum()

    mfr   = cache[cache["Metric"] == "Registration Manufacturer"].copy()
    mfr["is_lux"] = mfr["Name"].apply(lambda n: any(k in str(n).upper() for k in LUXURY_MAKERS))
    luxury = mfr[mfr["is_lux"]].groupby(["State","RTO"])["Count"].sum()

    fuel  = cache[cache["Metric"] == "Registration Fuel"].copy()
    fuel["Name"] = fuel["Name"].str.strip().str.upper()
    ev    = fuel[fuel["Name"].str.startswith("ELECTRIC")].groupby(["State","RTO"])["Count"].sum()
    tot_f = fuel.groupby(["State","RTO"])["Count"].sum()

    return lmv, tw, luxury, ev, tot_f


def rto_signal(state, rto_num, lmv, tw, luxury, ev, tot_f):
    key = (state, rto_num)
    l   = int(lmv.get(key, 0))
    t   = int(tw.get(key, 0))
    lux = int(luxury.get(key, 0))
    e   = int(ev.get(key, 0))
    tf  = int(tot_f.get(key, 0))
    pop = RTO_POP.get(key, 1_000_000)
    return {
        "lmv_per_1000":  round(l / pop * 1000, 1) if pop else 0.0,
        "car_2w_ratio":  round(l / max(t, 1), 3),
        "luxury_share":  round(lux / max(l, 1), 5),
        "ev_share":      round(e / max(tf, 1), 5),
    }


# ── Overpass POI query ────────────────────────────────────────────────────────
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


def main():
    # ── Filter out pincodes already in the dataset ────────────────────────────
    existing = pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str})
    existing_set = set(existing["pincode"])

    rows = [r for r in NEW_PINCODES if r["pc"] not in existing_set]
    skipped = len(NEW_PINCODES) - len(rows)
    if skipped:
        print(f"  · Skipping {skipped} pincodes already in dataset")
    print(f"  Adding {len(rows)} new pincodes  (current total: {len(existing_set)})\n")

    # ── Build RTO signal lookup from cache ────────────────────────────────────
    print("Loading RTO cache…")
    lmv, tw, luxury, ev, tot_f = build_rto_signals()

    # ── Compute per-pincode signal dict ───────────────────────────────────────
    prop_df  = pd.read_csv(RAW / "property_rates.csv",  dtype={"pincode": str}).set_index("pincode")
    itr_df   = pd.read_csv(RAW / "itr_filers.csv",      dtype={"pincode": str}).set_index("pincode")

    # City medians for ITR within-city scaling (from existing pincodes)
    city_pcs = {"DL": [], "MH": [], "KA": [], "HR": [], "UP": []}
    for pc_row in existing.itertuples():
        pc = pc_row.pincode
        if pc.startswith("11") or pc.startswith("20"):
            city_pcs["DL" if pc.startswith("11") else "UP"].append(pc)
        elif pc.startswith("40"):
            city_pcs["MH"].append(pc)
        elif pc.startswith("56"):
            city_pcs["KA"].append(pc)
    city_median = {}
    for st, pcs in city_pcs.items():
        vals = prop_df.reindex(pcs)["rate_per_sqft"].dropna()
        city_median[st] = vals.median() if len(vals) else prop_df["rate_per_sqft"].median()
    # Gurgaon: use Delhi median as reference (same income bracket)
    city_median["HR"] = city_median["DL"]
    print(f"  City property medians: " +
          "  ".join(f"{st}=₹{int(m):,}" for st, m in city_median.items()))

    # ── POI density queries ───────────────────────────────────────────────────
    print(f"\nFetching POI density for {len(rows)} pincodes from Overpass…")
    poi_map = {}
    for i, r in enumerate(rows):
        pc = r["pc"]
        print(f"  [{i+1:>2}/{len(rows)}] {pc} {r['name']:<26}", end=" ", flush=True)
        density = fetch_poi(r["lat"], r["lng"])
        if density is None:
            density = round(r["nl"] * 0.38, 1)   # fallback: nl-based estimate
            print(f"→ {density} (estimated — Overpass failed)")
        else:
            print(f"→ {density} poi/km²")
        poi_map[pc] = density
        time.sleep(3)

    # ── Build new rows ────────────────────────────────────────────────────────
    print("\nBuilding rows…")
    out_rows = []
    for r in rows:
        pc    = r["pc"]
        state = r["state"]
        rto   = r.get("rto")

        # Vehicle / luxury / EV signals
        if rto is not None:
            sig = rto_signal(state, rto, lmv, tw, luxury, ev, tot_f)
        else:
            sig = {
                "lmv_per_1000": r["veh"],
                "car_2w_ratio": r["c2w"],
                "luxury_share": r["lux"],
                "ev_share":     r["ev"],
            }

        # ITR filers
        rate_val  = r["rate"]
        med       = city_median.get(state, city_median["DL"])
        base_rate = METRO_RATE.get(state, 0.20)
        exp       = CITY_EXPONENT.get(state, 0.70)
        filers    = round(base_rate * (rate_val / med) ** exp, 4)

        out_rows.append({
            "pincode":             pc,
            "name":                r["name"],
            "lat":                 r["lat"],
            "lng":                 r["lng"],
            "rate_per_sqft":       rate_val,
            "deposits_per_capita": r["dep"],
            "radiance_mean":       r["nl"],
            "premium_poi_per_km2": poi_map.get(pc, round(r["nl"] * 0.38, 1)),
            "filers_per_capita":   filers,
            **sig,
        })

    new_df = pd.DataFrame(out_rows).set_index("pincode")

    # ── Append to all CSV files ───────────────────────────────────────────────
    def extend(fname, col):
        old = pd.read_csv(RAW / fname, dtype={"pincode": str}).set_index("pincode")
        upd = pd.concat([old, new_df[[col]]])
        upd.to_csv(RAW / fname)
        print(f"  ✓ {fname:<35} {len(upd)} rows")

    print("\nUpdating raw CSVs…")
    pd.concat([
        pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str}).set_index("pincode"),
        new_df[["lat","lng"]]
    ]).to_csv(RAW / "pincode_coords.csv")
    print(f"  ✓ pincode_coords.csv  {len(existing_set)+len(rows)} rows")

    pd.concat([
        pd.read_csv(RAW / "pincode_names.csv", dtype={"pincode": str}).set_index("pincode"),
        new_df[["name"]]
    ]).to_csv(RAW / "pincode_names.csv")
    print(f"  ✓ pincode_names.csv   {len(existing_set)+len(rows)} rows")

    extend("property_rates.csv",  "rate_per_sqft")
    extend("bank_deposits.csv",   "deposits_per_capita")
    extend("nightlights.csv",     "radiance_mean")
    extend("poi_density.csv",     "premium_poi_per_km2")
    extend("itr_filers.csv",      "filers_per_capita")

    # vehicle_density uses cars_per_1000, rto_enhanced uses the 4-column set
    old_veh = pd.read_csv(RAW / "vehicle_density.csv", dtype={"pincode": str}).set_index("pincode")
    pd.concat([old_veh, new_df[["lmv_per_1000"]].rename(
        columns={"lmv_per_1000": "cars_per_1000"})]).to_csv(RAW / "vehicle_density.csv")
    print(f"  ✓ vehicle_density.csv  {len(old_veh)+len(rows)} rows")

    old_rto = pd.read_csv(RAW / "rto_enhanced.csv", dtype={"pincode": str}).set_index("pincode")
    pd.concat([old_rto,
               new_df[["lmv_per_1000","car_2w_ratio","luxury_share","ev_share"]]
              ]).to_csv(RAW / "rto_enhanced.csv")
    print(f"  ✓ rto_enhanced.csv     {len(old_rto)+len(rows)} rows")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n✓ Pincode expansion complete: {len(existing_set)} → {len(existing_set)+len(rows)}")
    print("\nNew pincodes summary:")
    for _, row in new_df.sort_index().iterrows():
        print(f"  {row.name}  {row['name']:<28} ₹{int(row['rate_per_sqft']):>6,}/sqft  "
              f"poi={row['premium_poi_per_km2']:>5.1f}  itr={row['filers_per_capita']:.3f}")


if __name__ == "__main__":
    main()
