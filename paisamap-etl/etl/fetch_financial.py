"""
fetch_financial.py — Financial inclusion signal: SFB / co-op / RRB branch density.

Strategy (v2 — fixed timeout):
  One simple Overpass query per pincode: fetch ALL bank tags within 5km radius.
  Classify SFB / COOP / RRB in Python — no server-side regex, no timeouts.

OSM tag fields checked: name, operator, brand (catches inconsistent tagging).

Output: data/raw/financial_inclusion.csv
  pincode, sfb_branches, coop_branches, rrb_branches,
  fin_branches_total, fin_density_per_km2

Usage:
  cd paisamap-etl
  python3 etl/fetch_financial.py           # full run
  python3 etl/fetch_financial.py --resume  # skip pincodes already in output CSV
  python3 etl/fetch_financial.py --dry-run # query but don't write
"""

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

OVERPASS  = "https://overpass-api.de/api/interpreter"
RADIUS_M  = 5_000               # 5 km — reduces data volume vs old 8 km
AREA_KM2  = math.pi * (RADIUS_M / 1000) ** 2   # ~78.5 km²
SLEEP_SEC = 4                   # between requests — Overpass rate limit

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
OUT = RAW / "financial_inclusion.csv"

# ── Classification keywords (checked against name + operator + brand tags) ────
# Lower-case, substring match. Order matters: SFB checked before COOP.

SFB_KEYWORDS = [
    "ujjivan", "equitas", "au small finance", "au sfb",
    "jana small finance", "jana sfb", "suryoday", "esaf",
    "utkarsh", "capital small finance", "fincare",
    "north east small finance", "shivalik small finance",
    "unity small finance", "savein", "northeast sfb",
]

COOP_KEYWORDS = [
    "saraswat", "cosmos co-op", "cosmos bank", "svc bank",
    "tjsb", "nkgsb", "abhyudaya", "janata sahakari",
    "mahesh bank", "sahakari bank", "co-operative bank",
    "cooperative bank", "urban co-op", "nagpur nagarik",
    "zoroastrian", "the shamrao vithal", "apna sahakari",
    "mandvi co-op", "goa urban",
]

RRB_KEYWORDS = [
    "gramin bank", "grameena bank", "regional rural bank",
    "kshetriya gramin", "pragathi krishna", "kaveri grameena",
    "canara bank (gramin)", "andhra pragathi", "chaitanya godavari",
    "bangiya gramin", "paschim banga", "madhyanchal gramin",
    "baroda rajasthan", "rajasthan marudhara", "uttar bihar",
    "dakshin bihar", "jharkhand rajya gramin", "vananchal gramin",
    "chhattisgarh rajya gramin", "vidarbha konkan gramin",
]


def classify(tags: dict) -> str:
    """Return 'sfb', 'coop', 'rrb', or '' based on name/operator/brand tags."""
    text = " ".join(filter(None, [
        tags.get("name", ""),
        tags.get("operator", ""),
        tags.get("brand", ""),
    ])).lower()

    for kw in SFB_KEYWORDS:
        if kw in text:
            return "sfb"
    for kw in RRB_KEYWORDS:
        if kw in text:
            return "rrb"
    for kw in COOP_KEYWORDS:
        if kw in text:
            return "coop"
    return ""


def fetch_banks(lat: float, lng: float) -> list[dict]:
    """
    Single Overpass query: all amenity=bank within RADIUS_M.
    Returns list of tag dicts. Empty list on failure.
    """
    q = f"""[out:json][timeout:30];
(
  nwr["amenity"="bank"](around:{RADIUS_M},{lat},{lng});
);
out tags;"""

    data = urllib.parse.urlencode({"data": q}).encode()
    req  = urllib.request.Request(
        OVERPASS, data=data,
        headers={"User-Agent": "PaisaMap-ETL/2.0",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                j = json.loads(r.read())
            return [el.get("tags", {}) for el in j.get("elements", [])]
        except Exception as e:
            wait = 10 * (attempt + 1)
            print(f"    attempt {attempt+1} failed ({e}) — retry in {wait}s",
                  flush=True)
            time.sleep(wait)
    return []


def process_pincode(pc: str, lat: float, lng: float) -> dict:
    banks = fetch_banks(lat, lng)
    sfb = coop = rrb = 0
    for tags in banks:
        cat = classify(tags)
        if cat == "sfb":   sfb  += 1
        elif cat == "coop": coop += 1
        elif cat == "rrb":  rrb  += 1

    total   = sfb + coop + rrb
    density = round(total / AREA_KM2, 4)
    return {
        "pincode":             pc,
        "sfb_branches":        sfb,
        "coop_branches":       coop,
        "rrb_branches":        rrb,
        "fin_branches_total":  total,
        "fin_density_per_km2": density,
        "_bank_nodes_found":   len(banks),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume",  action="store_true",
                    help="Skip pincodes already written to output CSV")
    ap.add_argument("--dry-run", action="store_true",
                    help="Query but don't write CSV")
    args = ap.parse_args()

    coords = pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str})
    total  = len(coords)

    # Build resume set
    done: set[str] = set()
    if args.resume and OUT.exists():
        done = set(pd.read_csv(OUT, dtype={"pincode": str})["pincode"])
        print(f"Resuming: {len(done)}/{total} already done")

    results: list[dict] = []
    # Carry forward existing rows when resuming
    if args.resume and OUT.exists():
        results = pd.read_csv(OUT, dtype={"pincode": str}).to_dict("records")

    for i, row in coords.iterrows():
        pc  = str(row["pincode"])
        lat = float(row["lat"])
        lng = float(row["lng"])

        if pc in done:
            continue

        print(f"  [{i+1:>2}/{total}] {pc} ({lat:.4f},{lng:.4f})", end="  ", flush=True)
        res = process_pincode(pc, lat, lng)
        print(f"SFB={res['sfb_branches']} COOP={res['coop_branches']} "
              f"RRB={res['rrb_branches']}  "
              f"({res['_bank_nodes_found']} bank nodes found)")

        results.append(res)

        # Write incrementally — crash-safe
        if not args.dry_run:
            df_out = pd.DataFrame(results)
            df_out.drop(columns=["_bank_nodes_found"], errors="ignore")\
                  .to_csv(OUT, index=False)

        time.sleep(SLEEP_SEC)

    df_final = pd.DataFrame(results).drop(columns=["_bank_nodes_found"],
                                           errors="ignore")

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Wrote {len(df_final)} rows → {OUT}")

    print("\nTop 10 by fin_branches_total:")
    print(df_final.nlargest(10, "fin_branches_total")
          [["pincode","sfb_branches","coop_branches","rrb_branches","fin_density_per_km2"]]
          .to_string(index=False))

    print("\nCity totals:")
    for prefix, label in [("11","Delhi"), ("12","Gurgaon/HR"),
                           ("40","Mumbai"), ("56","Bengaluru")]:
        sub = df_final[df_final["pincode"].str.startswith(prefix)]
        if not sub.empty:
            print(f"  {label}: SFB={sub['sfb_branches'].sum()} "
                  f"COOP={sub['coop_branches'].sum()} "
                  f"RRB={sub['rrb_branches'].sum()}")


if __name__ == "__main__":
    main()
