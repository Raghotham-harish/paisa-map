#!/usr/bin/env python3
"""
fetch_pincode_directory.py — real India Post office names per pincode, to
replace the "District, State · pincode" disambiguation hack (59882f1) with
actual place names.

That earlier fix made names unique (no two pincodes shared a label
anymore) but wasn't actually informative — every pincode in, say, Jaipur
district still showed as "Jaipur, Rajasthan · <number>", which reads as
the same place repeated with a cryptic suffix, not as distinct localities.
Found live via user screenshots 2026-08-08.

Source: data.gov.in's "All India Pincode Directory" (Ministry of
Communications / Department of Posts) — 155,570 real post office records,
same api.data.gov.in + public sample key pattern already used elsewhere in
this project (fetch_commercial.py etc.). One pincode can have multiple post
offices; picks one representative name per pincode, preferring a Head
Office, then a Sub Office, then a Branch Office (delivery-status offices
preferred over non-delivery), and strips the " S.O"/" H.O"/" B.O" suffix
for a clean display name matching the style of real enriched locality
names already in the app (e.g. "Chandni Chowk", not "Chandni Chowk S.O").

Output: data/reference/pincode_office_names.csv — pincode, name

Usage:
  cd paisamap-etl && python3 etl/fetch_pincode_directory.py
"""

from pathlib import Path
import re

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
REF  = ROOT / "data" / "reference"

API_URL = "https://api.data.gov.in/resource/6176ee09-3d56-4a3b-8115-21841576b2f6"
API_KEY = "579b464db66ec23bdd000001cdc3b564546246a772a26393094f5645"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PaisaMap-ETL/1.0)"}
BATCH = 5000

# Lower = preferred representative office for a pincode with multiple offices
OFFICE_TYPE_RANK = {"H.O": 0, "S.O": 1, "B.O": 2}


def fetch_all() -> pd.DataFrame:
    first = requests.get(API_URL, params={"api-key": API_KEY, "format": "json", "limit": 1},
                          headers=HEADERS, timeout=30).json()
    total = int(first["total"])
    print(f"Total records: {total}")

    rows = []
    offset = 0
    while offset < total:
        r = requests.get(API_URL, params={"api-key": API_KEY, "format": "json",
                                            "limit": BATCH, "offset": offset},
                          headers=HEADERS, timeout=60)
        r.raise_for_status()
        batch = r.json().get("records", [])
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        print(f"  fetched {offset}/{total}")
    return pd.DataFrame(rows)


def clean_name(officename: str, officetype: str) -> str:
    """'Chakragaon S.O' -> 'Chakragaon', 'Patel Nagar S.O (Central Delhi)' ->
    'Patel Nagar' — strips the office-type suffix and any trailing
    disambiguating parenthetical India Post itself adds when multiple
    offices share a name (found affecting 1,800/19,238 names 2026-08-08),
    so the result reads like the real locality names already used
    elsewhere in the app (e.g. "Chandni Chowk")."""
    name = officename
    suffix = f" {officetype}"
    if name.endswith(suffix):
        name = name[: -len(suffix)]
    # Strip a trailing parenthetical, then any remaining trailing office-type
    # marker, then a parenthetical again in case it was sandwiched between
    # two office-type-like tokens — order matters, these can nest either way.
    for _ in range(2):
        name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
        name = re.sub(r"\s+[A-Z]\.?O\.?\s*$", "", name).strip()
    return name or officename


def main():
    print("Fetching All India Pincode Directory...")
    df = fetch_all()
    df["pincode"] = df["pincode"].astype(str).str.strip()
    df = df[df["pincode"].str.match(r"^\d{6}$")]
    print(f"Valid 6-digit pincode rows: {len(df)}")

    df["type_rank"] = df["officetype"].map(OFFICE_TYPE_RANK).fillna(3)
    df["delivery_rank"] = (df["deliverystatus"] != "Delivery").astype(int)
    df = df.sort_values(["pincode", "delivery_rank", "type_rank"])
    best = df.drop_duplicates(subset="pincode", keep="first").copy()

    best["name"] = best.apply(lambda r: clean_name(r["officename"], r["officetype"]), axis=1)
    out = best[["pincode", "name", "districtname", "statename"]].rename(
        columns={"districtname": "district", "statename": "state"})

    dest = REF / "pincode_office_names.csv"
    out.to_csv(dest, index=False)
    print(f"\nWrote {len(out)} pincodes -> {dest}")
    print(out.head(10).to_string())


if __name__ == "__main__":
    main()
