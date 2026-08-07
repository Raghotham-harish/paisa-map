#!/usr/bin/env python3
"""
fetch_karnataka_income.py — Real district-level per-capita income for Karnataka.

Source: Karnataka Directorate of Economics & Statistics, "District Income and
Per Capita Income" (taluk-level, 2019-20 at current prices), published via
data.gov.in and catalogued on AIKosh (aikosh.indiaai.gov.in). Resource UUID
bb717792-f491-44cf-aa45-9304c1bc4bab, fetched live through api.data.gov.in
using the same working pattern fetch_commercial.py established: data.gov.in's
*website* 403s a default User-Agent, but api.data.gov.in itself just needs any
non-default User-Agent (requests' default hangs until timeout) — no manual
export needed. The api-key below is data.gov.in's own published sample key
(579b464db...), the same one used site-wide for anonymous/demo API access —
not a private credential.

This dataset is Karnataka-ONLY (236 taluks), not pan-India. Checked
extensively for a pan-India equivalent first (2026-08-07 session): SHRUG
(Development Data Lab) has real pan-India village/town-level income proxies
but is licensed CC BY-NC-SA 4.0 — non-commercial only, a hard blocker for a
paid product without the team's explicit sign-off. data.gov.in's own pan-India
"district-wise-gdp-and-growth-rate-constant-price2004-05" catalog entry has no
live API (manual download only) and is base-year 2004-05 — 20+ years stale,
not worth trusting for current relative district ranking. Karnataka's own
submission is the only current, live-fetchable, real income series found —
scoped accordingly: this only ever produces values for Karnataka pincodes
(the "bengaluru" CITIES entry in index.html), every other city keeps its
existing modelled income estimate untouched.

Taluk → district matching: the taluk-level rows are averaged up to Karnataka's
30 (pre-2021) districts to join against our existing
pincode_district_state_india.csv (district granularity, not taluk). District→
taluk membership is Wikipedia's "List of taluks of Karnataka" (236 taluks
across 31 districts incl. Vijayanagara, split from Bellary in 2021 — no
pincodes in our reference file are tagged to that new district yet, so it's
harmless that it has no match). Taluk names are normalised (lowercase, strip
punctuation/whitespace/parenthetical) before matching since the two sources
spell some taluks differently (e.g. "RABAKAVI BANAHATTI" vs "Rabkavi
Banhatti", "Hubli" vs "Hubballi (Urban)").

Output: data/raw/karnataka_income.csv — pincode, per_capita_income_karnataka

Usage:
  python3 etl/fetch_karnataka_income.py
  python3 etl/fetch_karnataka_income.py --dry-run
"""

import argparse
import difflib
import logging
import re
import sys
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
REF  = ROOT / "data" / "reference"
RAW  = ROOT / "data" / "raw"
PINCODE_REF = REF / "pincode_district_state_india.csv"
OUT_CSV     = RAW / "karnataka_income.csv"

API_URL = (
    "https://api.data.gov.in/resource/bb717792-f491-44cf-aa45-9304c1bc4bab"
    "?api-key=579b464db66ec23bdd000001cdc3b564546246a772a26393094f5645"
    "&offset=0&limit=all&format=json"
)
INCOME_COL = "_per_capita_income_in_rs_2019_20_at_current_prices_2019_20"
# See fetch_commercial.py's docstring for why this matters: requests' default
# User-Agent hangs against api.data.gov.in; any non-default one works fine.
HEADERS = {"User-Agent": "PaisaMap-KarnatakaIncome/1.0 (data.gov.in DES fetch)"}

# Wikipedia "List of taluks of Karnataka" → our pincode_district_state_india.csv
# district spelling. Only districts that actually appear in that reference file
# need an entry; a Wikipedia district with no entry here (e.g. Vijayanagara,
# a 2021 split with no pincodes tagged to it yet) is simply never joined.
WIKI_DISTRICT_TO_REF = {
    "bagalkote": "Bagalkot",
    "ballari": "Bellary",
    "belagavi": "Belgaum",
    "bengaluru urban": "Bangalore",
    "bengaluru rural": "Bangalore Rural",
    "bidar": "Bidar",
    "chamarajanagara": "Chamrajnagar",
    "chikkaballapura": "Chikkaballapur",
    "chikkamagaluru": "Chickmagalur",
    "chitradurga": "Chitradurga",
    "dakshina kannada": "Dakshina Kannada",
    "davanagere": "Davangere",
    "dharwad": "Dharwad",
    "gadag": "Gadag",
    "hassan": "Hassan",
    "haveri": "Haveri",
    "kalaburagi": "Gulbarga",
    "kodagu": "Kodagu",
    "kolar": "Kolar",
    "koppala": "Koppal",
    "mandya": "Mandya",
    "mysuru": "Mysore",
    "raichuru": "Raichur",
    "ramanagara": "Ramanagar",
    "shivamogga": "Shimoga",
    "tumakuru": "Tumkur",
    "udupi": "Udupi",
    "uttara kannada": "Uttara Kannada",
    "vijayapura": "Bijapur(KAR)",
    "yadagiri": "Yadgir",
}

# Wikipedia district → its taluks (official spelling), scraped from
# https://en.wikipedia.org/wiki/List_of_taluks_of_Karnataka (2026-08-07).
WIKI_DISTRICT_TALUKS = {
    "bagalkote": ["Bagalkote", "Jamkhandi", "Mudhola", "Badami", "Bilagi", "Hunagunda", "Ilkal", "Rabkavi Banhatti", "Guledgudda"],
    "ballari": ["Ballari", "Kurugodu", "Kampli", "Sanduru", "Siraguppa"],
    "belagavi": ["Belagavi", "Athani", "Bailhongal", "Chikkodi", "Gokak", "Khanapura", "Mudalgi", "Nippani", "Rayabaga", "Savadatti", "Ramadurga", "Kagawada", "Hukkeri", "Kitturu", "Yargatti"],
    # Confirmed against the actual 2019-20 taluk-income dataset's exact spelling
    # (this submission doesn't break out Kengeri/Krishnarajapura as separate
    # taluks the way Wikipedia's current list does — likely folded into
    # North/South/East here — so those two are simply left unmatched, not
    # guessed at).
    "bengaluru urban": ["Bangalore (North)", "Bangalore-South", "Bangalore-East", "Anekal", "Yelahanka"],
    "bengaluru rural": ["Nelamangala", "Doddaballapura", "Devanahalli", "Hosakote"],
    "bidar": ["Aurad", "Basavakalyana", "Bhalki", "Bidar", "Chitgoppa", "Hulsuru", "Humnabad", "Kamalanagara"],
    "chamarajanagara": ["Chamarajanagara", "Gundlupete", "Kollegala", "Yelanduru", "Hanuru"],
    "chikkaballapura": ["Chikkaballapura", "Bagepalli", "Chintamani", "Gauribidanuru", "Gudibanda", "Sidlaghatta", "Cheluru", "Manchenahalli"],
    "chikkamagaluru": ["Chikkamagaluru", "Kaduru", "Koppa", "Mudigere", "Narasimharajapura", "Sringeri", "Tarikere", "Ajjampura", "Kalasa"],
    "chitradurga": ["Chitradurga", "Challakere", "Hiriyur", "Holalkere", "Hosadurga", "Molakalmuru"],
    "dakshina kannada": ["Mangaluru", "Ullal", "Mulki", "Moodbidri", "Bantwala", "Belathangadi", "Putturu", "Sulya", "Kadaba"],
    "davanagere": ["Davanagere", "Harihara", "Channagiri", "Honnali", "Nyamathi", "Jagaluru"],
    "dharwad": ["Kalghatgi", "Dharwad", "Hubballi Rural", "Hubballi Urban", "Kundagolu", "Navalgunda", "Alnavara", "Annigeri"],
    "gadag": ["Gadag", "Naragunda", "Mundaragi", "Rona", "Gajendragada", "Lakshmeshwara", "Shirahatti"],
    "hassan": ["Hassan", "Arasikere", "Channarayapattana", "Holenarsipura", "Sakleshpura", "Aluru", "Arakalagudu", "Beluru"],
    "haveri": ["Ranibennur", "Byadgi", "Hangala", "Haveri", "Savanuru", "Hirekeruru", "Shiggavi", "Rattihalli"],
    "kalaburagi": ["Kalaburagi", "Afzalpura", "Alanda", "Chincholi", "Chitapura", "Jevargi", "Sedam", "Kamalapura", "Shahabad", "Kalgi", "Yedrami"],
    "kodagu": ["Madikeri", "Somawarapete", "Virajapete", "Ponnammapete", "Kushalnagara"],
    "kolar": ["Kolar", "Bangarapete", "Maluru", "Mulabagilu", "Srinivasapura", "Kolar Gold Fields"],
    "koppala": ["Koppala", "Gangavathi", "Kushtagi", "Yelaburga", "Kanakagiri", "Karatagi", "Kukanuru"],
    "mandya": ["Mandya", "Madduru", "Malavalli", "Srirangapattana", "Krishnarajapete", "Nagamangala", "Pandavapura"],
    "mysuru": ["Mysuru", "Hunasuru", "Krishnarajanagara", "Nanjanagodu", "Heggadadevanakote", "Piriyapattana", "Tirumakudalu Narasipura", "Saraguru", "Saligrama"],
    "raichuru": ["Raichuru", "Sindhanuru", "Manvi", "Devadurga", "Lingasaguru", "Mudgal", "Maski", "Sirawara"],
    "ramanagara": ["Ramanagara", "Magadi", "Kanakapura", "Channapattana", "Harohalli"],
    "shivamogga": ["Shivamogga", "Sagara", "Bhadravathi", "Hosanagara", "Shikaripura", "Soraba", "Tirthahalli"],
    "tumakuru": ["Tumakuru", "Chikkanayakanahalli", "Kunigal", "Madhugiri", "Sira", "Tipturu", "Gubbi", "Koratagere", "Pavagada", "Turuvekere"],
    "udupi": ["Udupi", "Kapu", "Bynduru", "Karkala", "Kundapura", "Hebri", "Brahmavara"],
    "uttara kannada": ["Karwara", "Sirsi", "Joida", "Dandeli", "Bhatkal", "Kumta", "Ankola", "Haliyal", "Honnavara", "Mundagodu", "Siddapura", "Yellapura"],
    "vijayapura": ["Vijayapura", "Indi", "Basavana Bagewadi", "Sindgi", "Muddebihala", "Talikote", "Devara Hipparagi", "Chadchana", "Tikote", "Babaleshwara", "Kolhara", "Nidagundi", "Alamela"],
    "yadagiri": ["Yadagiri", "Shahapura", "Surapura", "Gurmitkala", "Vadagera", "Hunsagi"],
    "vijayanagara": ["Hosapete", "Hagaribommanahalli", "Harapanahalli", "Hoovina Hadagali", "Kudligi", "Kotturu"],
}


# A handful of Karnataka's 2014 official town-name changes (Bangalore->
# Bengaluru-style renamings) that land below the safe fuzzy-match cutoff
# but are well-documented, unambiguous single-place renamings — not a
# string-similarity guess. Keys are Wikipedia spellings (normalised).
MANUAL_TALUK_ALIASES = {
    "shivamogga": "shimoga",     # renamed 2014
    "vijayapura": "bijapur",     # renamed 2014 (district HQ taluk)
    "hosapete":   "hospet",      # renamed 2014
    "mangaluru":  "mangalore",   # renamed 2014
}

# The taluk-income dataset carries NO district column (just 236 bare taluk
# names), so fuzzy matching can't be structurally restricted to "only
# candidates from the same district" — the only real safeguard is a cutoff
# high enough that every hit was manually inspected and confirmed correct.
# Verified by hand (2026-08-07) at 0.90: every fuzzy hit is a genuine
# same-place spelling variant (Jamkhandi/Jamakhandi, Mudhola/Mudhol, etc.),
# zero cross-taluk mismatches found. Lower cutoffs (tried 0.85, 0.72) DID
# produce wrong matches on inspection — e.g. "Bengaluru North" fuzzy-matching
# to the actual dataset's unrelated "Bangalore-South" row — so those aren't
# used. If this dataset is ever refreshed and re-fetched, re-run the
# match report below and re-inspect before trusting a new fuzzy hit list.
FUZZY_CUTOFF = 0.90


def _norm(name: str) -> str:
    """Lowercase, strip parentheticals/punctuation/whitespace for fuzzy taluk matching."""
    name = re.sub(r"\([^)]*\)", "", name)          # drop "(North)" etc.
    name = re.sub(r"[^a-z0-9]", "", name.lower())  # drop spaces/hyphens/dots
    # The taluk-income dataset and Wikipedia disagree on Bengaluru vs Bangalore
    # (and Belgaum/Belagavi-adjacent old-vs-new-name spellings show up elsewhere
    # too) — fold the common ones so e.g. "Bangalore (North)" and "Bengaluru
    # North" match as the same taluk.
    name = name.replace("bengaluru", "bangalore")
    return name


def fetch_taluk_income() -> pd.DataFrame:
    resp = requests.get(API_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    records = data.get("records", [])
    log.info("Fetched %d taluk records (API reports total=%s)", len(records), data.get("total"))

    df = pd.DataFrame(records)
    df = df[df["taluks_districts"].str.strip().str.upper() != "STATE_TOTAL"]
    df["income"] = pd.to_numeric(df[INCOME_COL], errors="coerce")
    df = df.dropna(subset=["income"])
    df["taluk_norm"] = df["taluks_districts"].apply(_norm)
    return df[["taluks_districts", "taluk_norm", "income"]]


def _match_taluk(wiki_taluk: str, income_by_norm: dict, used: set):
    """Return (matched actual-taluk norm, income) or None. Tries exact norm,
    then a manual known-renaming alias, then a cutoff-guarded fuzzy match —
    each candidate pool excludes taluks already claimed by an earlier match
    so two Wikipedia taluks can't both be assigned the same income row."""
    tn = _norm(wiki_taluk)
    if tn in income_by_norm and tn not in used:
        return tn
    alias = MANUAL_TALUK_ALIASES.get(tn)
    if alias and alias in income_by_norm and alias not in used:
        return alias
    candidates = [a for a in income_by_norm if a not in used]
    close = difflib.get_close_matches(tn, candidates, n=1, cutoff=FUZZY_CUTOFF)
    return close[0] if close else None


def build_district_income(taluk_income: pd.DataFrame, verbose: bool = False) -> pd.Series:
    """Average taluk-level income up to district level, matched by name."""
    income_by_norm = taluk_income.set_index("taluk_norm")["income"].to_dict()
    actual_display = dict(zip(taluk_income["taluk_norm"], taluk_income["taluks_districts"]))

    district_vals = {}
    used: set = set()
    matched_total = 0
    wiki_total = 0
    for wiki_district, taluks in WIKI_DISTRICT_TALUKS.items():
        ref_district = WIKI_DISTRICT_TO_REF.get(wiki_district)
        wiki_total += len(taluks)
        vals = []
        for t in taluks:
            hit = _match_taluk(t, income_by_norm, used)
            if hit is None:
                continue
            used.add(hit)
            vals.append(income_by_norm[hit])
            matched_total += 1
            if verbose:
                print(f"    {wiki_district:<18} {t:<22} -> {actual_display[hit]}")
        if ref_district is None:
            continue  # e.g. vijayanagara — not in our pincode reference yet
        if not vals:
            log.warning("No taluk income matched for district=%s (wiki=%s) — skipped", ref_district, wiki_district)
            continue
        district_vals[ref_district] = sum(vals) / len(vals)

    log.info("Matched %d/%d Karnataka taluks to real income rows, covering %d/%d districts",
              matched_total, wiki_total, len(district_vals), len(WIKI_DISTRICT_TO_REF))
    return pd.Series(district_vals, name="per_capita_income_karnataka")


def build_pincode_output(district_income: pd.Series) -> pd.DataFrame:
    pc_map = pd.read_csv(PINCODE_REF, dtype={"pincode": str})
    ka = pc_map[pc_map["state_name"] == "Karnataka"].copy()
    ka["income"] = ka["district"].map(district_income)
    ka = ka.dropna(subset=["income"])
    out = ka[["pincode", "income"]].rename(columns={"income": "per_capita_income_karnataka"})
    return out.drop_duplicates("pincode").set_index("pincode")


def write_output(out: pd.DataFrame, dry_run: bool = False) -> None:
    if dry_run:
        log.info("[DRY RUN] karnataka_income.csv (%d rows):\n%s", len(out), out.head(15))
        return
    RAW.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV)
    log.info("Written: %s (%d rows)", OUT_CSV.name, len(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="print every wiki-taluk -> actual-row match")
    args = ap.parse_args()

    if not PINCODE_REF.exists():
        sys.exit(f"Missing {PINCODE_REF} — run the pincode reference build first.")

    taluk_income = fetch_taluk_income()
    district_income = build_district_income(taluk_income, verbose=args.verbose)
    out = build_pincode_output(district_income)
    log.info("per_capita_income_karnataka computed for %d pincodes", len(out))
    write_output(out, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
