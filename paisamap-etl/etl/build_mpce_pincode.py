#!/usr/bin/env python3
"""
build_mpce_pincode.py — Join HCES district MPCE onto pincodes

Reads:
  data/raw/hces_mpce.csv                    — 628 districts × mpce_combined
  data/reference/pincode_district_map.csv   — pincode → (district, state_name)
    -- hand-curated, ~101 pincodes, matched via DISTRICT_FIX below (primary path)
  data/reference/pincode_district_state_india.csv    — pincode → (postal district, state), pan-India
  data/reference/postal_to_hces_district_crosswalk.csv — postal district → hces_district
    -- covers any pincode outside the hand-curated map (fallback path)

Writes:
  data/raw/mpce_district.csv          — pincode + mpce_combined + hces_district + hces_state
  (hces_district/hces_state let ml_refinement.py group pincodes by real
  district instead of the crude 2-digit-prefix/state fallback it used before.)

The district name join is imperfect: HCES uses Census 2011 names while the
pincode map uses local/common names. A normalisation dict handles known
mismatches for the ~76 reference pincodes. Pincodes outside that hand-curated
set are joined via the pan-India postal-district crosswalk instead (lower
confidence — exact/verified-manual crosswalk rows only, fuzzy/unmatched rows
are dropped rather than risking a wrong district).
"""

from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW  = ROOT / "data" / "raw"
REF  = ROOT / "data" / "reference"
OUT  = RAW / "mpce_district.csv"

# District name fixes: (pincode_map_district.upper(), pincode_map_state.upper()) →
# (hces_district, hces_state) — only needed where exact match fails
DISTRICT_FIX = {
    # Delhi sub-districts (HCES uses short names, pincode map uses full names)
    ("CENTRAL DELHI",   "DELHI"): ("CENTRAL",   "DELHI"),
    ("SOUTH DELHI",     "DELHI"): ("SOUTH",     "DELHI"),
    ("NORTH DELHI",     "DELHI"): ("NORTH",     "DELHI"),
    ("SOUTH WEST DELHI","DELHI"): ("SOUTH WEST","DELHI"),
    ("WEST DELHI",      "DELHI"): ("WEST",      "DELHI"),
    ("EAST DELHI",      "DELHI"): ("EAST",      "DELHI"),
    ("NORTH WEST DELHI","DELHI"): ("NORTH WEST","DELHI"),
    ("NORTH EAST DELHI","DELHI"): ("NORTH EAST","DELHI"),
    ("SHAHDARA",        "DELHI"): ("EAST",      "DELHI"),   # Shahdara is part of East Delhi
    ("NEW DELHI",       "DELHI"): ("NEW DELHI", "DELHI"),
    # Haryana
    ("GURUGRAM",        "HARYANA"): ("GURGAON",          "HARYANA"),
    # Maharashtra — HCES has no separate "Mumbai City" district
    ("MUMBAI CITY",     "MAHARASHTRA"): ("MUMBAI SUBURBAN", "MAHARASHTRA"),
    ("MUMBAI SUBURBAN", "MAHARASHTRA"): ("MUMBAI SUBURBAN", "MAHARASHTRA"),
    ("KHED",            "MAHARASHTRA"): ("RATNAGIRI",       "MAHARASHTRA"),
    ("HAVELI SUBDISTRICT","MAHARASHTRA"):("PUNE",           "MAHARASHTRA"),
    # Karnataka
    ("BENGALURU URBAN", "KARNATAKA"): ("BANGALORE", "KARNATAKA"),
    ("BENGALURU RURAL", "KARNATAKA"): ("BANGALORE", "KARNATAKA"),
    # Punjab
    ("LUDHIANA (WEST) TAHSIL","PUNJAB"): ("LUDHIANA", "PUNJAB"),
    ("LUDHIANA",        "PUNJAB"): ("LUDHIANA", "PUNJAB"),
}


def load_hces() -> pd.DataFrame:
    hces = pd.read_csv(RAW / "hces_mpce.csv")
    hces["state_u"]    = hces["state"].str.upper().str.strip()
    hces["district_u"] = hces["district"].str.upper().str.strip()
    return hces[["state_u", "district_u", "mpce_combined", "mpce_rural", "mpce_urban", "ppi_signal"]]


def load_pincode_map() -> pd.DataFrame:
    pdf = pd.read_csv(REF / "pincode_district_map.csv", dtype={"pincode": str})
    pdf["state_u"]    = pdf["state_name"].str.upper().str.strip()
    pdf["district_u"] = pdf["district"].str.upper().str.strip()
    return pdf[["pincode", "district", "state_name", "state_u", "district_u"]]


def apply_fixes(pdf: pd.DataFrame) -> pd.DataFrame:
    """Replace district/state keys with HCES equivalents where needed."""
    pdf = pdf.copy()
    for (d_key, s_key), (d_hces, s_hces) in DISTRICT_FIX.items():
        mask = (pdf["district_u"] == d_key) & (pdf["state_u"] == s_key)
        pdf.loc[mask, "district_u"] = d_hces
        pdf.loc[mask, "state_u"]    = s_hces
    return pdf


def build_primary(hces: pd.DataFrame) -> pd.DataFrame:
    """Hand-curated pincode_district_map.csv join (existing, ~101 pincodes)."""
    pdf = load_pincode_map()
    pdf = apply_fixes(pdf)
    merged = pdf.merge(hces, on=["state_u", "district_u"], how="left")
    matched = merged["mpce_combined"].notna().sum()
    print(f"  Primary (hand-curated map): matched {matched}/{len(merged)} pincodes")
    unmatched = merged[merged["mpce_combined"].isna()][["district", "state_name"]].drop_duplicates()
    if not unmatched.empty:
        print("  Unmatched districts (will fall through to pan-India path):")
        for _, r in unmatched.iterrows():
            print(f"    {r['state_name']}: {r['district']}")
    merged["hces_district"] = merged["district_u"]
    merged["hces_state"]    = merged["state_u"]
    return merged[["pincode", "mpce_combined", "mpce_rural", "mpce_urban",
                    "ppi_signal", "hces_district", "hces_state"]]


def build_pan_india(hces: pd.DataFrame, exclude_pincodes: set) -> pd.DataFrame:
    """
    Pan-India fallback for pincodes outside the hand-curated map, via the
    postal-district crosswalk built 2026-07-15. Only exact/verified_manual
    crosswalk rows are used -- fuzzy/unmatched rows are dropped rather than
    risk joining a pincode to the wrong district's MPCE.
    """
    pin_path = REF / "pincode_district_state_india.csv"
    cw_path  = REF / "postal_to_hces_district_crosswalk.csv"
    if not pin_path.exists() or not cw_path.exists():
        return pd.DataFrame(columns=["pincode", "mpce_combined", "mpce_rural",
                                      "mpce_urban", "ppi_signal", "hces_district", "hces_state"])

    pin = pd.read_csv(pin_path, dtype={"pincode": str})
    pin = pin[~pin["pincode"].isin(exclude_pincodes)]

    cw = pd.read_csv(cw_path)
    cw = cw[cw["match_method"].isin(["exact", "verified_manual"])]
    cw = cw.rename(columns={"state": "state_name_u"})

    pin = pin.copy()
    pin["state_name_u"] = pin["state_name"].str.upper().str.strip()

    merged = pin.merge(
        cw[["postal_district", "state_name_u", "hces_district"]],
        left_on=["district", "state_name_u"], right_on=["postal_district", "state_name_u"],
        how="inner",
    )
    merged["state_u"] = merged["state_name"].str.upper().str.strip()
    merged["district_u"] = merged["hces_district"]
    merged = merged.merge(hces, on=["state_u", "district_u"], how="left")
    matched = merged["mpce_combined"].notna().sum()
    print(f"  Pan-India crosswalk path: matched {matched}/{len(merged)} candidate pincodes "
          f"({len(pin)} outside hand-curated map)")

    merged["hces_state"] = merged["state_u"]
    out = merged[["pincode", "mpce_combined", "mpce_rural", "mpce_urban",
                   "ppi_signal", "hces_district", "hces_state"]].dropna(subset=["mpce_combined"])
    return out.drop_duplicates(subset="pincode")


def build() -> pd.DataFrame:
    hces = load_hces()
    primary = build_primary(hces)
    pan_india = build_pan_india(hces, exclude_pincodes=set(primary["pincode"]))

    out = pd.concat([primary, pan_india], ignore_index=True)
    out.columns = ["pincode", "mpce_combined", "mpce_rural", "mpce_urban",
                    "hces_ppi", "hces_district", "hces_state"]
    print(f"  Total: {out['mpce_combined'].notna().sum()}/{len(out)} pincodes "
          f"matched to real HCES district MPCE")
    return out


def main():
    print("Building pincode-level MPCE from HCES district data...")
    result = build()
    result.to_csv(OUT, index=False)
    print(f"  → {OUT}  ({len(result)} pincodes)")
    print("\nSample (top MPCE pincodes):")
    top = result.dropna(subset=["mpce_combined"]).sort_values("mpce_combined", ascending=False)
    print(top.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
