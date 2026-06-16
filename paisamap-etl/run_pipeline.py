"""
run_pipeline.py — PaisaMap full ETL + ML pipeline runner

Run order:
  1. fetch_rto_enhanced.py   — RTO vehicle data: car/2W ratio, luxury share, EV share
  2. fetch_itr.py            — ITR filer rates per pincode (CBDT AY2022-23)
  3. fetch_financial.py      — SFB / cooperative / RRB branch counts (Overpass, slow)
  4. pipeline.py             — Fixed-weight composite PPI (baseline)
  5. ml_refinement.py        — Multi-model ML refinement of PPI
  6. update_app_data.py      — Write ppi_map_data.csv for the web app

Steps 1-3 fetch / compute raw signals.
Step 4 produces the traditional weighted PPI.
Step 5 produces the ML-refined PPI using property-rate anchoring + spatial smoothing.
Step 6 writes the app data from the ML output (or falls back to step 4 if ML is skipped).

Usage:
    python3 run_pipeline.py [--skip-fetch] [--skip-ml] [--only STEP]

    --skip-fetch   Skip the slow HTTP fetch steps (1, 2, 3). Requires cached data.
    --skip-ml      Skip ML refinement (step 5). App uses fixed-weight PPI instead.
    --only STEP    Run only the named step: rto | itr | financial | pipeline | ml | app
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ETL  = ROOT / "etl"


def run(name: str, script: str, desc: str):
    print(f"\n{'='*60}")
    print(f"  {name}: {desc}")
    print(f"{'='*60}")
    t0 = time.time()
    result = subprocess.run(
        [sys.executable, str(ETL / script)],
        cwd=str(ROOT),
        capture_output=False,
    )
    elapsed = time.time() - t0
    status = "OK" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    print(f"\n  → {status} in {elapsed:.0f}s")
    if result.returncode != 0:
        raise SystemExit(f"Pipeline stopped at step: {name}")


def update_app_data(use_ml: bool = True):
    """Write ppi_map_data.csv to the app from ML-refined or original PPI output."""
    import pandas as pd
    print(f"\n{'='*60}")
    print(f"  APP DATA: Writing ppi_map_data.csv ({'ML' if use_ml else 'original'})")
    print(f"{'='*60}")

    out_dir = ROOT / "data" / "output"
    coords  = pd.read_csv(ROOT / "data" / "raw" / "pincode_coords.csv",
                          dtype={"pincode": str}).set_index("pincode")

    if use_ml and (out_dir / "ppi_ml_refined.csv").exists():
        src = pd.read_csv(out_dir / "ppi_ml_refined.csv", dtype={"pincode": str}).set_index("pincode")
        ppi_col, inc_col = "ppi_ml", "est_monthly_income_hh"
    else:
        src = pd.read_csv(out_dir / "ppi_pincode.csv", dtype={"pincode": str}).set_index("pincode")
        ppi_col, inc_col = "ppi", "est_monthly_income_hh"

    app_dir = ROOT.parent / "data" / "output"
    app_dir.mkdir(parents=True, exist_ok=True)

    out = pd.DataFrame({
        "name":   src.get("name", pd.Series(dtype=str)),
        "lat":    coords.reindex(src.index)["lat"],
        "lng":    coords.reindex(src.index)["lng"],
        "ppi":    src[ppi_col],
        "income": src[inc_col],
    })
    out.index.name = "pincode"
    out = out.sort_values("ppi", ascending=False)
    dest = app_dir / "ppi_map_data.csv"
    out.to_csv(dest)
    print(f"  Wrote {len(out)} rows → {dest}")
    print(out[["name", "ppi", "income"]].head(5).to_string())


def main():
    args = sys.argv[1:]
    skip_fetch = "--skip-fetch" in args
    skip_ml    = "--skip-ml"    in args
    only       = None
    if "--only" in args:
        idx  = args.index("--only")
        only = args[idx + 1] if idx + 1 < len(args) else None

    steps = [
        ("rto",       "fetch_rto_enhanced.py", "RTO enhanced signals (car/2W, luxury, EV)"),
        ("itr",       "fetch_itr.py",           "ITR filer rates (CBDT AY2022-23)"),
        ("financial", "fetch_financial.py",      "SFB / coop / RRB branches (Overpass — slow)"),
        ("pipeline",  "pipeline.py",             "Fixed-weight composite PPI"),
        ("ml",        "ml_refinement.py",        "Multi-model ML refinement"),
    ]

    for key, script, desc in steps:
        if only and key != only:
            continue
        if skip_fetch and key in ("rto", "itr", "financial"):
            print(f"  · skipping {key} (--skip-fetch)")
            continue
        if skip_ml and key == "ml":
            print(f"  · skipping ml (--skip-ml)")
            continue
        run(key, script, desc)

    if not only or only == "app":
        update_app_data(use_ml=not skip_ml)

    print("\n✓ Pipeline complete.")


if __name__ == "__main__":
    main()
