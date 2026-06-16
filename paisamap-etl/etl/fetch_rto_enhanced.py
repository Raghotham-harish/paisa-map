"""
fetch_rto_enhanced.py
Downloads OpenCity RTO registration data (same 15 CSVs as fetch_vehicle.py)
and extracts FOUR vehicle-based signals per pincode:

  1. lmv_per_1000          — cars per 1000 people (stock proxy)
  2. car_2w_ratio          — cars / two-wheelers (wealth indicator; doesn't
                             need population denominator, so more reliable)
  3. luxury_share          — % of car registrations from BMW, Mercedes, JLR,
                             Audi/VW/Porsche group, Volvo, Ferrari, Lamborghini
                             → direct income signal; rich areas buy luxury cars
  4. ev_share              — electric vehicles / total registrations
                             → correlated with income + urban tech adoption

Derived signals are written to  data/raw/rto_enhanced.csv  and the raw
combined frame (all categories) is cached to  data/raw/rto_raw_cache.csv.gz
so subsequent runs skip the 15-file download.

Source: data.opencity.in/dataset (CC BY 4.0)
Method: cumulative new registrations 2021–2025 per RTO → mapped to pincodes.
"""

import io
import os
import time
import urllib.request
import pandas as pd

OVERWRITE_CACHE = False   # set True to force re-download

DELHI_URLS = [
    "https://data.opencity.in/dataset/d9dfb9e3-2d13-4f78-b590-4a1157fbe77d/resource/19318a1f-22de-40ec-a2af-9be313f5aa2c/download/aaacdd04-398b-4e68-8fac-4da2d0b16376.csv",
    "https://data.opencity.in/dataset/d9dfb9e3-2d13-4f78-b590-4a1157fbe77d/resource/293a496c-8d77-4109-893c-cd9ff1158d99/download/a2747cd8-a56b-4a6e-bbfe-aa91d01383af.csv",
    "https://data.opencity.in/dataset/d9dfb9e3-2d13-4f78-b590-4a1157fbe77d/resource/2903ccbf-d971-4146-847a-30ba767e5bc1/download/1105a62c-ac6b-46ee-b623-a9b53c3b6912.csv",
    "https://data.opencity.in/dataset/d9dfb9e3-2d13-4f78-b590-4a1157fbe77d/resource/3959e0d7-d027-457d-a7ad-d62021d051c3/download/b0b291ae-9867-4027-a2ea-047494f5dd13.csv",
    "https://data.opencity.in/dataset/d9dfb9e3-2d13-4f78-b590-4a1157fbe77d/resource/b4536e08-5163-4e0a-b249-aecb0bdd30aa/download/0819c12f-e925-402c-be4c-decfe37ccf00.csv",
]
MUMBAI_URLS = [
    "https://data.opencity.in/dataset/48f30e56-bb29-4506-94c4-132aed4492b9/resource/a98d9232-9e8d-40be-b326-59399e451b12/download/4645bd7d-7fd0-42c1-8efb-2fc4cce4803a.csv",
    "https://data.opencity.in/dataset/48f30e56-bb29-4506-94c4-132aed4492b9/resource/8e7dadb0-81bb-4bb3-bf35-ebfc554123c3/download/4198a4cb-7c9f-4c28-9f96-6d946d3caa60.csv",
    "https://data.opencity.in/dataset/48f30e56-bb29-4506-94c4-132aed4492b9/resource/d4cfa71e-7b6c-44dc-ad7d-14425ce58e3b/download/abb8f161-69b6-4ace-a7d2-fd3c2632e52d.csv",
    "https://data.opencity.in/dataset/48f30e56-bb29-4506-94c4-132aed4492b9/resource/aa9e2268-a338-4c04-ae4a-4eb4f8405810/download/482c3bee-bf1a-4940-a5cb-08ae558d6389.csv",
    "https://data.opencity.in/dataset/48f30e56-bb29-4506-94c4-132aed4492b9/resource/48f9bce0-69ac-47e3-9c86-98f9d9331475/download/23b79bf2-e265-436d-9d09-91e4c7f3bc88.csv",
]
BENGALURU_URLS = [
    "https://data.opencity.in/dataset/71ab0845-b439-4c39-bf53-d157ae10bdef/resource/cdbd693d-2f1d-4fad-a43f-878f54f73cdf/download/e4fe1f99-a49d-4642-88b5-03c4479bc6be.csv",
    "https://data.opencity.in/dataset/71ab0845-b439-4c39-bf53-d157ae10bdef/resource/7d9c429d-45f9-42ae-a869-51415f51c769/download/c9e55b76-e0da-4864-96e4-91f383a96812.csv",
    "https://data.opencity.in/dataset/71ab0845-b439-4c39-bf53-d157ae10bdef/resource/76c2fd86-f061-4d56-a496-4733ddbeb9b0/download/32394726-4451-424f-9e2f-1c4c7b979240.csv",
    "https://data.opencity.in/dataset/71ab0845-b439-4c39-bf53-d157ae10bdef/resource/952d0b2f-63f5-4628-af82-b4e6ffaed605/download/a95a4755-b1a1-4c99-979f-2540080990df.csv",
    "https://data.opencity.in/dataset/71ab0845-b439-4c39-bf53-d157ae10bdef/resource/c4c2b458-1d7f-4da1-b7d1-c5a02fb0f133/download/da330e05-c93c-42a3-94ba-09ebd5dd28d3.csv",
]

PINCODE_RTO = {
    "110003": ("DL",  3), "110021": ("DL", 12), "110057": ("DL", 12),
    "110024": ("DL",  3), "110048": ("DL",  3), "110016": ("DL",  3),
    "110017": ("DL",  3), "110070": ("DL",  9), "110034": ("DL", 11),
    "110092": ("DL",  7), "110059": ("DL",  4), "110093": ("DL", 13),
    "110040": ("DL",  1), "201301": ("UP", 99),
    "400021": ("MH",  1), "400005": ("MH",  1), "400049": ("MH",  2),
    "400051": ("MH",  2), "400053": ("MH",  2), "400059": ("MH",  2),
    "400068": ("MH",  3), "400050": ("MH",  3), "400071": ("MH",  3),
    "400070": ("MH",  3), "400063": ("MH", 47), "400086": ("MH", 47),
    "560025": ("KA",  3), "560027": ("KA",  5), "560001": ("KA",  1),
    "560099": ("KA", 51), "560034": ("KA",  3), "560017": ("KA",  5),
    "560076": ("KA",  2), "560037": ("KA",  3), "560011": ("KA",  5),
    "560068": ("KA", 41), "560085": ("KA",  5), "560035": ("KA", 51),
}

RTO_POP = {
    ("DL",  1):   887_978,  ("DL",  2):   142_004,  ("DL",  3): 2_731_929,
    ("DL",  4): 1_271_621,  ("DL",  5): 2_241_624,  ("DL",  6):   291_160,
    ("DL",  7):   854_673,  ("DL",  8): 1_828_270,  ("DL",  9): 1_146_479,
    ("DL", 10): 1_271_622,  ("DL", 11): 1_828_269,  ("DL", 12): 1_146_479,
    ("DL", 13):   854_673,
    ("MH",  1): 3_085_411,  ("MH",  2): 3_700_000,
    ("MH",  3): 3_500_000,  ("MH", 47): 2_156_962,
    ("KA",  1): 1_202_694,  ("KA",  2): 1_202_694,  ("KA",  3): 1_202_694,
    ("KA",  4): 1_202_694,  ("KA",  5): 1_202_694,  ("KA", 41): 1_202_694,
    ("KA", 50): 1_202_694,  ("KA", 51): 1_202_694,
}

# ── Vehicle classification ───────────────────────────────────────────────────
# Luxury car manufacturers registered in India (by OEM name in Vahan/OpenCity)
LUXURY_MAKERS = {
    "BMW INDIA",
    "MERCEDES-BENZ",
    "JAGUAR LAND ROVER",
    "AUDI INDIA",
    "SKODA AUTO VOLKSWAGEN",   # covers VW, Skoda, Audi, Porsche under one entity
    "VOLVO AUTO INDIA",
    "VOLVO CAR",
    "FERRARI",
    "LAMBORGHINI",
    "PORSCHE",
    "ROLLS-ROYCE",
    "BENTLEY",
    "MASERATI",
    "ASTON MARTIN",
}


def is_luxury(maker_name: str) -> bool:
    m = str(maker_name).upper()
    return any(lux in m for lux in LUXURY_MAKERS)


CACHE_PATH = "data/raw/rto_raw_cache.csv.gz"


def load_or_download() -> pd.DataFrame:
    if not OVERWRITE_CACHE and os.path.exists(CACHE_PATH):
        print(f"  Loading from cache {CACHE_PATH}…")
        return pd.read_csv(CACHE_PATH, dtype={"State": str, "RTO": int})

    all_urls = DELHI_URLS + MUMBAI_URLS + BENGALURU_URLS
    city_labels = ["DL"] * 5 + ["MH"] * 5 + ["KA"] * 5
    frames = []
    for i, url in enumerate(all_urls):
        print(f"  [{i+1:>2}/{len(all_urls)}] {city_labels[i]} {2021 + i % 5}…",
              end=" ", flush=True)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PaisaMap-ETL/2.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                df = pd.read_csv(io.BytesIO(r.read()))
            frames.append(df)
            print(f"{len(df):,} rows")
        except Exception as e:
            print(f"WARN: {e}")
        time.sleep(1)

    if not frames:
        raise SystemExit("All downloads failed.")

    data = pd.concat(frames, ignore_index=True)
    print(f"  Saving cache → {CACHE_PATH}")
    data.to_csv(CACHE_PATH, index=False, compression="gzip")
    return data


def main():
    print("Loading OpenCity RTO data…")
    data = load_or_download()
    print(f"  Total rows: {len(data):,}")

    # ── Per-RTO aggregation ─────────────────────────────────────────────────
    # Category counts
    cat = data[data["Metric"] == "Registration Category"].copy()
    cat["Name"] = cat["Name"].str.strip().str.upper()

    lmv   = cat[cat["Name"] == "LIGHT MOTOR VEHICLE"].groupby(["State","RTO"])["Count"].sum()
    tw    = cat[cat["Name"] == "TWO WHEELER(NT)"].groupby(["State","RTO"])["Count"].sum()
    total_cat = cat.groupby(["State","RTO"])["Count"].sum()

    # Luxury manufacturer registrations
    mfr = data[data["Metric"] == "Registration Manufacturer"].copy()
    mfr["is_luxury"] = mfr["Name"].apply(is_luxury)
    luxury = mfr[mfr["is_luxury"]].groupby(["State","RTO"])["Count"].sum()

    # EV registrations
    fuel = data[data["Metric"] == "Registration Fuel"].copy()
    fuel["Name"] = fuel["Name"].str.strip().str.upper()
    ev = fuel[fuel["Name"].str.startswith("ELECTRIC")].groupby(["State","RTO"])["Count"].sum()
    total_fuel = fuel.groupby(["State","RTO"])["Count"].sum()

    # ── Print RTO summary ───────────────────────────────────────────────────
    rto_index = sorted(set(
        list(lmv.index) + list(tw.index) + list(total_cat.index)
    ))
    print("\nRTO-level summary:")
    for state, rto in rto_index:
        l = int(lmv.get((state, rto), 0))
        t = int(tw.get((state, rto), 0))
        lux = int(luxury.get((state, rto), 0))
        e = int(ev.get((state, rto), 0))
        tot = int(total_cat.get((state, rto), 0))
        ratio = round(l / max(t, 1), 2)
        lux_pct = round(lux / max(l, 1) * 100, 1)
        ev_pct  = round(e / max(tot, 1) * 100, 2)
        print(f"  {state}-{rto:<3}  LMV={l:>7,}  2W={t:>7,}  car/2W={ratio:>5.2f}"
              f"  luxury={lux_pct:>5.1f}%  EV={ev_pct:>5.2f}%")

    # ── Map to pincodes ─────────────────────────────────────────────────────
    results = []
    east_dl_lmv = int(sum(lmv.get(("DL", r), 0) for r in [7, 13]))
    east_dl_2w  = int(sum(tw.get(("DL",  r), 0) for r in [7, 13]))
    east_dl_lux = int(sum(luxury.get(("DL", r), 0) for r in [7, 13]))
    east_dl_ev  = int(sum(ev.get(("DL",  r), 0) for r in [7, 13]))
    east_dl_tot = int(sum(total_fuel.get(("DL", r), 0) for r in [7, 13]))
    east_dl_pop = RTO_POP[("DL", 7)] + RTO_POP[("DL", 13)]

    for pc, (state, rto) in PINCODE_RTO.items():
        if state == "UP":   # Noida → East Delhi proxy
            lmv_n  = int(east_dl_lmv  * 1.05)
            tw_n   = int(east_dl_2w   * 0.95)   # Noida slightly more car-oriented
            lux_n  = int(east_dl_lux  * 1.10)   # IT workers, slightly more luxury
            ev_n   = int(east_dl_ev   * 1.15)
            tot_n  = int(east_dl_tot  * 1.05)
            pop    = east_dl_pop
        else:
            lmv_n  = int(lmv.get((state, rto), 0))
            tw_n   = int(tw.get((state, rto), 0))
            lux_n  = int(luxury.get((state, rto), 0))
            ev_n   = int(ev.get((state, rto), 0))
            tot_n  = int(total_fuel.get((state, rto), 0))
            pop    = RTO_POP.get((state, rto), 1_000_000)

        if lmv_n == 0:
            print(f"  WARN: {pc} → {state}-{rto} has 0 LMV — skipping")
            continue

        results.append({
            "pincode":         pc,
            "lmv_per_1000":    round(lmv_n / pop * 1000, 1),
            "car_2w_ratio":    round(lmv_n / max(tw_n, 1), 3),
            "luxury_share":    round(lux_n / max(lmv_n, 1), 5),
            "ev_share":        round(ev_n  / max(tot_n, 1), 5),
            # raw counts for ML feature matrix
            "_lmv":  lmv_n, "_2w": tw_n, "_lux": lux_n, "_ev": ev_n,
        })

    out = pd.DataFrame(results)
    # Keep only the 4 signal columns (plus pincode) for the pipeline
    out[["pincode","lmv_per_1000","car_2w_ratio","luxury_share","ev_share"]].to_csv(
        "data/raw/rto_enhanced.csv", index=False)
    print(f"\nWrote {len(out)} rows → data/raw/rto_enhanced.csv")

    print("\nTop 10 pincodes by luxury_share:")
    print(out.nlargest(10,"luxury_share")[
        ["pincode","car_2w_ratio","luxury_share","ev_share","lmv_per_1000"]
    ].to_string(index=False))

    print("\nTop 10 pincodes by car_2w_ratio:")
    print(out.nlargest(10,"car_2w_ratio")[
        ["pincode","car_2w_ratio","luxury_share","ev_share","lmv_per_1000"]
    ].to_string(index=False))

    # Sanity gates
    print("\nValidation gates:")
    idx = out.set_index("pincode")
    for hi, lo, metric in [
        ("110003", "110040", "luxury_share"),
        ("110003", "110040", "car_2w_ratio"),
        ("400021", "400063", "luxury_share"),
        ("560025", "560035", "luxury_share"),
    ]:
        if hi in idx.index and lo in idx.index:
            ok = idx.loc[hi, metric] >= idx.loc[lo, metric]
            print(f"  {'PASS' if ok else 'FAIL'}  {metric}({hi}) >= {metric}({lo})"
                  f"  [{idx.loc[hi, metric]:.4f} vs {idx.loc[lo, metric]:.4f}]")


if __name__ == "__main__":
    main()
