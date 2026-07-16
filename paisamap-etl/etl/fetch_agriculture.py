#!/usr/bin/env python3
"""
fetch_agriculture.py — Cropping intensity (%) by pincode, from MOSPI's
Statistical Year Book India, Table 8.1 (Pattern of Land Utilisation).

Source: user-downloaded "Table-8.1.xlsx" from
  https://www.mospi.gov.in/publications-reports/innerpage/2688
("Statistical Year Book India" publications page — JS-rendered, couldn't
confirm programmatically whether a more recent edition exists; the
downloaded file's own data only runs through 2012-13. That's a real
vintage limitation, flagged here rather than glossed over — a fresher
edition, if the MOSPI portal has one, would be a meaningful accuracy
upgrade. Not yet checked.)

cropping_intensity_pct = Total Cropped Area / Net Area Sown x 100 — the
standard Indian agricultural statistic for how many times land is
effectively cropped per year (100% = no multiple cropping; Punjab/Haryana
run ~180-190% from irrigation-fed double/triple cropping, desert states
like Rajasthan sit around 135%). Read as agricultural intensity/output
proxy, most relevant for rural/peri-urban pincodes — not a claim about
purchasing power's direction, same as every other proxy signal here.

The sheet's own header text is misleading if taken literally: row 5 labels
column 15 as "Net area Sown" and column 16 as "Total Cropped Area", but
those numbers don't hold up against known facts (Punjab's is a famous
~190% cropping intensity, not 6000%+ as literal columns 15/16 would give).
There's an off-by-one starting from a duplicated/corrupted header cell
earlier in the sheet. Verified empirically instead: columns 16 and 17
(0-indexed) give Punjab 189.6%, Haryana 181.5%, West Bengal 185.9%,
Rajasthan 137.0% — all match well-known real-world cropping-intensity
figures, confirming that pair is the real Net Sown / Total Cropped one.

State-level only (no district breakdown in this source) — applied
uniformly to every pincode in a state, same approach as
fetch_education.py's state-level fallback path. No population
normalization needed since this is already a ratio.

This data predates Telangana's 2014 split from Andhra Pradesh — undivided
AP's figure is applied to Telangana too as a rough approximation, flagged
in the code, same "nearest available, documented" spirit as the Sikkim
district-reorganization workaround in fetch_rbi_bsr.py's
CENSUS_DISTRICT_ALIASES.

Output: data/raw/agriculture.csv — pincode, cropping_intensity_pct

Usage:
  python3 etl/fetch_agriculture.py --xlsx /path/to/Table-8.1.xlsx
  python3 etl/fetch_agriculture.py --xlsx /path/to/Table-8.1.xlsx --dry-run
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from fetch_rbi_bsr import _norm_state  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
REF  = ROOT / "data" / "reference"
RAW  = ROOT / "data" / "raw"
PINCODE_REF = REF / "pincode_district_state_india.csv"
COORDS      = RAW / "pincode_coords.csv"

# 0-indexed columns confirmed empirically — see docstring.
NET_SOWN_COL      = 16
TOTAL_CROPPED_COL = 17

# Aliases specific to this source's state-name spelling/typos — one-off
# enough (typos, not generic variants) that they stay local rather than
# joining the shared STATE_NAME_ALIASES in fetch_rbi_bsr.py.
STATE_ALIASES = {
    "ANDMAN AND NICOBAR ISLAND": "ANDAMAN AND NICOBAR",
    "DADAR AND NAGAR HAVELI": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
}


def load_state_cropping_intensity(xlsx_path: Path) -> pd.Series:
    """state_norm -> cropping intensity %, latest available year per state."""
    df = pd.read_excel(xlsx_path, sheet_name="Statewise", header=None)

    results = {}
    current_state = None
    for _, row in df.iterrows():
        if pd.isna(row[1]):
            label = row[0]
            if pd.notna(label) and not str(label).strip().upper().startswith(("AGRICULTURE", "TABLE")):
                current_state = str(label).strip()
            continue
        if current_state is None:
            continue
        net_sown, total_cropped = row[NET_SOWN_COL], row[TOTAL_CROPPED_COL]
        if pd.isna(net_sown) or pd.isna(total_cropped) or net_sown == 0:
            continue
        # Overwritten every year row in the block -> ends on the latest year available
        results[current_state] = round(total_cropped / net_sown * 100, 1)

    log.info("Parsed cropping intensity for %d states/UTs (latest available year each)", len(results))

    state_norm = {}
    for name, val in results.items():
        norm = _norm_state(name)
        norm = STATE_ALIASES.get(norm, norm)
        state_norm[norm] = val

    if "ANDHRA PRADESH" in state_norm and "TELANGANA" not in state_norm:
        state_norm["TELANGANA"] = state_norm["ANDHRA PRADESH"]
        log.info("Approximated TELANGANA from pre-bifurcation ANDHRA PRADESH figure (%.1f%%)",
                 state_norm["ANDHRA PRADESH"])

    return pd.Series(state_norm, name="cropping_intensity_pct")


def build_pincode_output(state_intensity: pd.Series) -> pd.DataFrame:
    known = set(pd.read_csv(COORDS, dtype={"pincode": str})["pincode"])
    pc_map = pd.read_csv(PINCODE_REF, dtype={"pincode": str})
    pc_map = pc_map[pc_map["pincode"].isin(known)].copy()
    pc_map["state_norm"] = pc_map["state_name"].apply(_norm_state)

    matched_states = set(state_intensity.index) & set(pc_map["state_norm"].unique())
    log.info("Matched %d/%d states with pincode coverage", len(matched_states), pc_map["state_norm"].nunique())

    pc_map["cropping_intensity_pct"] = pc_map["state_norm"].map(state_intensity)
    out = pc_map.dropna(subset=["cropping_intensity_pct"])[["pincode", "cropping_intensity_pct"]]
    return out.drop_duplicates("pincode").set_index("pincode")


def write_output(out: pd.DataFrame, dry_run: bool = False) -> None:
    out_path = RAW / "agriculture.csv"
    if dry_run:
        log.info("[DRY RUN] agriculture.csv (%d rows):\n%s", len(out), out.head(10))
        return
    out.to_csv(out_path)
    log.info("Written: %s (%d rows)", out_path.name, len(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True, help="MOSPI Table-8.1.xlsx (Statewise sheet) — see module docstring")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    state_intensity = load_state_cropping_intensity(Path(args.xlsx))
    out = build_pincode_output(state_intensity)
    log.info("cropping_intensity_pct computed for %d pincodes", len(out))
    write_output(out, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
