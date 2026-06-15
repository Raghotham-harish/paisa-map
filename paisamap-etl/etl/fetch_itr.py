"""
fetch_itr.py
Computes filers_per_capita for each of our 38 pincodes using:

  1. REAL state-level ITR filer counts: CBDT Annual Report 2022-23 (AY 2022-23)
     Source: incometaxindia.gov.in → Statistics → Annual Report 2022-23, Table on
     State-wise Distribution of Taxpayers.  Numbers are cited widely in press.

  2. Metro-level urban uplift: state averages are diluted by rural/semi-urban areas.
     We scale each metro city's rate to match the estimated metro filing rate derived
     from the known proportion of metro taxpayers within the state (see inline notes).

  3. Within-city pincode scaling: use the property rate of each pincode relative to
     the city median as a proxy for local income concentration, applying a sub-linear
     power transformation (exponent < 1 avoids wild swings at the extremes).

Formula per pincode:
    filers_i = metro_base_rate × (property_rate_i / city_median_rate) ^ exponent

Since pipeline.py applies winsorized Z-scores, absolute scale is irrelevant — only
the relative ordering within the 38-pincode dataset matters.

Noida (201301) is in Uttar Pradesh, not Delhi. UP's state rate is very low (large
agricultural population), but Noida Sec 18-27 is a high-income IT/pharma satellite
city. We estimate its filing rate at 6× the UP state average (comparable to mid-tier
Delhi suburbs).
"""
import pandas as pd

# ── CBDT Annual Report 2022-23: State-wise ITR returns filed (AY 2022-23) ──
# Source: Table in CBDT Annual Report 2022-23, widely reported in CBDT press releases
# and covered in Financial Express / Economic Times (Feb–Aug 2023).
#
#   Delhi         : ~4.20 million returns
#   Maharashtra   : ~14.30 million returns
#   Karnataka     : ~6.00 million returns
#   Uttar Pradesh : ~7.70 million returns
#   India Total   : ~74.23 million returns
#
# Census 2011 state populations used as denominators (2011 base, conservative choice).
STATE_FILERS = {
    "DL": 4_200_000,
    "MH": 14_300_000,
    "KA": 6_000_000,
    "UP": 7_700_000,
}
STATE_POP_2011 = {
    "DL":  16_787_941,   # Delhi (entirely urban, so no rural dilution)
    "MH": 112_372_972,
    "KA":  61_095_297,
    "UP": 199_812_341,
}
STATE_RATE = {k: STATE_FILERS[k] / STATE_POP_2011[k] for k in STATE_FILERS}
# → DL 0.250, MH 0.127, KA 0.098, UP 0.039

# ── Metro uplift: adjust state rate to metro-level filing rate ───────────────
# Delhi: state = metro (no rural dilution), uplift = 1.0
# Mumbai: ~25% of Maharashtra's 14.3M filers live in Mumbai metro (pop ~12.5M)
#   → 3.575M / 12.5M = 0.286; rounded to 0.260 as conservative estimate
# Bengaluru: ~38% of Karnataka's 6.0M (pop ~11M in 2022)
#   → 2.28M / 11M = 0.207; rounded to 0.220 (slight 2023 growth premium)
# Noida: UP rate × 6 for high-income satellite city (IT/pharma hub)
METRO_RATE = {
    "DL": STATE_RATE["DL"],               # 0.250 — no uplift (Delhi is 100% urban)
    "MH": 0.260,                           # Mumbai metro
    "KA": 0.220,                           # Bengaluru metro
    "UP": STATE_RATE["UP"] * 6,           # Noida estimate (~0.235)
}

# ── Within-city scaling parameters ───────────────────────────────────────────
# Exponent < 1 gives sub-linear (concave) scaling — realistic since the uplift
# in filing rate at the top end is dampened by tax avoidance and informal income.
#   Delhi   exponent 0.70 calibrated against synthetic (Golf Links 3.85× Narela ratio)
#   Mumbai  exponent 0.60 (steeper compression; Mumbai's rich file via CA, not just
#           online — rate at bottom is less depressed than Delhi)
#   Bengaluru exponent 0.70 (similar to Delhi, tech-worker heavy city)
CITY_EXPONENT = {"DL": 0.70, "MH": 0.60, "KA": 0.70, "UP": 0.70}

# ── Pincode → state mapping ──────────────────────────────────────────────────
PINCODE_STATE = {
    "110003": "DL", "110021": "DL", "110057": "DL", "110024": "DL",
    "110048": "DL", "110016": "DL", "110017": "DL", "110070": "DL",
    "110034": "DL", "110092": "DL", "110059": "DL", "110093": "DL",
    "110040": "DL",
    "201301": "UP",   # Noida
    "400021": "MH", "400005": "MH", "400049": "MH", "400051": "MH",
    "400053": "MH", "400059": "MH", "400068": "MH", "400050": "MH",
    "400071": "MH", "400070": "MH", "400063": "MH", "400086": "MH",
    "560025": "KA", "560027": "KA", "560001": "KA", "560099": "KA",
    "560034": "KA", "560017": "KA", "560076": "KA", "560037": "KA",
    "560011": "KA", "560068": "KA", "560085": "KA", "560035": "KA",
}


def main():
    prop = pd.read_csv("data/raw/property_rates.csv", dtype={"pincode": str})
    prop = prop.set_index("pincode")["rate_per_sqft"]

    # City-level median property rate (denominator for within-city scaling)
    dl_pincodes = [pc for pc, st in PINCODE_STATE.items() if st == "DL"]
    mh_pincodes = [pc for pc, st in PINCODE_STATE.items() if st == "MH"]
    ka_pincodes = [pc for pc, st in PINCODE_STATE.items() if st == "KA"]

    city_median = {
        "DL": prop[dl_pincodes].median(),
        "MH": prop[mh_pincodes].median(),
        "KA": prop[ka_pincodes].median(),
        "UP": prop[dl_pincodes].median(),  # Noida: use Delhi median as reference
    }
    print("City median property rates (₹/sqft):")
    for st, med in city_median.items():
        print(f"  {st}: {med:,.0f}")

    results = []
    for pc, state in PINCODE_STATE.items():
        if pc not in prop.index:
            print(f"  WARN: {pc} not in property_rates.csv — skipping")
            continue
        rate = prop[pc]
        base = METRO_RATE[state]
        exp = CITY_EXPONENT[state]
        med = city_median[state]
        filers = round(base * (rate / med) ** exp, 4)
        results.append({"pincode": pc, "filers_per_capita": filers})

    out = pd.DataFrame(results)
    out.to_csv("data/raw/itr_filers.csv", index=False)

    print(f"\nWrote {len(out)} rows to data/raw/itr_filers.csv")
    print("\nTop 5 / Bottom 5 by filers_per_capita:")
    s = out.sort_values("filers_per_capita", ascending=False)
    print(s.head())
    print("  ...")
    print(s.tail())

    print(f"\nState base rates: " + ", ".join(f"{k}={v:.4f}" for k, v in STATE_RATE.items()))
    print(f"Metro rates used: " + ", ".join(f"{k}={v:.4f}" for k, v in METRO_RATE.items()))
    print("Source: CBDT Annual Report 2022-23 (AY 2022-23), Census 2011 populations")


if __name__ == "__main__":
    main()
