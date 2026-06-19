"""
run_phase2.py — Phase 2 LLM-enhanced enrichment pipeline for PaisaMap.

Adds four new data signals derived from PDF documents and external reports:
  ● rate_per_sqft      →  ROR / Sale deeds  (land record PDFs)
  ● ev_share / car_2w_ratio / luxury_share  →  RTO registration certs (PDFs / CSV)
  ● upi_txn_density    →  NPCI monthly UPI stats (PDF or reference)
  ● rrb_branch_density →  NABARD RRB annual data (PDF or reference)

Phase 2 runs AFTER Phase 1 (real government signals). It supplements those
signals with document-derived data whenever PDFs are available.

LLM engine: Ollama (local) — uses llama3.2 by default.
  Install: brew install ollama && ollama serve && ollama pull llama3.2
  If Ollama is not running, scripts use built-in reference data (stub mode).

Pipeline steps:
  1. fetch_npci_upi.py      — UPI transaction density per state → pincode
  2. fetch_nabard_rrb.py    — RRB branch density (rural signal)
  3. fetch_itr_stats.py     — IT Dept ITR filers + income by state (optional PDF)
  4. parse_bbmp_tax.py      — BBMP property tax zone signal (optional PDFs)
  5. parse_ror.py            — Property rate from land record PDFs (optional)
  6. parse_rto_cert.py       — EV/luxury signals from RTO PDFs/CSV (optional)
  7. merge_phase2_signals.py — Merge new signals, print delta, run gates
  8. ml_refinement.py        — Re-run ML with new signals → ppi_ml_refined.csv
  9. Copy to app CSV

Usage:
  cd paisamap-etl
  python3 run_phase2.py                                  # reference data only
  python3 run_phase2.py --upi-pdf /path/npci.pdf         # NPCI UPI PDF
  python3 run_phase2.py --nabard-pdf /path/rrb.pdf       # NABARD report PDF
  python3 run_phase2.py --itr-pdf /path/itr_stats.pdf    # IT Dept stats PDF
  python3 run_phase2.py --bbmp-dir /path/receipts/       # BBMP tax receipt PDFs
  python3 run_phase2.py --ror-dir /path/deeds/           # land record PDFs
  python3 run_phase2.py --rto-csv /path/rto.csv          # bulk RTO data CSV
  python3 run_phase2.py --dry-run                        # don't write files
  python3 run_phase2.py --skip-ml                        # fetch + merge only
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


def run_step(script: Path, extra_args: list = [], label: str = "") -> bool:
    label = label or script.name
    log.info("── %s ─────────────────────────────────────", label)
    cmd = [PYTHON, str(script)] + extra_args
    result = subprocess.run(cmd, cwd=str(HERE))
    if result.returncode != 0:
        log.error("Step FAILED: %s (exit %d)", label, result.returncode)
        return False
    log.info("Step OK: %s\n", label)
    return True


def check_ollama() -> bool:
    """Return True if Ollama is running and has at least one model."""
    try:
        import requests as _r
        resp = _r.get("http://localhost:11434/api/tags", timeout=3)
        models = resp.json().get("models", [])
        if models:
            log.info("Ollama running: models = %s",
                     [m["name"] for m in models[:3]])
            return True
        log.warning("Ollama running but no models installed.")
        log.warning("  Run: ollama pull llama3.2")
        return False
    except Exception:
        log.warning("Ollama not running — PDF extraction will use reference/stub data")
        log.warning("  To enable LLM: brew install ollama && ollama serve && ollama pull llama3.2")
        return False


def copy_to_app(src: Path, dst: Path) -> None:
    if not src.exists():
        log.error("ML output not found at %s — cannot copy to app", src)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    row_count = sum(1 for _ in open(dst)) - 1
    log.info("App CSV updated: %s  (%d pincodes)", dst, row_count)


def print_summary(success: bool, ollama_live: bool) -> None:
    print("\n" + "═" * 60)
    if success:
        print("  Phase 2 pipeline complete ✓")
        print("  4 new signals added:")
        print("    · upi_txn_density    → NPCI UPI state volumes")
        print("    · rrb_branch_density → NABARD RRB rural data")
        print("    · rate_per_sqft      → Land records / ROR PDFs")
        print("    · ev/luxury signals  → RTO registration certificates")
        print()
        if ollama_live:
            print("  LLM extraction: ACTIVE (Ollama live)")
        else:
            print("  LLM extraction: STUB (Ollama not running — reference data used)")
            print("  To activate:  ollama serve && ollama pull llama3.2")
        print()
        print("  Next: commit and push")
        print("    git add data/ && git commit -m 'Phase 2: LLM-enhanced signals'")
        print("    git push")
    else:
        print("  Phase 2 pipeline FAILED — check errors above")
    print("═" * 60 + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upi-pdf",    type=Path, default=None, help="NPCI UPI report PDF")
    ap.add_argument("--nabard-pdf", type=Path, default=None, help="NABARD annual report PDF")
    ap.add_argument("--itr-pdf",    type=Path, default=None, help="IT Dept Annual Statistics PDF")
    ap.add_argument("--bbmp-dir",   type=Path, default=None, help="Directory of BBMP tax receipt PDFs")
    ap.add_argument("--bbmp-pdf",   type=Path, default=None, help="Single BBMP tax receipt PDF")
    ap.add_argument("--ror-dir",    type=Path, default=None, help="Directory of land record PDFs")
    ap.add_argument("--ror-pdf",    type=Path, default=None, help="Single land record PDF")
    ap.add_argument("--ror-pc",     default="",  help="Pincode for --ror-pdf")
    ap.add_argument("--rto-csv",    type=Path, default=None, help="Bulk RTO data CSV")
    ap.add_argument("--rto-dir",    type=Path, default=None, help="Directory of RTO RC PDFs")
    ap.add_argument("--dry-run",    action="store_true")
    ap.add_argument("--skip-ml",    action="store_true")
    args = ap.parse_args()

    dry_flag = ["--dry-run"] if args.dry_run else []
    ollama_live = check_ollama()
    all_ok = True

    # Step 0a — WDI national calibration baseline (always runs; uses cached parquet)
    wdi_parquet = RAW / "wdi.parquet"
    wdi_args = ["--local", str(wdi_parquet)] if wdi_parquet.exists() else []
    run_step(ETL / "fetch_wdi_baseline.py", wdi_args, "WDI national calibration baseline")
    # Non-fatal: WDI is calibration-only, not a gate signal

    # Step 0b — HCES 2023-24 district MPCE (always runs; fetches from GitHub)
    hces_cached = RAW / "district_fractile_summary.csv"
    hces_args = ["--local", str(hces_cached)] if hces_cached.exists() else []
    ok = run_step(ETL / "fetch_hces_mpce.py", hces_args, "HCES 2023-24 district MPCE signal")
    all_ok = all_ok and ok

    # Step 0c — Join HCES district MPCE to pincodes
    ok = run_step(ETL / "build_mpce_pincode.py", [], "HCES MPCE → pincode join")
    all_ok = all_ok and ok

    # Step 0d — Vehicle state trend (RS Session 248)
    ok = run_step(ETL / "fetch_vehicle_state_trend.py", [], "Vehicle state trend (RS248 → growth_4yr)")
    all_ok = all_ok and ok

    # Step 1 — NPCI UPI stats
    upi_args = dry_flag + (["--pdf", str(args.upi_pdf)] if args.upi_pdf else [])
    ok = run_step(ETL / "fetch_npci_upi.py", upi_args, "NPCI UPI transaction density")
    all_ok = all_ok and ok

    # Step 2 — NABARD RRB rural data
    nabard_args = dry_flag + (["--pdf", str(args.nabard_pdf)] if args.nabard_pdf else [])
    ok = run_step(ETL / "fetch_nabard_rrb.py", nabard_args, "NABARD RRB rural signal")
    all_ok = all_ok and ok

    # Step 3 — IT Dept Annual Statistics (optional)
    if args.itr_pdf:
        ok = run_step(ETL / "fetch_itr_stats.py", [str(args.itr_pdf)], "IT Dept ITR filer stats")
        all_ok = all_ok and ok
    else:
        log.info("Skipping ITR stats (no --itr-pdf provided — download from incometaxindia.gov.in)")

    # Step 4 — BBMP property tax receipts (optional)
    if args.bbmp_pdf or args.bbmp_dir:
        bbmp_args = dry_flag
        if args.bbmp_dir:
            bbmp_args += ["--dir", str(args.bbmp_dir)]
        elif args.bbmp_pdf:
            bbmp_args += [str(args.bbmp_pdf)]
        ok = run_step(ETL / "parse_bbmp_tax.py", bbmp_args, "BBMP property tax zone signal")
        all_ok = all_ok and ok
    else:
        log.info("Skipping BBMP tax (no --bbmp-pdf or --bbmp-dir provided)")

    # Step 5 — Land record PDFs (optional — skip if no PDFs provided)
    if args.ror_pdf or args.ror_dir:
        ror_args = dry_flag
        if args.ror_pdf:
            ror_args += ["--pdf", str(args.ror_pdf)]
            if args.ror_pc:
                ror_args += ["--pincode", args.ror_pc]
        elif args.ror_dir:
            ror_args += ["--dir", str(args.ror_dir)]
        ok = run_step(ETL / "parse_ror.py", ror_args, "Land record property rates")
        all_ok = all_ok and ok
    else:
        log.info("Skipping ROR extraction (no --ror-pdf or --ror-dir provided)")

    # Step 4 — RTO certificates (optional)
    if args.rto_csv or args.rto_dir:
        rto_args = dry_flag
        if args.rto_csv:
            rto_args += ["--csv", str(args.rto_csv)]
        elif args.rto_dir:
            rto_args += ["--dir", str(args.rto_dir)]
        ok = run_step(ETL / "parse_rto_cert.py", rto_args, "RTO vehicle signals")
        all_ok = all_ok and ok
    else:
        log.info("Skipping RTO extraction (no --rto-csv or --rto-dir provided)")

    # Step 5 — Merge and validate (informational, never fails pipeline)
    run_step(ETL / "merge_real_signals.py", [], "Phase 2 signal delta + gate check")

    if args.skip_ml or args.dry_run:
        print_summary(all_ok, ollama_live)
        return

    if not all_ok:
        log.error("Some steps failed — running ML anyway on available signals")

    # Step 6 — Re-run ML with Phase 2 signals
    ok = run_step(ETL / "ml_refinement.py", [], "ML refinement (Phase 2 signals)")
    all_ok = all_ok and ok

    # Step 7 — Copy output to app
    if all_ok:
        ml_out  = OUT / "ppi_ml_refined.csv"
        app_out = OUT / "ppi_map_data.csv"
        if ml_out.exists():
            shutil.copy2(ml_out, app_out)
            copy_to_app(app_out, APP_CSV)

    print_summary(all_ok, ollama_live)
    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
