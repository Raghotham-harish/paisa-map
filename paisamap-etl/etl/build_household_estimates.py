#!/usr/bin/env python3
"""
build_household_estimates.py — per-pincode household-count estimate, for the
map workspace's "Households in view" KPI tile.

Deliberately a standalone companion file (data/output/pincode_households.csv),
NOT a new column appended to ppi_map_data.csv — that file is rewritten from
scratch by five different live scripts (batch_enrich_hces.py, enrich_single.py,
ml_refinement.py, expand_core_idw.py, merge_coords.py; see
project_ppi_map_schema_incident.md in the assistant's own memory for a real
schema-drift incident from touching that file's shape), so adding a column
there means keeping five writers in lockstep. A separate best-effort file the
frontend merges in (same pattern as build_broad_coverage.py) carries zero risk
to the core pipeline: if this script is never run, or the fetch 404s, the
household KPI just doesn't render — nothing else is affected.

Reuses _forecast_model.estimate_households(...) — the exact same household-
count method the Forecast feature already relies on — rather than
re-deriving a second, potentially-divergent formula here. All three inputs
it needs already have a loader:
  - _signals_data.load_ppi_signals_rows() — density-proxy columns per pincode
  - _signals_data.load_geography()        — pincode -> district/state
  - _forecast_model.load_district_population() — district -> population

Output: data/output/pincode_households.csv, columns: pincode,households

Usage:
  cd paisamap-etl && python3 etl/build_household_estimates.py
"""

from pathlib import Path
import sys

ETL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ETL_DIR))

import _signals_data
import _forecast_model

ROOT = ETL_DIR.parent
OUT = ROOT / "data" / "output"
APP_OUT = ROOT.parent / "data" / "output" / "pincode_households.csv"


def build():
    print("Loading signals rows, geography and district population…")
    rows_by_pincode, source = _signals_data.load_ppi_signals_rows()
    geography = _signals_data.load_geography()
    district_pop = _forecast_model.load_district_population()
    print(f"  {len(rows_by_pincode)} pincodes ({source}), "
          f"{len(geography)} with geography, {len(district_pop)} districts with population")

    households = _forecast_model.estimate_households(rows_by_pincode, geography, district_pop)
    print(f"  Estimated households for {len(households)} pincodes")
    return households


def main():
    households = build()

    OUT.mkdir(parents=True, exist_ok=True)
    APP_OUT.parent.mkdir(parents=True, exist_ok=True)

    def write(dest: Path):
        with open(dest, "w") as f:
            f.write("pincode,households\n")
            for pc, hh in sorted(households.items()):
                f.write(f"{pc},{round(hh)}\n")

    write(OUT / "pincode_households.csv")
    print(f"\nWrote {len(households)} rows -> {OUT / 'pincode_households.csv'}")
    write(APP_OUT)
    print(f"Synced -> {APP_OUT}")


if __name__ == "__main__":
    main()
