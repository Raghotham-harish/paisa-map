"""
test_refit_gates.py — issue #5: every refit validation gate points at the place its label names.

    python3 tests/test_refit_gates.py

The Sunday refit reverts on any "  FAIL" gate (cron_enrich.sh), so a gate keyed to the wrong PIN
silently validates the wrong place: "Indiranagar" was 560025, which India Post names Museum Road.
Checks each gate PIN against data/reference/pincode_office_names.csv (India Post) and against our
own label in pincode_names.csv, and that a missing gate PIN fails the refit instead of being
skipped. Also prints how each gate scores on the committed data (informational). Plain script.
"""
import ast
import csv
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ETL = os.path.join(REPO, "paisamap-etl")
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


src = open(os.path.join(ETL, "etl", "ml_refinement.py")).read()
block = src[src.index("    gates = ["):src.index("    gate_results = []")]
gates = ast.literal_eval(re.sub(r"#.*", "", block.split("=", 1)[1]).strip())
check(len(gates) == 10, f"10 refit gates ({len(gates)})")
check("122022" not in open(os.path.join(ETL, "data", "reference", "pincode_office_names.csv")).read(),
      "122022 is still absent from India Post (revisit PENDING_GURGAON if not)")

office = {}
for r in csv.DictReader(open(os.path.join(ETL, "data", "reference", "pincode_office_names.csv"))):
    office.setdefault(r["pincode"], []).append(r["name"].lower())
label = {r["pincode"]: r["name"] for r in csv.DictReader(open(os.path.join(ETL, "data", "raw", "pincode_names.csv")))}

# The Gurgaon gate, pending a founder decision (fix brief item 2): 122022 has no India Post office
# at all, and 122002 is DLF QE, not "Gurgaon City". Its honest replacement (DLF QE 122002 >
# Gurgaon 122001) fails on current data, so it is left as-is and excluded here, visibly.
PENDING_GURGAON = {"122022", "122002"}
# Gate place -> India Post office name(s) for the same PIN, where the office is named differently
# (Golf Links and Saket sit in the Lodi Road / Malviya Nagar delivery areas).
ALIASES = {"hebbal": ["h.a. farm"], "golf links": ["lodi road"], "saket": ["malviya nagar"]}


def confirms(pc, place):
    place = place.lower().strip()
    names = office.get(pc, [])
    wanted = [place] + ALIASES.get(place, [])
    return any(w in n or n in w for w in wanted for n in names)


for hi, lo, text in gates:
    places = [p.strip() for p in re.split(r"\s>\s", re.sub(r"\(.*?\)", "", text))]
    check(len(places) == 2, f"gate label '{text}' reads 'A > B'")
    for pc, place in zip((hi, lo), places):
        if pc in PENDING_GURGAON:
            continue
        check(pc in office, f"gate PIN {pc} ('{place}') exists in the India Post directory")
        check(confirms(pc, place) or any(confirms(pc, a) for a in place.split(" / ")),
              f"gate PIN {pc}: India Post names it {office.get(pc)}, gate says '{place}'")
        ours = label.get(pc, "")
        check(ours and (confirms(pc, ours) or ours.lower() in place.lower() or place.lower() in ours.lower()),
              f"gate PIN {pc}: our own label '{ours}' agrees with India Post {office.get(pc)}")

check("560025" not in {pc for g in gates for pc in g[:2]}, "560025 (Museum Road) is no longer the Indiranagar gate")
check(label.get("560025") == "Museum Road", f"560025 is labelled Museum Road (is '{label.get('560025')}')")
check(label.get("560038") == "Indiranagar", "560038 is labelled Indiranagar")

# a gate PIN missing from the refit fails it (used to be skipped silently)
check('"FAIL  {label}: gate PIN(s)' in src.replace("f\"FAIL", "\"FAIL"), "missing gate PIN is reported as FAIL")
check("if hi in income_df.index and lo in income_df.index:" not in src, "no silent skip of a missing gate PIN")

# informational: how the gates score on the committed refit output
ppi = {r["pincode"]: float(r["ppi_ml"]) for r in csv.DictReader(open(os.path.join(ETL, "data", "output", "ppi_ml_refined.csv")))}
print("gates on committed data:")
for hi, lo, text in gates:
    a, b = ppi.get(hi), ppi.get(lo)
    state = "MISSING" if a is None or b is None else ("PASS" if a > b else "FAIL")
    print(f"  {state:<7} {text}: PPI({hi})={a} vs PPI({lo})={b}")

print(f"test_refit_gates: {passed} checks passed")
