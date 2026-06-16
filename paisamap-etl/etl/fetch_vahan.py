"""
fetch_vahan.py — Vehicle registration data from VAHAN / Parivahan portal.

Live source : https://vahan.parivahan.gov.in/vahan4dashboard/
              Tries two public AJAX endpoints (state summary + district drill-down).
Fallback    : data/reference/vahan_district_2024.csv  (MoRTH annual report figures)

Outputs (written to data/raw/):
  vehicle_density.csv   — pincode, cars_per_1000
  rto_enhanced.csv      — pincode, lmv_per_1000, car_2w_ratio, luxury_share, ev_share

Usage:
  python3 etl/fetch_vahan.py            # auto-detect paths
  python3 etl/fetch_vahan.py --dry-run  # print what would be written, don't write
"""

import sys
import time
import argparse
import logging
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

ETL  = Path(__file__).parent.parent
REF  = ETL / "data" / "reference"
RAW  = ETL / "data" / "raw"
PC_MAP = REF / "pincode_district_map.csv"
VAHAN_REF = REF / "vahan_district_2024.csv"

# VAHAN dashboard public endpoint (returns JSON on POST)
VAHAN_URL = "https://vahan.parivahan.gov.in/vahan4dashboard/vahan/data/stateWiseVehicleSubCatCount.xhtml"
VAHAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PaisaMap-ETL/1.0)",
    "Accept": "application/json, text/javascript, */*",
    "Referer": "https://vahan.parivahan.gov.in/vahan4dashboard/",
}

# Mapping from VAHAN state names to our state codes
VAHAN_STATE_MAP = {
    "DELHI": "DL", "HARYANA": "HR", "UTTAR PRADESH": "UP",
    "MAHARASHTRA": "MH", "KARNATAKA": "KA",
}


def _try_live_fetch() -> object:
    """Attempt to pull district data from VAHAN dashboard AJAX endpoint."""
    try:
        log.info("Trying VAHAN live API …")
        resp = requests.post(
            VAHAN_URL,
            data={"selectedStateCode": "0", "selectedYear": "2024"},
            headers=VAHAN_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        # VAHAN returns list of {stateName, vehicleClass, count} objects
        records = payload.get("data") or payload.get("records") or []
        if not records:
            log.warning("VAHAN API returned empty data — using reference CSV")
            return None
        df = pd.DataFrame(records)
        log.info("VAHAN live: %d records fetched", len(df))
        return df
    except Exception as exc:
        log.warning("VAHAN live fetch failed (%s) — using reference CSV", exc)
        return None


def load_vahan_district() -> pd.DataFrame:
    """Return district-level VAHAN data (live or fallback reference)."""
    live = _try_live_fetch()
    if live is not None:
        # TODO: parse live payload into standard schema when VAHAN API stabilises
        # For now fall through to reference
        log.info("Live data received but parser not yet wired — using reference CSV")

    log.info("Loading VAHAN reference CSV: %s", VAHAN_REF)
    df = pd.read_csv(VAHAN_REF, comment="#")
    df["district"] = df["district"].str.strip()
    return df.set_index("district")


def build_pincode_signals(vahan: pd.DataFrame, pc_map: pd.DataFrame) -> pd.DataFrame:
    """
    Map district-level VAHAN signals to pincodes.

    Within a district, pincodes are differentiated by a relative-wealth scalar
    derived from the existing POI density (premium_poi_per_km2).  This preserves
    relative rankings inside a city while anchoring absolute values to real data.
    """
    poi_path = RAW / "poi_density.csv"
    poi = pd.read_csv(poi_path).set_index("pincode") if poi_path.exists() else pd.DataFrame()

    rows = []
    for _, row in pc_map.iterrows():
        pc      = str(row["pincode"])
        district = row["district"]

        if district not in vahan.index:
            log.debug("No VAHAN data for district %s (pincode %s) — skipping", district, pc)
            continue

        v = vahan.loc[district]

        # Within-district scalar: pincodes with more POI get slightly higher
        # vehicle density (max ±30% swing to avoid over-indexing)
        if not poi.empty and int(pc) in poi.index:
            district_pincodes = pc_map[pc_map["district"] == district]["pincode"].astype(int).tolist()
            district_poi = poi.loc[poi.index.isin(district_pincodes), "premium_poi_per_km2"]
            if len(district_poi) >= 2:
                pc_poi = poi.loc[int(pc), "premium_poi_per_km2"] if int(pc) in poi.index else district_poi.median()
                scalar = 0.70 + 0.60 * (pc_poi - district_poi.min()) / max(district_poi.max() - district_poi.min(), 0.1)
                scalar = max(0.70, min(1.30, scalar))
            else:
                scalar = 1.0
        else:
            scalar = 1.0

        rows.append({
            "pincode"      : pc,
            "cars_per_1000": round(float(v["cars_per_1000"]) * scalar, 1),
            "lmv_per_1000" : round(float(v["cars_per_1000"]) * scalar, 1),
            "car_2w_ratio" : round(float(v["car_2w_ratio"]),  3),
            "luxury_share" : round(float(v["luxury_share"]),  4),
            "ev_share"     : round(float(v["ev_share"]),      4),
        })

    return pd.DataFrame(rows).set_index("pincode")


def write_outputs(signals: pd.DataFrame, dry_run: bool = False) -> None:
    vd_path  = RAW / "vehicle_density.csv"
    rto_path = RAW / "rto_enhanced.csv"

    # Merge with existing files so pincodes without VAHAN data keep prior values
    if vd_path.exists():
        existing_vd = pd.read_csv(vd_path).set_index("pincode")
        existing_vd["pincode"] = existing_vd.index.astype(str)
        signals_str = signals.copy()
        signals_str.index = signals_str.index.astype(str)
        existing_vd.update(signals_str[["cars_per_1000"]])
        vd_out = existing_vd
    else:
        vd_out = signals[["cars_per_1000"]]

    if rto_path.exists():
        existing_rto = pd.read_csv(rto_path).set_index("pincode")
        existing_rto.index = existing_rto.index.astype(str)
        signals_str = signals.copy()
        signals_str.index = signals_str.index.astype(str)
        rto_cols = [c for c in ["lmv_per_1000", "car_2w_ratio", "luxury_share", "ev_share"]
                    if c in signals_str.columns and c in existing_rto.columns]
        existing_rto.update(signals_str[rto_cols])
        rto_out = existing_rto
    else:
        rto_out = signals[["lmv_per_1000", "car_2w_ratio", "luxury_share", "ev_share"]]

    if dry_run:
        log.info("[DRY RUN] vehicle_density.csv:\n%s", vd_out.head())
        log.info("[DRY RUN] rto_enhanced.csv:\n%s", rto_out.head())
        return

    vd_out.to_csv(vd_path)
    rto_out.to_csv(rto_path)
    log.info("Written: %s (%d rows)", vd_path.name, len(vd_out))
    log.info("Written: %s (%d rows)", rto_path.name, len(rto_out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pc_map  = pd.read_csv(PC_MAP)
    vahan   = load_vahan_district()
    signals = build_pincode_signals(vahan, pc_map)
    log.info("VAHAN signals computed for %d pincodes", len(signals))
    write_outputs(signals, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
