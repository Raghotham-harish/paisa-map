"""
test_refit_hard_gate.py — F2 (refit peer review, 2026-10-06): the weekly refit's ±10pt stability
check is a hard gate, measured against the last ACCEPTED refit, and a rejected refit writes nothing.

    python3 tests/test_refit_hard_gate.py

Covers ml_refinement.stability_check / write_run_record / the decide-before-write ordering in
main(), and refit_weekly.sh (the cron step) driven by a fake python: accepted, rejected (exit 3),
crash (outputs restored from the snapshot), and the one-shot owner waiver. The full refit itself
was run end to end on real data when this landed (rejected without waiver -> 222 pins, accepted
with it, 0 swings on rerun). Plain script, no DATABASE_URL needed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ETL = os.path.join(REPO, "paisamap-etl", "etl")
sys.path.insert(0, ETL)
os.chdir(ETL)
import ml_refinement as ml  # noqa: E402

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


def s(d):
    return pd.Series(d, dtype=float).rename_axis("pincode")


# ── stability_check ──────────────────────────────────────────────────────────
base = s({"560038": 114, "700019": 40, "110001": 150})
live = s({"560038": 114, "700019": 40, "110001": 150, "804435": 118})   # 804435 appended by IDW
new = s({"560038": 120, "700019": 50.5, "110001": 160, "804435": 79})

r = ml.stability_check(new, base, live)
check(r["status"] == "FAIL", "a 10.5pt move fails the gate")
check(list(r["swings"].index) == ["700019"], f"only 700019 swings, got {list(r['swings'].index)}")
check(r["source"] == "refit_baseline.csv", "compares against the baseline when there is one")
check(r["new_pincodes"] == ["804435"], "a pincode the baseline lacks is listed as new")
check("804435" not in r["swings"].index, "a new pincode's first model score is not a swing (Δ39 vs IDW)")
check(r["compared"] == 3, "compares only pincodes in both")
check(r["max_drift_pincode"] == "700019" and abs(r["max_drift"] - 10.5) < 1e-9, "max drift reported")

r = ml.stability_check(s({"560038": 124, "700019": 40, "110001": 150}), base, live)
check(r["status"] == "PASS", "exactly 10pt is within the limit")
r = ml.stability_check(s({"560038": 124.01, "700019": 40, "110001": 150}), base, live)
check(r["status"] == "FAIL", "just over 10pt fails")
r = ml.stability_check(s({"560038": 104, "700019": 40, "110001": 150}), base, live)
check(r["status"] == "PASS", "a 10pt drop is within the limit")
r = ml.stability_check(s({"560038": 103, "700019": 40, "110001": 150}), base, live)
check(r["status"] == "FAIL" and list(r["swings"].index) == ["560038"], "drops count as swings too")

r = ml.stability_check(new, None, live)
check(r["status"] == "FAIL" and "804435" in r["swings"].index,
      "without a baseline it falls back to the live file (old behaviour)")
check("no baseline" in r["source"], "the fallback is labelled")
check(r["new_pincodes"] == [], "no 'new' list without a baseline")

r = ml.stability_check(new, None, None)
check(r["status"] == "SKIP" and r["compared"] == 0, "first ever run skips")

r = ml.stability_check(s({"560038": float("nan"), "700019": 40}), s({"560038": 114, "700019": 40}), None)
check(r["status"] == "PASS" and r["compared"] == 1, "a NaN score is dropped, not compared")

# ── write_run_record ─────────────────────────────────────────────────────────
tmp = Path(tempfile.mkdtemp())
ml.RUN_RECORD_PATH = tmp / "refit_last_run.json"
r = ml.stability_check(new, base, live)
names = pd.Series({"700019": "Ballygunge", "560038": float("nan")})
rec = ml.write_run_record("rejected", ["PASS  x", "FAIL  PPI stability: 1"], r, new, base, names, None)
on_disk = json.loads(ml.RUN_RECORD_PATH.read_text())
check(on_disk == rec, "record written to RUN_RECORD_PATH")
check(rec["status"] == "rejected" and rec["accepted_drift"] is None, "status + waiver recorded")
check(rec["stability"]["pincodes_over_limit"] == 1, "swing count recorded")
check(rec["stability"]["top_swings"][0] == {"pincode": "700019", "name": "Ballygunge", "old": 40.0,
                                             "new": 50.5, "abs_delta": 10.5}, "top swing detail")
check(rec["stability"]["new_since_baseline"] == 1 and rec["stability"]["new_since_baseline_sample"] == ["804435"],
      "new pincodes recorded")
check(rec["validation_gates"][1].startswith("FAIL"), "gate lines kept")
rec = ml.write_run_record("accepted", [], r, new, base, None, "F1 approved")
check(rec["accepted_drift"] == "F1 approved", "waiver reason recorded")

# ── args ─────────────────────────────────────────────────────────────────────
check(ml._parse_args([]).accept_drift is None, "no waiver by default")
check(ml._parse_args(["--accept-drift", "why"]).accept_drift == "why", "--accept-drift takes a reason")

# ── main(): decide before writing (source order) ─────────────────────────────
src = open(os.path.join(ETL, "ml_refinement.py")).read()
body = src[src.index("def main("):]
reject = body.index("raise SystemExit(EXIT_REJECTED)")
for marker in ["out_df.to_csv(ml_out_path)", "app_df.to_csv(app_path)", "app_df.to_csv(app_dest)",
               "_db.bulk_upsert_pincodes(", '"ml_diagnostics.json").write_text', ".to_csv(BASELINE_PATH)"]:
    check(marker in body and body.index(marker) > reject, f"{marker} happens only after the reject check")
check("WARN  PPI stability" not in src and 'f"WARN  {len(swings)}' not in src, "the WARN-only stability line is gone")
check(body.index("stab = stability_check(") < reject, "stability is decided before the reject check")
# ── stability_gate_line / failed_gates (the decision main() acts on) ─────────
fail = ml.stability_check(new, base, live)
line = ml.stability_gate_line(fail, None)
check(line.startswith("FAIL  PPI stability: 1 pincodes"), f"breach without waiver is FAIL: {line}")
line = ml.stability_gate_line(fail, "F1 approved")
check(line.startswith("ACCEPTED  PPI stability") and "waived: F1 approved" in line, "waiver -> ACCEPTED + reason")
ok = ml.stability_check(s({"560038": 114, "700019": 40, "110001": 150}), base, live)
check(ml.stability_gate_line(ok, None).startswith("PASS"), "no breach -> PASS")
check(ml.stability_gate_line(ok, "x").startswith("PASS"), "waiver on a clean run is still PASS")
gates = ["PASS  Golf Links > Saket", "FAIL  Indiranagar > Electronics City: 113 vs 114"]
check(ml.failed_gates(gates) == [gates[1]], "an ordering-gate FAIL rejects")
check(ml.failed_gates(["PASS  a", ml.stability_gate_line(fail, None)]) != [], "a stability FAIL rejects")
check(ml.failed_gates(["PASS  a", ml.stability_gate_line(fail, "ok")]) == [], "a waived breach does not reject")
check(ml.failed_gates(gates + [ml.stability_gate_line(fail, "ok")]) == [gates[1]],
      "the waiver never covers an ordering gate")
check('r = stability_gate_line(stab, args.accept_drift)' in body and 'failed = failed_gates(gate_results)' in body,
      "main() uses these two")
check("        gate_results.append(r)\n\n    # ── Decide before writing" in body,
      "main() adds the stability line to the gates it decides on")
check(ml.EXIT_REJECTED == 3 and ml.PPI_SWING_LIMIT == 10, "constants")

# ── refit_weekly.sh with a fake python ───────────────────────────────────────
OUTPUTS = ["data/output/ppi_map_data.csv", "paisamap-etl/data/output/ppi_map_data.csv",
           "paisamap-etl/data/output/ppi_ml_refined.csv", "paisamap-etl/data/output/ml_diagnostics.json"]
FAKE = r'''#!/usr/bin/env bash
# fake python: "$1" is etl/ml_refinement.py, the rest are its args
shift
if [ $# -gt 0 ]; then printf '%s\n' "$@" > "$FAKE_ARGS"; else : > "$FAKE_ARGS"; fi
case "$FAKE_MODE" in
  ok)     echo "  PASS  all"; echo new > data/output/ppi_ml_refined.csv; exit 0 ;;
  reject) echo "  FAIL  PPI stability: 9 pincodes drifted"; exit 3 ;;
  crash)  echo half-written > data/output/ppi_ml_refined.csv; echo boom > ../data/output/ppi_map_data.csv; exit 1 ;;
  failok) echo "  FAIL  odd"; exit 0 ;;
esac
'''


def run_wrapper(mode, waiver=None):
    root = Path(tempfile.mkdtemp())
    for f in OUTPUTS:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("orig " + f + "\n")
    shutil.copy(os.path.join(REPO, "refit_weekly.sh"), root / "refit_weekly.sh")
    fake = root / "fakepy"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    accept = root / "accept_drift"
    if waiver is not None:
        accept.write_text(waiver)
    env = dict(os.environ, REPO=str(root), PYTHON=str(fake), LOG_DIR=str(root / "logs"),
               REFIT_ACCEPT_FILE=str(accept), FAKE_MODE=mode, FAKE_ARGS=str(root / "args"))
    env.pop("DATABASE_URL", None)
    p = subprocess.run(["bash", str(root / "refit_weekly.sh")], env=env, capture_output=True, text=True)
    args = (root / "args").read_text().splitlines() if (root / "args").exists() else None
    files = {f: (root / f).read_text() for f in OUTPUTS}
    return p, args, files, accept.exists()


p, args, files, left = run_wrapper("ok")
check(p.returncode == 0 and "Full refit OK" in p.stdout, f"ok run reported OK: {p.stdout} {p.stderr}")
check(args == [], f"no waiver -> no extra args, got {args}")
check(files["paisamap-etl/data/output/ppi_ml_refined.csv"] == "new\n", "accepted output kept")

p, args, files, left = run_wrapper("reject")
check(p.returncode == 0, "a rejection doesn't fail the cron (set -e in cron_enrich.sh)")
check("REJECTED" in p.stdout and "FAIL  PPI stability" in p.stdout, "rejection reported with the failing gate")
check(all(v.startswith("orig") for v in files.values()), "rejection leaves outputs as they were")

p, args, files, left = run_wrapper("crash")
check(p.returncode == 0 and "restored" in p.stdout, "crash reported")
check(files["paisamap-etl/data/output/ppi_ml_refined.csv"].startswith("orig") and
      files["paisamap-etl/data/output/ppi_map_data.csv"].startswith("orig"),
      "crash mid-write -> outputs restored from the snapshot")

p, args, files, left = run_wrapper("failok")
check("restored" in p.stdout, "exit 0 with a FAIL line in the log is still treated as not-OK")

p, args, files, left = run_wrapper("ok", waiver="F1 state fix\napproved 2026-10-08\n")
check(args == ["--accept-drift", "F1 state fix approved 2026-10-08"], f"waiver text passed as one reason, got {args}")
check(not left, "waiver file removed after use")
check("waived for this run only" in p.stdout, "waiver announced in the cron log")

p, args, files, left = run_wrapper("reject", waiver="x")
check(not left, "waiver removed even when the run is rejected (never carries over a week)")

p, args, files, left = run_wrapper("ok", waiver="")
check(args == ["--accept-drift", "approved by owner"], "empty waiver file -> default reason")

# ── cron_enrich.sh wiring ────────────────────────────────────────────────────
cron = open(os.path.join(REPO, "cron_enrich.sh")).read()
check('bash "$REPO/refit_weekly.sh"' in cron, "cron step 5 runs refit_weekly.sh via bash")
check("git checkout --" not in cron, "the CSV-only git checkout revert is gone")
for f in ["paisamap-etl/data/output/refit_baseline.csv", "paisamap-etl/data/output/refit_last_run.json"]:
    check(f in cron[cron.index("git add"):], f"cron commits {f}")

print(f"test_refit_hard_gate: {passed} checks passed")
