"""
fetch_rbi_bsr.py — District-level bank deposit data from RBI Basic Statistical Returns.

Live source : RBI DBIE (Database on Indian Economy)
              https://dbie.rbi.org.in/DBIE/dbie.rbi?site=statistics
              Table: BSR-1 — Outstanding Credit of Scheduled Commercial Banks
              Table: BSR-2 — Deposits with Scheduled Commercial Banks
              Both available as Excel downloads (annual, released ~9 months after March)

Fallback    : data/reference/rbi_bsr_district_2023.csv  (BSR March 2023 figures,
              only 14 districts — Delhi x8, Gurugram, Gautam Buddha Nagar,
              Mumbai City/Suburban, Thane, Bengaluru Urban)

Neither DBIE (dbie.rbi.org.in — unreachable, no DNS response from this
environment) nor RBI's own document server (rbidocs.rbi.org.in, which
hosts the "Handbook of Statistics on Indian States" annual publication —
reachable, but served behind a JavaScript bot-challenge that blocks any
non-browser client) can be fetched automatically. Confirmed by direct
testing 2026-07-15, same class of block as the pan-India branch-master
export (see parse_rbi_branch_master.py) — that one was solved by a human
exporting from a real browser, which is the only proven path here too.

STATE-level (coarser than the district baseline above, but real data
rather than a flat imputed median) via a manual export:
  1. Open https://rbidocs.rbi.org.in/rdocs/Publications/DOCs/155T_11122025BC88547570414295AB088FBCF5C90806.XLSX
     in a browser — "Table 155: State-wise Deposits by Scheduled Commercial
     Banks in India". If that link 404s (RBI republishes with a new
     ID-stamped URL each release), go to rbi.org.in -> Publications ->
     Handbook of Statistics on Indian States -> find Table 155 in the list.
  2. Same for Table 153 or 154 ("State-wise Credit-Deposit Ratio... by
     Place of Sanction / Utilisation" — either is fine, they're close):
     https://rbidocs.rbi.org.in/rdocs/Publications/DOCs/153T_111220255DEA2A2D23744132BFEDD5768D038648.XLSX
  3. python3 etl/fetch_rbi_bsr.py --deposits-xlsx /path/to/155T....XLSX

The Excel column layout isn't guaranteed stable release to release and
this parser hasn't been run against a real download yet — load_handbook_state_table()
looks up columns by name/keyword rather than position, but check the
printed "Using column" / match-count lines the first time you run it for
real before trusting the output.

Outputs (written to data/raw/):
  bank_deposits.csv  — pincode, deposits_per_capita, bank_branches_per_lakh

Usage:
  python3 etl/fetch_rbi_bsr.py
  python3 etl/fetch_rbi_bsr.py --excel /path/to/bsr2_2023.xlsx        # district Excel (untested path)
  python3 etl/fetch_rbi_bsr.py --deposits-xlsx /path/to/155T....XLSX  # state-level Handbook fallback
  python3 etl/fetch_rbi_bsr.py --dry-run
"""

import sys
import argparse
import logging
import re
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

ETL     = Path(__file__).parent.parent
REF     = ETL / "data" / "reference"
RAW     = ETL / "data" / "raw"
PC_MAP  = REF / "pincode_district_map.csv"
BSR_REF = REF / "rbi_bsr_district_2023.csv"
POP_REF = REF / "district_population_census.csv"
BRANCH_COUNTS = RAW / "rbi_branch_counts_india.csv"
COORDS  = RAW / "pincode_coords.csv"

# RBI DBIE does not expose a clean REST API; data comes as Excel.
# The URL below downloads the latest BSR-2 state/district summary when available.
RBI_BSR2_URL = (
    "https://www.rbi.org.in/scripts/BSRView.aspx"
    "?Id=bsr2&Mode=0"
)


def _try_parse_excel(excel_path: Path) -> object:
    """
    Parse an RBI BSR-2 Excel file into a district→deposits dataframe.

    RBI BSR-2 Excel layout (as of 2023 release):
      Sheet "Table 1.1": columns include State, District, Total Deposits (₹ lakh)
    Column positions may shift between years — this parser looks for them by name.
    """
    try:
        import openpyxl  # noqa: F401
        xl = pd.ExcelFile(excel_path, engine="openpyxl")
        # Try known sheet names
        sheet = next(
            (s for s in xl.sheet_names if "1.1" in s or "district" in s.lower()),
            xl.sheet_names[0],
        )
        df = xl.parse(sheet, header=None)
        # Find header row (contains "District" keyword)
        hdr_row = next(
            (i for i, row in df.iterrows() if "District" in row.values or "district" in str(row.values).lower()),
            None,
        )
        if hdr_row is None:
            log.warning("Could not find header row in BSR Excel")
            return None
        df.columns = df.iloc[hdr_row]
        df = df.iloc[hdr_row + 1:].reset_index(drop=True)
        # Normalise column names
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
        dep_col = next((c for c in df.columns if "deposit" in c), None)
        dist_col = next((c for c in df.columns if "district" in c), None)
        state_col = next((c for c in df.columns if "state" in c), None)
        if not all([dep_col, dist_col]):
            log.warning("Could not identify deposit/district columns in BSR Excel")
            return None
        df = df[[state_col, dist_col, dep_col]].dropna(subset=[dist_col, dep_col])
        df.columns = ["state", "district", "deposits_lakh"]
        df["deposits_lakh"] = pd.to_numeric(df["deposits_lakh"], errors="coerce")
        df = df.dropna(subset=["deposits_lakh"])
        log.info("BSR Excel parsed: %d district rows", len(df))
        return df
    except Exception as exc:
        log.warning("BSR Excel parse failed: %s", exc)
        return None


def load_bsr_district(excel_path: object = None) -> pd.DataFrame:
    """Return district BSR data: index=district, columns include per_capita_deposits."""
    if excel_path and excel_path.exists():
        df = _try_parse_excel(excel_path)
        if df is not None:
            # Compute per-capita from Excel totals (requires population join)
            # For now merge with reference to get population, then scale deposits
            ref = pd.read_csv(BSR_REF, comment="#").set_index("district")
            district_pop = {
                row["district"]: row["district_pop_lakh"] * 100000
                for _, row in pd.read_csv(PC_MAP).iterrows()
            }
            df["population"] = df["district"].map(district_pop)
            df["per_capita_deposits"] = (
                df["deposits_lakh"] * 1e5 / df["population"]
            ).round(0)
            df = df.dropna(subset=["per_capita_deposits"])
            return df.set_index("district")

    log.info("Loading RBI BSR reference CSV: %s", BSR_REF)
    df = pd.read_csv(BSR_REF, comment="#")
    df["district"] = df["district"].str.strip()
    return df.set_index("district")


# Our project's district names (pincode_district_map.csv) -> RBI branch-master
# district names (data/raw/rbi_branch_counts_mh.csv). RBI's field is the pre-2011
# Mumbai split ("MUMBAI" = Mumbai City); everything else is an uppercase match.
RBI_DISTRICT_ALIASES = {
    "Mumbai City":     "MUMBAI",
    "Mumbai Suburban": "MUMBAI SUBURBAN",
}


def _minmax_scalar(pc_val: float, vals: pd.Series) -> float:
    """Scale a value to 0.60-1.40 against the min/max of its district peers."""
    v_min, v_max = vals.min(), vals.max()
    scalar = 0.60 + 0.80 * (pc_val - v_min) / max(v_max - v_min, 0.1)
    return max(0.60, min(1.40, scalar))


def build_pincode_deposits(bsr: pd.DataFrame, pc_map: pd.DataFrame) -> pd.DataFrame:
    """
    Map district-level per-capita deposits and branch density to pincodes.

    deposits_per_capita  scales within a district using nightlights (proxy for
                          local economic density — we have no per-pincode deposit
                          figures to work with directly).
    bank_branches_per_lakh scales within a district using REAL public-sector-bank
                          branch counts (data/raw/rbi_branch_counts_india.csv,
                          parsed from RBI's branch master, pan-India) when
                          available for that district, falling back to the
                          nightlights proxy elsewhere. PSU branches undercount
                          the true branch network (no private/cooperative banks
                          in that source), but their relative distribution
                          across pincodes within a district is real data, not a
                          modeled proxy.
    """
    nl_path = RAW / "nightlights.csv"
    nl = pd.read_csv(nl_path).set_index("pincode") if nl_path.exists() else pd.DataFrame()

    branch_path = RAW / "rbi_branch_counts_india.csv"
    branch_counts = pd.DataFrame()
    if branch_path.exists():
        bc = pd.read_csv(branch_path, dtype={"pincode": str})
        branch_counts = bc.set_index("pincode")["psu_branch_count"]

    rows = []
    for _, row in pc_map.iterrows():
        pc       = str(row["pincode"])
        district = row["district"]

        if district not in bsr.index:
            log.debug("No BSR data for district %s (pincode %s)", district, pc)
            continue

        dep_base = float(bsr.loc[district, "per_capita_deposits"])

        # Within-district scale from nightlights (max ±40% swing)
        dep_scalar = 1.0
        if not nl.empty and int(pc) in nl.index:
            district_pincodes = pc_map[pc_map["district"] == district]["pincode"].astype(int).tolist()
            nl_vals = nl.loc[nl.index.isin(district_pincodes), "radiance_mean"]
            if len(nl_vals) >= 2:
                dep_scalar = _minmax_scalar(float(nl.loc[int(pc), "radiance_mean"]), nl_vals)

        branches_base = float(bsr.loc[district, "bank_branches_per_lakh"]) \
                         if "bank_branches_per_lakh" in bsr.columns else None

        # Prefer real branch-count share over the nightlights proxy for
        # bank_branches_per_lakh, when RBI branch-master data covers this district.
        branches = branches_base
        if branches_base is not None and not branch_counts.empty:
            rbi_district = RBI_DISTRICT_ALIASES.get(district, district.upper())
            district_pincodes = pc_map[pc_map["district"] == district]["pincode"].astype(str).tolist()
            bc_vals = branch_counts.loc[branch_counts.index.isin(district_pincodes)]
            if pc in bc_vals.index and len(bc_vals) >= 2 and bc_vals.nunique() > 1:
                branch_scalar = _minmax_scalar(float(bc_vals.loc[pc]), bc_vals)
                branches = round(branches_base * branch_scalar, 1)
            elif pc in bc_vals.index:
                log.debug("Only one RBI-covered pincode in %s (%s) — keeping district value", district, rbi_district)

        rows.append({
            "pincode"               : pc,
            "deposits_per_capita"   : round(dep_base * dep_scalar, 0),
            "bank_branches_per_lakh": branches,
        })

    return pd.DataFrame(rows).set_index("pincode")


# ── Pan-India bank_branches_per_lakh backfill ─────────────────────────────────
# build_pincode_deposits() above only computes anything for the ~130 pincodes
# in pincode_district_map.csv whose district also has a base entry in the
# 14-district rbi_bsr_district_2023.csv. But rbi_branch_counts_india.csv (real
# PSU branch counts, see parse_rbi_branch_master.py) is genuinely pan-India —
# 773 districts. The gate was never the branch data; it was having a
# population figure to convert branch counts into a per-lakh rate. Census
# district population (fetch_district_population.py) plugs that gap for any
# district, independent of the BSR/pincode_district_map coverage above.
#
# Names differ between RBI's branch-master export and Wikipedia's Census
# tables often enough to need a small alias list (spelling variants, renamed
# districts) — checked empirically: normalized exact-match alone resolves
# ~85% of branch-master districts against the Census table; these aliases
# for common cases push current live-dataset coverage further. Not
# exhaustive — anything not listed here just falls back to no branch data
# for that pincode, same as today.
CENSUS_DISTRICT_ALIASES = {
    "BENGALURU URBAN": "BANGALORE URBAN",
    "BENGALURU RURAL": "BANGALORE RURAL",
    "BENGALURU SOUTH": "BANGALORE URBAN",   # newly split out, no separate Census row
    "MYSURU": "MYSORE",
    "DAKSHIN KANNAD": "DAKSHINA KANNADA",
    "CHHATRAPATI SAMBHAJINAGAR": "AURANGABAD",
    "NASIK": "NASHIK",
    "DEHRA DUN": "DEHRADUN",
    "KANCHEEPURAM": "KANCHIPURAM",
    "THIRUVALLUR": "TIRUVALLUR",
    "SABAR KANTHA": "SABARKANTHA",
    "BANAS KANTHA": "BANASKANTHA",
    "MUMBAI": "MUMBAI CITY",
    "BID": "BEED",
    "BAGALKOTE": "BAGALKOT",
    "BARA BANKI": "BARABANKI",
    "BALESHWAR": "BALASORE",
    "ANUGUL": "ANGUL",
    "BADGAM": "BUDGAM",
    "AHILYANAGAR": "AHMEDNAGAR",
    "ALAPUZHA": "ALAPPUZHA",
}


def _norm_district(s: str) -> str:
    s = str(s).upper()
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


# Census merged Dadra & Nagar Haveli with Daman & Diu into one UT in 2020;
# Census 2011 (and RBI's tables, which predate the merger) still list them
# separately — map both pre-merger names to the merged Wikipedia entry.
STATE_NAME_ALIASES = {
    "DADRA AND NAGAR HAVELI": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "DAMAN AND DIU": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "ANDAMAN AND NICOBAR ISLANDS": "ANDAMAN AND NICOBAR",
}


def _norm_state(s: str) -> str:
    s = str(s).upper().strip().rstrip("*")
    s = re.sub(r"\s*&\s*", " AND ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return STATE_NAME_ALIASES.get(s, s)


def backfill_branches_pan_india(already_covered: set) -> pd.DataFrame:
    """
    Real bank_branches_per_lakh for any currently-known pincode (i.e. already
    in pincode_coords.csv — we don't pre-populate ahead of the live dataset;
    see fetch_rbi_bsr.py's caller for why) whose district has both branch
    data and a Census population match, skipping pincodes build_pincode_deposits()
    already covered via the higher-quality BSR path.

    Returns a DataFrame indexed by pincode with just bank_branches_per_lakh —
    caller merges via combine_first so it only fills gaps, never overrides
    the BSR-sourced values.
    """
    if not (BRANCH_COUNTS.exists() and POP_REF.exists() and COORDS.exists()):
        log.info("Pan-India branch backfill skipped — missing branch counts, "
                 "population reference, or pincode_coords.csv")
        return pd.DataFrame()

    known_pincodes = set(pd.read_csv(COORDS, dtype={"pincode": str})["pincode"])
    bc = pd.read_csv(BRANCH_COUNTS, dtype={"pincode": str})
    bc = bc[bc["pincode"].isin(known_pincodes) & ~bc["pincode"].isin(already_covered)]
    if bc.empty:
        return pd.DataFrame()

    pop = pd.read_csv(POP_REF)
    pop["norm"] = pop["district"].apply(_norm_district)
    pop_rate = pop.drop_duplicates("norm").set_index("norm")["population"]

    bc["norm"] = bc["district"].apply(_norm_district)
    bc["norm"] = bc["norm"].apply(lambda n: CENSUS_DISTRICT_ALIASES.get(n, n))
    bc = bc[bc["norm"].isin(pop_rate.index)]
    if bc.empty:
        return pd.DataFrame()

    # District-level rate: total known PSU branches / Census population.
    # Uses ALL branch-master rows for that district (not just known
    # pincodes) for a more representative district total, matching how
    # rbi_bsr_district_2023.csv's own figures are district-wide totals.
    all_bc = pd.read_csv(BRANCH_COUNTS, dtype={"pincode": str})
    all_bc["norm"] = all_bc["district"].apply(_norm_district)
    all_bc["norm"] = all_bc["norm"].apply(lambda n: CENSUS_DISTRICT_ALIASES.get(n, n))
    district_totals = all_bc.groupby("norm")["psu_branch_count"].sum()
    district_rate = (district_totals / pop_rate.reindex(district_totals.index) * 100000)

    rows = []
    for district_norm, grp in bc.groupby("norm"):
        base_rate = district_rate.get(district_norm)
        if base_rate is None or pd.isna(base_rate):
            continue
        district_pcs = all_bc[all_bc["norm"] == district_norm].set_index("pincode")["psu_branch_count"]
        for _, r in grp.iterrows():
            pc = r["pincode"]
            if len(district_pcs) >= 2 and district_pcs.nunique() > 1:
                scalar = _minmax_scalar(float(r["psu_branch_count"]), district_pcs)
                rate = round(base_rate * scalar, 1)
            else:
                rate = round(base_rate, 1)
            rows.append({"pincode": pc, "bank_branches_per_lakh": rate})

    return pd.DataFrame(rows).set_index("pincode")


# ── State-level Handbook of Statistics fallback ───────────────────────────────
# See module docstring for why this is manual-export-only. STATE-level real
# data is coarser than the 14-district baseline above, but is layered in as
# a fallback for every OTHER district — real RBI figures instead of the flat
# column-median ml_refinement.py falls back to today.

def _find_header_row(df: pd.DataFrame, keyword: str = "state") -> "int | None":
    # A column-label cell like "Region/State/Union Territory", not a full
    # title sentence like "TABLE 155: STATE-WISE DEPOSITS BY SCHEDULED
    # COMMERCIAL BANKS IN INDIA" (which also contains the keyword) — cap
    # length so the title row doesn't match first. 40 fits the former
    # (29 chars) but not the latter (71 chars) — verified against real
    # RBI Handbook of Statistics downloads (Tables 153/155), 2026-07-15.
    for i, row in df.iterrows():
        if any(keyword in str(v).lower() and len(str(v)) <= 40 for v in row.values):
            return i
    return None


def load_handbook_state_table(xlsx_path: Path) -> pd.Series:
    """
    Parse an RBI "Handbook of Statistics on Indian States" table (deposits,
    or credit-deposit ratio) into a state-name(upper) -> value Series.

    Verified against real downloads of Tables 153 and 155 (2026-07-15).
    RBI splits multi-decade series across two sheets with identical layout
    (e.g. "T_155(i)" covering 2004-2014, "T_155(ii)" covering 2015-2025) —
    this scans every sheet and keeps whichever single year column is most
    recent overall. Year columns are plain 4-digit integers, not "YYYY-YY"
    fiscal-year strings. The state-name column is literally labelled
    "Region/State/Union Territory", and region subtotal rows (e.g.
    "NORTHERN REGION") are interleaved with real state rows — excluded
    alongside the "ALL INDIA" total and the trailing "Source: ..." row.
    Missing values are the literal string "-", which pd.to_numeric coerces
    to NaN and drops.
    """
    import openpyxl  # noqa: F401
    xl = pd.ExcelFile(xlsx_path, engine="openpyxl")

    best_year = None
    best_series = None
    for sheet in xl.sheet_names:
        raw = xl.parse(sheet, header=None)
        hdr_row = _find_header_row(raw, "state")
        if hdr_row is None:
            continue

        raw.columns = raw.iloc[hdr_row]
        df = raw.iloc[hdr_row + 1:].reset_index(drop=True)
        df.columns = [str(c).strip() for c in df.columns]

        state_col = next((c for c in df.columns if "state" in c.lower()), None)
        year_cols = [c for c in df.columns if re.match(r"^\d{4}(-\d{2})?$", str(c).strip())]
        if not state_col or not year_cols:
            continue

        for yc in year_cols:
            year_num = int(str(yc).strip()[:4])
            if best_year is not None and year_num <= best_year:
                continue
            sub = df[[state_col, yc]].dropna()
            sub[yc] = pd.to_numeric(sub[yc], errors="coerce")
            sub = sub.dropna(subset=[yc])
            sub = sub[~sub[state_col].astype(str).str.contains(
                r"all.india|region\s*$|^total|^note|^source", case=False, regex=True)]
            if sub.empty:
                continue
            sub["state_norm"] = sub[state_col].apply(_norm_state)
            sub = sub.drop_duplicates("state_norm")
            best_year, best_series = year_num, sub.set_index("state_norm")[yc]

    if best_series is None:
        raise ValueError(
            f"Could not identify state/year columns in any sheet of {xlsx_path.name} "
            f"— sheets found: {xl.sheet_names}"
        )
    log.info("Handbook table %s: using year %d (%d states, sheets: %s)",
              xlsx_path.name, best_year, len(best_series), xl.sheet_names)
    return best_series


def backfill_deposits_state_level(deposits_xlsx: Path, already_covered: set) -> pd.DataFrame:
    """
    State-level deposits_per_capita for any currently-known pincode whose
    district isn't in the precise BSR baseline. Real RBI Handbook deposits
    (₹ crore) ÷ Census state population (aggregated from
    district_population_census.csv).
    """
    if not (deposits_xlsx.exists() and POP_REF.exists() and COORDS.exists()):
        log.info("State-level deposits backfill skipped — missing inputs")
        return pd.DataFrame()

    state_deposits_cr = load_handbook_state_table(deposits_xlsx)  # ₹ crore

    pop = pd.read_csv(POP_REF)
    state_pop = pop.groupby(pop["state_name"].apply(_norm_state))["population"].sum()

    common = state_deposits_cr.index.intersection(state_pop.index)
    log.info("State deposits: %d/%d RBI states matched to Census population "
             "(unmatched: %s)", len(common), len(state_deposits_cr),
             sorted(set(state_deposits_cr.index) - set(state_pop.index))[:10])
    if len(common) == 0:
        return pd.DataFrame()

    # ₹ crore -> ₹, then per person
    per_capita_by_state = (state_deposits_cr.loc[common] * 1e7 / state_pop.loc[common]).round(0)

    known = pd.read_csv(COORDS, dtype={"pincode": str})
    branch = pd.read_csv(BRANCH_COUNTS, dtype={"pincode": str}) if BRANCH_COUNTS.exists() else pd.DataFrame()
    if branch.empty:
        return pd.DataFrame()
    branch = branch[branch["pincode"].isin(set(known["pincode"]) - already_covered)]
    branch["state_norm"] = branch["state"].apply(_norm_state)

    rows = []
    for _, r in branch.iterrows():
        rate = per_capita_by_state.get(r["state_norm"])
        if rate is not None and not pd.isna(rate):
            rows.append({"pincode": r["pincode"], "deposits_per_capita": rate})

    return pd.DataFrame(rows).drop_duplicates("pincode").set_index("pincode")


def write_output(deposits: pd.DataFrame, dry_run: bool = False) -> None:
    out_path = RAW / "bank_deposits.csv"
    if out_path.exists():
        existing = pd.read_csv(out_path).set_index("pincode")
        existing.index = existing.index.astype(str)
        deposits.index = deposits.index.astype(str)
        # combine_first adds new columns + fills missing rows
        out = deposits.combine_first(existing)
    else:
        out = deposits

    if dry_run:
        log.info("[DRY RUN] bank_deposits.csv head:\n%s", out.head(10))
        return

    out.to_csv(out_path)
    log.info("Written: %s (%d rows)", out_path.name, len(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", type=Path, help="Path to downloaded RBI BSR-2 Excel file")
    ap.add_argument("--deposits-xlsx", type=Path,
                     help="Path to RBI Handbook Table 155 (State-wise Deposits) XLSX")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pc_map   = pd.read_csv(PC_MAP)
    bsr      = load_bsr_district(excel_path=args.excel)
    deposits = build_pincode_deposits(bsr, pc_map)
    log.info("RBI BSR deposits computed for %d pincodes", len(deposits))

    branches_pan_india = backfill_branches_pan_india(already_covered=set(deposits.index))
    if not branches_pan_india.empty:
        deposits = deposits.combine_first(branches_pan_india)
        log.info("Pan-India branch backfill: bank_branches_per_lakh for %d more pincodes",
                 len(branches_pan_india))

    if args.deposits_xlsx:
        # Exclude pincodes that already have a real deposits_per_capita
        # value specifically — not every pincode with *any* row in
        # `deposits`, which after the branches backfill above includes
        # pincodes that only got bank_branches_per_lakh set (a different
        # column). Using the full index here would wrongly block those
        # pincodes from ever getting a real deposits figure.
        already_have_deposits = (
            set(deposits.index[deposits["deposits_per_capita"].notna()])
            if "deposits_per_capita" in deposits.columns else set()
        )
        deposits_state = backfill_deposits_state_level(
            args.deposits_xlsx, already_covered=already_have_deposits)
        if not deposits_state.empty:
            deposits = deposits.combine_first(deposits_state)
            log.info("State-level Handbook backfill: deposits_per_capita for %d more pincodes",
                     len(deposits_state))

    write_output(deposits, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
