"""
fetch_district_population.py — Pan-India district population reference.

Needed to convert real per-pincode PSU bank branch counts
(data/raw/rbi_branch_counts_india.csv, see parse_rbi_branch_master.py) into
a real bank_branches_per_lakh signal for districts outside the 14 covered
by data/reference/rbi_bsr_district_2023.csv (RBI's own district-level BSR
deposits/branch-density baseline only covers Delhi's 8 sub-districts,
Gurugram, Gautam Buddha Nagar, Mumbai City/Suburban, Thane, Bengaluru
Urban — see fetch_rbi_bsr.py). RBI's district-level BSR figures aren't
practically fetchable pan-India from this environment (the DBIE/BSRView
portal requires an interactive session and redirects raw requests — same
bot-wall class of issue as the branch-master export; see
parse_rbi_branch_master.py's docstring for that precedent). District
population is a much simpler, static fact we can source cleanly instead:
Census of India district population totals, via Wikipedia's per-state
district tables (which cite the Census as their source).

Caveat: most states show 2011 Census figures; a handful of reorganized
states (currently just Andhra Pradesh, as of this script's writing) show
2021 estimates for their post-2011-split districts instead — Wikipedia's
own inconsistency, kept as-is rather than papered over. Either way this is
"best available population for the district as currently named," which is
the same standard the rest of this ETL pipeline already applies to VAHAN/
HCES reference data (also Census-2011-anchored).

Output: data/reference/district_population_census.csv
  columns: state_name, state_code, district, population, pop_year

Usage:
  python3 etl/fetch_district_population.py
  python3 etl/fetch_district_population.py --dry-run
"""

import argparse
import re
from io import StringIO
from pathlib import Path

import pandas as pd
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT  = ROOT / "data" / "reference" / "district_population_census.csv"

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_districts_in_India"
HEADERS  = {"User-Agent": "PaisaMap-DistrictPopulation/1.0 (one-time reference fetch)"}

# Matches state/UT section headings like "Andhra Pradesh (AP)"
STATE_HEADING_RE = re.compile(r"^([A-Za-z .&]+?)\s*\(([A-Z]{2})\)$")


def fetch_html() -> str:
    req = urllib.request.Request(WIKI_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


def parse_district_population(html: str) -> pd.DataFrame:
    # Split the page into (heading, content-until-next-heading) segments and
    # keep only the ones whose heading is a state/UT name — this walks the
    # per-state district tables in document order without needing to align
    # index positions against pandas.read_html's full table list (which also
    # includes non-state tables interspersed, an easy source of off-by-one
    # bugs if matched by position instead of by heading).
    parts = re.split(r"(<h[23][^>]*>.*?</h[23]>)", html, flags=re.S)

    rows = []
    i = 1
    while i < len(parts) - 1:
        heading_text = re.sub("<[^>]+>", "", parts[i]).strip()
        content = parts[i + 1]
        m = STATE_HEADING_RE.match(heading_text)
        is_delhi = heading_text == "National Capital Territory of Delhi (DL)"
        if m or is_delhi:
            state_name, code = (m.group(1).strip(), m.group(2)) if m else ("Delhi", "DL")
            tbl_m = re.search(
                r'<table[^>]*class="[^"]*wikitable[^"]*"[^>]*>.*?</table>', content, re.S
            )
            if tbl_m:
                try:
                    df = pd.read_html(StringIO(tbl_m.group(0)))[0]
                except Exception:
                    df = None
                if df is not None:
                    pop_col  = next((c for c in df.columns if str(c).startswith("Population")), None)
                    dist_col = next((c for c in df.columns if str(c).startswith("District")), None)
                    if pop_col and dist_col:
                        pop_year = 2021 if "2021" in str(pop_col) else 2011
                        for _, r in df.iterrows():
                            dist = re.sub(r"\[\d+\]", "", str(r[dist_col])).strip()
                            rows.append({
                                "state_name": state_name, "state_code": code,
                                "district": dist, "population": r[pop_col],
                                "pop_year": pop_year,
                            })
        i += 2

    out = pd.DataFrame(rows)
    out["population"] = pd.to_numeric(out["population"], errors="coerce")
    out = out.dropna(subset=["population"])
    out["population"] = out["population"].astype(int)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Fetching: {WIKI_URL}")
    html = fetch_html()
    df = parse_district_population(html)
    n_states = df["state_code"].nunique()
    print(f"Parsed {len(df)} districts across {n_states} states/UTs")

    if args.dry_run:
        print(df.head(15))
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"Written: {OUT} ({len(df)} rows)")


if __name__ == "__main__":
    main()
