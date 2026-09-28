"""
test_public_data_licensing.py — nothing we may not redistribute reaches a public surface.

    python3 tests/test_public_data_licensing.py

Public = the static files nginx serves to anyone (data/output/*.csv, data/boundaries.geojson) and
what the free map (index.html) asks for. See docs/DATA_LICENSING.md:
  - RED: OSM/ODbL-derived values (premium_poi_per_km2, the map CSV's `poi`, the Overpass-sourced
    financial_inclusion columns) and the PhonePe live-site grid (upi_txn_count_nearby).
  - INTERNAL: the RBI branch count (psu_branch_count) until the RBI terms question is answered.
Static files are checked against an ALLOWLIST, so a new column has to be added here on purpose.
Plain script, no server needed.
"""
import csv
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


RED_KEYS = {"premium_poi_per_km2", "poi", "fin_density_per_km2", "sfb_branches", "coop_branches",
            "rrb_branches", "fin_branches_total", "upi_txn_count_nearby"}
INTERNAL_KEYS = {"psu_branch_count"}
NOT_PUBLIC = RED_KEYS | INTERNAL_KEYS

PUBLIC_STATIC_COLUMNS = {
    "ppi_map_data.csv": {"pincode", "name", "lat", "lng", "ppi", "income"},
    "broad_coverage.csv": {"pincode", "name", "lat", "lng", "mpce_combined", "hces_ppi", "state", "district"},
    "enrichment_log.csv": {"timestamp", "pincode", "name", "lat", "lng", "source", "ppi", "income"},
    "pincode_households.csv": {"pincode", "households"},
}

# every CSV nginx can serve from data/output/ is on the allowlist, with only allowed columns
out_dir = os.path.join(REPO, "data", "output")
served = sorted(f for f in os.listdir(out_dir) if f.endswith(".csv"))
for fname in served:
    check(fname in PUBLIC_STATIC_COLUMNS, f"data/output/{fname} is on the public allowlist (add it deliberately)")
    with open(os.path.join(out_dir, fname), newline="") as fh:
        header = set(next(csv.reader(fh)))
    extra = header - PUBLIC_STATIC_COLUMNS[fname]
    check(not extra, f"data/output/{fname} has no non-allowlisted columns (found {sorted(extra)})")
    check(not (header & NOT_PUBLIC), f"data/output/{fname} carries no red/internal column")

with open(os.path.join(REPO, "data", "boundaries.geojson")) as fh:
    props = {k for feat in json.load(fh)["features"] for k in (feat.get("properties") or {})}
check(not (props & NOT_PUBLIC), f"boundaries.geojson properties carry no red/internal key ({sorted(props & NOT_PUBLIC)})")

# the builders of the public files write only allowed columns
import build_broad_coverage
check(set(build_broad_coverage.PUBLIC_COLUMNS) == PUBLIC_STATIC_COLUMNS["broad_coverage.csv"],
      "build_broad_coverage.PUBLIC_COLUMNS matches the allowlist")
for script in ("ml_refinement.py", "enrich_single.py", "batch_enrich_hces.py", "expand_core_idw.py"):
    src = open(os.path.join(REPO, "paisamap-etl", "etl", script)).read()
    check('"poi":' not in src, f"{script} no longer writes a `poi` column into ppi_map_data.csv")

# the free map never asks for a red/internal key
html = open(os.path.join(REPO, "index.html"), encoding="utf-8").read()
for key in ("upi_txn_count_nearby", "psu_branch_count"):
    check(key not in html, f"index.html does not reference {key}")
check("fetch_phonepe_grid" not in html, "index.html has no PhonePe grid reference")
for path in ("paisamap-etl/etl/fetch_phonepe_grid.py", "paisamap-etl/data/raw/upi_txn_density_grid.csv"):
    check(not os.path.exists(os.path.join(REPO, path)), f"{path} is out of the repo tree (quarantined)")

# deploy's merge must not re-add a column the repo file dropped
with tempfile.TemporaryDirectory() as d:
    repo_f, bak_f = os.path.join(d, "repo.csv"), os.path.join(d, "bak.csv")
    open(repo_f, "w").write("pincode,name,ppi\n110001,A,100\n")
    open(bak_f, "w").write("pincode,name,ppi,poi\n110001,A,99,50\n110002,B,90,40\n")
    subprocess.run([sys.executable, os.path.join(REPO, "merge_enrichment_file.py"), bak_f, repo_f],
                   check=True, capture_output=True)
    rows = list(csv.reader(open(repo_f)))
    check(rows[0] == ["pincode", "name", "ppi"], f"merge keeps the repo's columns (got {rows[0]})")
    check(rows[1] == ["110001", "A", "100"] and rows[2] == ["110002", "B", "90"],
          f"merge keeps repo rows and adds server-only rows (got {rows[1:]})")

print(f"test_public_data_licensing: {passed} checks passed")
