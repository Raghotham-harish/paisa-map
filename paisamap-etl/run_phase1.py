"""
run_phase1.py — Phase 1 real-data enrichment pipeline for PaisaMap.

Replaces 5 modelled signals with real government data:
  ● deposits_per_capita  →  RBI BSR 2023  (district bank deposits)
  ● filers_per_capita    →  CBDT ITR AY2023  (income tax return filers)
  ● cars_per_1000        →  VAHAN 2024  (vehicle registrations)
  ● ev_share             →  VAHAN 2024
  ● car_2w_ratio         →  VAHAN 2024

Pipeline steps:
  1. fetch_vahan.py       — updates vehicle_density.csv + rto_enhanced.csv
  2. fetch_rbi_bsr.py     — updates bank_deposits.csv
  3. fetch_cbdt_itr.py    — updates itr_filers.csv
  4. merge_real_signals.py — delta report + validation gate check
  5. ml_refinement.py     — re-runs ML with updated signals → ppi_ml_refined.csv
  6. Copies output to     ../data/output/ppi_map_data.csv  (served by the app)

Usage:
  cd paisamap-etl
  python3 run_phase1.py

Options:
  --dry-run       Run fetch steps in dry-run mode (print what would change, don't write)
  --skip-fetch    Skip fetch steps (re-use existing raw CSVs, jump to ML)
  --skip-ml       Run fetch + merge only, skip ML refinement (fast check)
  --excel-bsr     /path/to/bsr2.xlsx  (supply a downloaded RBI BSR Excel)
  --excel-cbdt    /path/to/cbdt.xlsx  (supply a downloaded CBDT statistics Excel)
"""

import sys
import subprocess
import argparse
import logging
import shutil
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

HERE    = Path(__file__).parent
ETL     = HERE / "etl"
DATA    = HERE / "data"
RAW     = DATA / "raw"
OUT     = DATA / "output"
APP_CSV = HERE.parent / "data" / "output" / "ppi_map_data.csv"

VENV_PY = HERE / "venv" / "bin" / "python3"
PYTHON  = str(VENV_PY) if VENV_PY.exists() else sys.executable


def run_step(script: Path, extra_args: list[str] = [], label: str = "") -> bool:
    label = label or script.name
    log.info("── %s ─────────────────────────────────────", label)
    cmd = [PYTHON, str(script)] + extra_args
    result = subprocess.run(cmd, cwd=str(HERE))
    if result.returncode != 0:
        log.error("Step FAILED: %s (exit %d)", label, result.returncode)
        return False
    log.info("Step OK: %s\n", label)
    return True


def copy_to_app(src: Path, dst: Path) -> None:
    if not src.exists():
        log.error("ML output not found at %s — cannot copy to app", src)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    row_count = sum(1 for _ in open(dst)) - 1
    log.info("App CSV updated: %s  (%d pincodes)", dst, row_count)


def print_summary(success: bool, gates_passed=None) -> None:
    print("\n" + "═" * 60)
    if success:
        print("  Phase 1 pipeline complete ✓")
        print("  5 signals now backed by real government data:")
        print("    · deposits_per_capita  → RBI BSR 2023")
        print("    · filers_per_capita    → CBDT ITR AY2023")
        print("    · cars_per_1000        → VAHAN 2024")
        print("    · ev_share             → VAHAN 2024")
        print("    · car_2w_ratio         → VAHAN 2024")
        if gates_passed is not None:
            print(f"  Validation gates: {gates_passed}/10 passed")
        print(f"  App CSV: {APP_CSV}")
        print("\n  Next: commit data/output/ppi_map_data.csv and push to GitHub")
        print("        git add data/output/ppi_map_data.csv && git commit -m 'Phase 1: real data signals'")
    else:
        print("  Phase 1 pipeline FAILED — check errors above")
    print("═" * 60 + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run",    action="store_true")
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--skip-ml",    action="store_true")
    ap.add_argument("--excel-bsr",  type=Path, default=None)
    ap.add_argument("--excel-cbdt", type=Path, default=None)
    args = ap.parse_args()

    dry_flag  = ["--dry-run"] if args.dry_run else []
    all_ok    = True

    if not args.skip_fetch:
        # Step 1 — VAHAN vehicle data
        ok = run_step(ETL / "fetch_vahan.py",    dry_flag, "VAHAN vehicle signals")
        all_ok = all_ok and ok

        # Step 2 — RBI BSR bank deposits
        bsr_args = dry_flag + (["--excel", str(args.excel_bsr)] if args.excel_bsr else [])
        ok = run_step(ETL / "fetch_rbi_bsr.py",  bsr_args,  "RBI BSR deposits")
        all_ok = all_ok and ok

        # Step 3 — CBDT ITR filers
        cbdt_args = dry_flag + (["--excel", str(args.excel_cbdt)] if args.excel_cbdt else [])
        ok = run_step(ETL / "fetch_cbdt_itr.py", cbdt_args, "CBDT ITR filer rates")
        all_ok = all_ok and ok

    # Step 4 — delta report + validation gates (informational, never fails pipeline)
    run_step(ETL / "merge_real_signals.py", [], "Signal delta + gate check")

    if args.skip_ml or args.dry_run:
        print_summary(all_ok)
        return

    if not all_ok:
        log.error("Fetch steps had errors — not running ML refinement on partial data")
        print_summary(False)
        sys.exit(1)

    # Step 5 — re-run ML refinement
    ok = run_step(ETL / "ml_refinement.py", [], "ML refinement")
    all_ok = all_ok and ok

    # Step 6 — copy output to app
    if all_ok:
        ml_out = OUT / "ppi_ml_refined.csv"
        app_out = OUT / "ppi_map_data.csv"
        if ml_out.exists():
            shutil.copy2(ml_out, app_out)
            # Also copy to the web app's data directory
            copy_to_app(app_out, APP_CSV)

    print_summary(all_ok)
    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
