"""
fetch_financial.py
Queries the Overpass API to count three categories of financial-inclusion
institutions within 8 km of each of our 38 pincodes:

  1. Small Finance Banks (SFBs): Ujjivan, Equitas, Au Small Finance, Jana,
     Suryoday, ESAF, Utkarsh, Capital Small Finance, Fincare, North East SFB
  2. Cooperative / Urban Co-operative Banks: Saraswat, Cosmos, SVC, TJSB,
     NKGSB, Abhyudaya, Janata, Mahesh, Sahakari
  3. Regional Rural Banks (RRBs / Gramin Banks): any branch with "Gramin" or
     "Grameena" or "Regional Rural" in its name

Output: data/raw/financial_inclusion.csv
  pincode, sfb_branches, coop_branches, rrb_branches, fin_branches_total,
  fin_density_per_km2

Note: SFBs and cooperative banks primarily serve the middle and lower-middle
income segments. In the PPI composite they function similarly to poi_density
— a proxy for economic activity and financial-services penetration — so they
are included as a positive contributor with a small weight.
"""

import json
import math
import time
import urllib.request
import urllib.parse
import pandas as pd

OVERPASS = "https://overpass-api.de/api/interpreter"
RADIUS_M = 8_000       # 8 km — small enough to avoid timeouts
AREA_KM2 = math.pi * (RADIUS_M / 1000) ** 2    # ~201 km²

SLEEP_SEC = 3          # polite pause between per-pincode requests

# ── Institution name patterns ────────────────────────────────────────────────
SFB_PATTERN  = (
    "Ujjivan|Equitas|Au Small Finance|Au Small|Jana Small Finance|Jana SFB|"
    "Suryoday|ESAF|Utkarsh|Capital Small Finance|Fincare|North East Small"
)

COOP_PATTERN = (
    "Saraswat|Cosmos Co-op|Cosmos Bank|SVC Bank|TJSB|NKGSB|Abhyudaya|"
    "Janata Sahakari|Mahesh Bank|Sahakari Bank|Co-operative Bank|"
    "Urban Co-op|Cooperative Bank"
)

RRB_PATTERN  = "Gramin Bank|Grameena Bank|Regional Rural Bank|Kshetriya"


def overpass_count(lat, lng, pattern):
    """Return branch count matching `pattern` within RADIUS_M of (lat, lng)."""
    q = f"""[out:json][timeout:25];
(
  nwr["amenity"="bank"]["name"~"{pattern}",i](around:{RADIUS_M},{lat},{lng});
  nwr["amenity"="bank"]["operator"~"{pattern}",i](around:{RADIUS_M},{lat},{lng});
);
out count;"""
    try:
        data = urllib.parse.urlencode({"data": q}).encode()
        req = urllib.request.Request(
            OVERPASS, data=data,
            headers={"User-Agent": "PaisaMap-ETL/2.0",
                     "Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read())
        return int(j["elements"][0]["tags"]["total"])
    except Exception as e:
        print(f"      WARN Overpass error: {e}")
        return None


def main():
    coords = pd.read_csv("data/raw/pincode_coords.csv", dtype={"pincode": str})
    results = []

    total = len(coords)
    for i, row in coords.iterrows():
        pc  = row["pincode"]
        lat = row["lat"]
        lng = row["lng"]
        print(f"  [{i+1:>2}/{total}] {pc} ({lat}, {lng})")

        sfb  = overpass_count(lat, lng, SFB_PATTERN);  time.sleep(SLEEP_SEC)
        coop = overpass_count(lat, lng, COOP_PATTERN); time.sleep(SLEEP_SEC)
        rrb  = overpass_count(lat, lng, RRB_PATTERN);  time.sleep(SLEEP_SEC)

        print(f"         SFB={sfb}  COOP={coop}  RRB={rrb}")

        # Treat None (timeout) as 0 — conservative undercount, noted in warnings
        sfb  = sfb  if sfb  is not None else 0
        coop = coop if coop is not None else 0
        rrb  = rrb  if rrb  is not None else 0

        total_fin = sfb + coop + rrb
        density   = round(total_fin / AREA_KM2, 4)
        results.append({
            "pincode":            pc,
            "sfb_branches":       sfb,
            "coop_branches":      coop,
            "rrb_branches":       rrb,
            "fin_branches_total": total_fin,
            "fin_density_per_km2": density,
        })

    out = pd.DataFrame(results)
    out.to_csv("data/raw/financial_inclusion.csv", index=False)
    print(f"\nWrote {len(out)} rows → data/raw/financial_inclusion.csv")

    print("\nTop 10 by fin_branches_total:")
    print(out.nlargest(10, "fin_branches_total")[
        ["pincode","sfb_branches","coop_branches","rrb_branches","fin_density_per_km2"]
    ].to_string(index=False))

    print("\nCity totals:")
    for prefix, label in [("11","Delhi/NCR"), ("40","Mumbai"), ("56","Bengaluru")]:
        sub = out[out["pincode"].str.startswith(prefix)]
        print(f"  {label}: SFB={sub['sfb_branches'].sum()} "
              f"COOP={sub['coop_branches'].sum()} RRB={sub['rrb_branches'].sum()}")


if __name__ == "__main__":
    main()
