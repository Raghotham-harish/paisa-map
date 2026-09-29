"""
test_synthetic_pincodes.py — issue #3. Synthetic D###### ids are not pincodes.

    python3 tests/test_synthetic_pincodes.py
"""

import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "paisamap-etl" / "etl"))

import pincode_master as M  # noqa: E402

passed = 0
PIN = re.compile(r"^[1-9]\d{5}$")
D_ID = re.compile(r"^D\d{6}$")


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"  ok  {msg}")


check(M.real_pincode_id("560038") == "560038", "a real pincode id is kept")
check(M.real_pincode_id("012345") == "", "a leading zero is not a pincode id")
check(M.real_pincode_id("D367866") == "", "a D###### id is not a pincode id")
check(M.real_pincode_id("56003") == "", "a 5-digit id is not a pincode id")
check(M.real_pincode_id("") == "", "an empty id is not a pincode id")

try:
    M.assert_real_pincode_ids(["560038", "D367866"], context="test")
    check(False, "writing a D-id must raise")
except ValueError as e:
    check("D367866" in str(e), "the assertion names the bad id")
M.assert_real_pincode_ids(["560038", "110001"], context="test")
check(True, "a list of real pincode ids is accepted")

src = (REPO / "paisamap-etl" / "etl" / "batch_enrich_hces.py").read_text()
check("abs(hash(" not in src and "Synthetic pincode" not in src,
      "batch_enrich_hces no longer mints a synthetic id")
check('status": "no_pincode"' in src or "no_pincode" in src,
      "a district with no real pincode is logged as no_pincode and skipped")

# Every published pincode file and every raw signal file.
roots = [
    REPO / "paisamap-etl" / "data" / "raw",
    REPO / "paisamap-etl" / "data" / "output",
    REPO / "data" / "output",
]
skip = {"batch_enrich_log.csv", "enrichment_log.csv"}
scanned = 0
for folder in roots:
    for path in sorted(folder.glob("*.csv")):
        if path.name in skip:
            continue
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "pincode" not in reader.fieldnames:
                continue
            scanned += 1
            bad = []
            for row in reader:
                pc = (row.get("pincode") or "").strip()
                if not PIN.fullmatch(pc):
                    bad.append(pc)
                    if len(bad) >= 3:
                        break
            check(not bad, f"{path.relative_to(REPO)} has 0 non-pincode ids, saw {bad}")
check(scanned >= 10, f"scanned {scanned} pincode files")

qpath = REPO / "paisamap-etl" / "data" / "quarantine" / "district_centroids.csv"
with open(qpath, newline="") as f:
    qrows = list(csv.DictReader(f))
check(len(qrows) == 252, f"252 synthetic rows quarantined, got {len(qrows)}")
check(all(D_ID.fullmatch(r["pincode"] or "") for r in qrows), "every quarantined id is a D###### id")
check(all(r["district"] and r["state"] and r["lat"] and r["lng"] and r["ppi_ml"] for r in qrows),
      "quarantine keeps district, state, lat, lng and PPI")
ids = {r["pincode"] for r in qrows}
with open(REPO / "paisamap-etl" / "data" / "output" / "ppi_ml_refined.csv", newline="") as f:
    still = [r["pincode"] for r in csv.DictReader(f) if r["pincode"] in ids]
check(not still, "none of the quarantined ids remain in ppi_ml_refined.csv")

inv = REPO / "paisamap-etl" / "data" / "quarantine" / "invalid_pincode_rows.csv"
with open(inv, newline="") as f:
    inv_rows = list(csv.DictReader(f))
check(inv_rows and all(not PIN.fullmatch(r["pincode"]) for r in inv_rows),
      "other invalid pincode rows were quarantined rather than deleted")

# deploy.sh restores server-side-only rows with merge_enrichment_file.py. The
# server's pre-deploy backup still holds the D###### rows, so the merge must
# not add them back (or ml_refinement's assert_real_pincode_ids aborts the refit).
import subprocess, sys as _sys, tempfile
with tempfile.TemporaryDirectory() as td:
    repo_f, bak_f = Path(td, "repo.csv"), Path(td, "bak.csv")
    repo_f.write_text("pincode,rate\n560038,100\n")
    bak_f.write_text("pincode,rate\n560038,999\nD123456,5\n012345,6\n,7\n560100,80\n")
    subprocess.run([_sys.executable, str(REPO / "merge_enrichment_file.py"), str(bak_f), str(repo_f)],
                   check=True, capture_output=True)
    merged = repo_f.read_text().split()
    check(merged == ["pincode,rate", "560038,100", "560100,80"],
          f"deploy merge re-adds only real server-side pincodes, got {merged}")

print(f"\n{passed} checks passed")
