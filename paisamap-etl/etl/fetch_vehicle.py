"""
fetch_vehicle.py
Downloads real RTO-wise LMV (Light Motor Vehicle) registration data from
OpenCity India for Delhi, Mumbai, and Bengaluru (2021-2025), then maps
each of our 38 pincodes to its primary RTO and computes cars_per_1000.

Source: data.opencity.in (CC BY 4.0)
Method: cumulative new LMV registrations 2021-2025 ÷ RTO-area Census 2011 pop × 1000
Noida (UP): interpolated from East Delhi RTO neighbours (no public UP RTO data).
"""
import io
import time
import urllib.request
import pandas as pd

# OpenCity CSV URLs — one file per year per city (2021-2025)
DELHI_URLS = [
    "https://data.opencity.in/dataset/d9dfb9e3-2d13-4f78-b590-4a1157fbe77d/resource/19318a1f-22de-40ec-a2af-9be313f5aa2c/download/aaacdd04-398b-4e68-8fac-4da2d0b16376.csv",
    "https://data.opencity.in/dataset/d9dfb9e3-2d13-4f78-b590-4a1157fbe77d/resource/293a496c-8d77-4109-893c-cd9ff1158d99/download/a2747cd8-a56b-4a6e-bbfe-aa91d01383af.csv",
    "https://data.opencity.in/dataset/d9dfb9e3-2d13-4f78-b590-4a1157fbe77d/resource/2903ccbf-d971-4146-847a-30ba767e5bc1/download/1105a62c-ac6b-46ee-b623-a9b53c3b6912.csv",
    "https://data.opencity.in/dataset/d9dfb9e3-2d13-4f78-b590-4a1157fbe77d/resource/3959e0d7-d027-457d-a7ad-d62021d051c3/download/b0b291ae-9867-4027-a2ea-047494f5dd13.csv",
    "https://data.opencity.in/dataset/d9dfb9e3-2d13-4f78-b590-4a1157fbe77d/resource/b4536e08-5163-4e0a-b249-aecb0bdd30aa/download/0819c12f-e925-402c-be4c-decfe37ccf00.csv",
]
MUMBAI_URLS = [
    "https://data.opencity.in/dataset/48f30e56-bb29-4506-94c4-132aed4492b9/resource/a98d9232-9e8d-40be-b326-59399e451b12/download/4645bd7d-7fd0-42c1-8efb-2fc4cce4803a.csv",
    "https://data.opencity.in/dataset/48f30e56-bb29-4506-94c4-132aed4492b9/resource/8e7dadb0-81bb-4bb3-bf35-ebfc554123c3/download/4198a4cb-7c9f-4c28-9f96-6d946d3caa60.csv",
    "https://data.opencity.in/dataset/48f30e56-bb29-4506-94c4-132aed4492b9/resource/d4cfa71e-7b6c-44dc-ad7d-14425ce58e3b/download/abb8f161-69b6-4ace-a7d2-fd3c2632e52d.csv",
    "https://data.opencity.in/dataset/48f30e56-bb29-4506-94c4-132aed4492b9/resource/aa9e2268-a338-4c04-ae4a-4eb4f8405810/download/482c3bee-bf1a-4940-a5cb-08ae558d6389.csv",
    "https://data.opencity.in/dataset/48f30e56-bb29-4506-94c4-132aed4492b9/resource/48f9bce0-69ac-47e3-9c86-98f9d9331475/download/23b79bf2-e265-436d-9d09-91e4c7f3bc88.csv",
]
BENGALURU_URLS = [
    "https://data.opencity.in/dataset/71ab0845-b439-4c39-bf53-d157ae10bdef/resource/cdbd693d-2f1d-4fad-a43f-878f54f73cdf/download/e4fe1f99-a49d-4642-88b5-03c4479bc6be.csv",
    "https://data.opencity.in/dataset/71ab0845-b439-4c39-bf53-d157ae10bdef/resource/7d9c429d-45f9-42ae-a869-51415f51c769/download/c9e55b76-e0da-4864-96e4-91f383a96812.csv",
    "https://data.opencity.in/dataset/71ab0845-b439-4c39-bf53-d157ae10bdef/resource/76c2fd86-f061-4d56-a496-4733ddbeb9b0/download/32394726-4451-424f-9e2f-1c4c7b979240.csv",
    "https://data.opencity.in/dataset/71ab0845-b439-4c39-bf53-d157ae10bdef/resource/952d0b2f-63f5-4628-af82-b4e6ffaed605/download/a95a4755-b1a1-4c99-979f-2540080990df.csv",
    "https://data.opencity.in/dataset/71ab0845-b439-4c39-bf53-d157ae10bdef/resource/c4c2b458-1d7f-4da1-b7d1-c5a02fb0f133/download/da330e05-c93c-42a3-94ba-09ebd5dd28d3.csv",
]

# Pincode → (State code, RTO number)
# Derived from Delhi district/RTO maps, Mumbai East/West/Central split, Bengaluru zone RTOs
PINCODE_RTO = {
    "110003": ("DL",  3),   # Golf Links/Lodhi    → South Delhi
    "110021": ("DL", 12),   # Chanakyapuri        → Vasant Vihar (South West)
    "110057": ("DL", 12),   # Vasant Vihar        → Vasant Vihar
    "110024": ("DL",  3),   # Defence Colony      → South Delhi
    "110048": ("DL",  3),   # Greater Kailash     → South Delhi
    "110016": ("DL",  3),   # Hauz Khas           → South Delhi
    "110017": ("DL",  3),   # Saket/Malviya Nagar → South Delhi
    "110070": ("DL",  9),   # Vasant Kunj         → Dwarka (South West)
    "110034": ("DL", 11),   # Pitampura           → Rohini (North West)
    "110092": ("DL",  7),   # Preet Vihar         → Mayur Vihar (East)
    "110059": ("DL",  4),   # Uttam Nagar         → Janakpuri (West)
    "110093": ("DL", 13),   # Shahdara            → Surajmal Vihar (East)
    "110040": ("DL",  1),   # Narela              → Mall Road (North)
    "201301": ("UP", 99),   # Noida — no public UP RTO data; estimated below
    "400021": ("MH",  1),   # Cuffe Parade        → Mumbai Central
    "400005": ("MH",  1),   # Colaba              → Mumbai Central
    "400049": ("MH",  2),   # Bandra West         → Mumbai West
    "400051": ("MH",  2),   # Khar West           → Mumbai West
    "400053": ("MH",  2),   # Santacruz West      → Mumbai West
    "400059": ("MH",  2),   # Andheri West        → Mumbai West
    "400068": ("MH",  3),   # Powai               → Mumbai East
    "400050": ("MH",  3),   # Bandra East         → Mumbai East
    "400071": ("MH",  3),   # Chembur             → Mumbai East
    "400070": ("MH",  3),   # Ghatkopar           → Mumbai East
    "400063": ("MH", 47),   # Malad West          → Borivali
    "400086": ("MH", 47),   # Borivali West       → Borivali
    "560025": ("KA",  3),   # Indiranagar         → Bengaluru East
    "560027": ("KA",  5),   # Koramangala         → Bengaluru South
    "560001": ("KA",  1),   # MG Road/Cubbon      → Bengaluru Central
    "560099": ("KA", 51),   # Bellandur           → Electronic City RTO
    "560034": ("KA",  3),   # Whitefield          → Bengaluru East
    "560017": ("KA",  5),   # HSR Layout          → Bengaluru South
    "560076": ("KA",  2),   # Malleshwaram        → Bengaluru West
    "560037": ("KA",  3),   # Marathahalli        → Bengaluru East
    "560011": ("KA",  5),   # Jayanagar           → Bengaluru South
    "560068": ("KA", 41),   # Vijayanagar         → Jnanabharathi
    "560085": ("KA",  5),   # Banashankari        → Bengaluru South
    "560035": ("KA", 51),   # Electronic City     → Electronic City RTO
}

# RTO → Census 2011 population estimate
# Delhi: district populations split proportionally across co-district RTOs
# Mumbai: Mumbai City district + estimated splits of Mumbai Suburban (9.36M)
# Bengaluru: Urban district (9.62M) divided equally across 8 RTOs
RTO_POP = {
    ("DL",  1):   887_978,   # North Delhi district
    ("DL",  2):   142_004,   # New Delhi district
    ("DL",  3): 2_731_929,   # South Delhi district
    ("DL",  4): 1_271_621,   # West Delhi ÷ 2 (Janakpuri half)
    ("DL",  5): 2_241_624,   # North East Delhi district
    ("DL",  6):   291_160,   # Central Delhi ÷ 2 (Sarai Kale Khan half)
    ("DL",  7):   854_673,   # East Delhi ÷ 2 (Mayur Vihar half)
    ("DL",  8): 1_828_270,   # North West Delhi ÷ 2 (Wazirpur half)
    ("DL",  9): 1_146_479,   # South West Delhi ÷ 2 (Dwarka half)
    ("DL", 10): 1_271_622,   # West Delhi ÷ 2 (Rajouri Garden half)
    ("DL", 11): 1_828_269,   # North West Delhi ÷ 2 (Rohini half)
    ("DL", 12): 1_146_479,   # South West Delhi ÷ 2 (Vasant Vihar half)
    ("DL", 13):   854_673,   # East Delhi ÷ 2 (Surajmal Vihar half)
    ("MH",  1): 3_085_411,   # Mumbai City district
    ("MH",  2): 3_700_000,   # Mumbai Suburban — western half
    ("MH",  3): 3_500_000,   # Mumbai Suburban — eastern half
    ("MH", 47): 2_156_962,   # Mumbai Suburban — Borivali north
    ("KA",  1): 1_202_694,   # Bengaluru Urban ÷ 8
    ("KA",  2): 1_202_694,
    ("KA",  3): 1_202_694,
    ("KA",  4): 1_202_694,
    ("KA",  5): 1_202_694,
    ("KA", 41): 1_202_694,
    ("KA", 50): 1_202_694,
    ("KA", 51): 1_202_694,
}


def fetch_csv(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PaisaMap-ETL/2.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return pd.read_csv(io.BytesIO(resp.read()))
    except Exception as e:
        print(f"  WARN: {e}")
        return None


def main():
    all_urls = DELHI_URLS + MUMBAI_URLS + BENGALURU_URLS
    city_labels = ["DL"] * 5 + ["MH"] * 5 + ["KA"] * 5

    print(f"Downloading {len(all_urls)} OpenCity CSV files (2021-2025)…\n")
    frames = []
    for i, url in enumerate(all_urls):
        print(f"  [{i+1:>2}/{len(all_urls)}] {city_labels[i]} {2021 + i % 5}…", end=" ", flush=True)
        df = fetch_csv(url)
        if df is not None:
            frames.append(df)
            print(f"{len(df):,} rows")
        else:
            print("FAILED")
        time.sleep(1)

    if not frames:
        raise SystemExit("All downloads failed.")

    data = pd.concat(frames, ignore_index=True)
    print(f"\nTotal rows: {len(data):,}")

    # Filter: new LMV registrations only
    lmv = data[
        (data["Metric"].str.strip() == "Registration Category") &
        (data["Name"].str.strip() == "LIGHT MOTOR VEHICLE")
    ].copy()
    print(f"LMV registration rows: {len(lmv):,}")

    rto_totals = lmv.groupby(["State", "RTO"])["Count"].sum().reset_index()

    print("\nRTO-level LMV totals (2021-2025):")
    for _, row in rto_totals.sort_values(["State", "RTO"]).iterrows():
        key = (str(row["State"]), int(row["RTO"]))
        pop = RTO_POP.get(key, 0)
        per_k = f"{row['Count'] / pop * 1000:.1f}" if pop else "—"
        print(f"  {row['State']}-{int(row['RTO']):<3}  {int(row['Count']):>8,} LMV  {per_k:>8}/1000")

    results = []
    for pc, (state, rto) in PINCODE_RTO.items():
        if state == "UP":
            continue  # Noida handled after Delhi block

        key = (state, rto)
        match = rto_totals[(rto_totals["State"] == state) & (rto_totals["RTO"] == rto)]
        if match.empty:
            print(f"  WARN: {pc} → {key} not in downloaded data — skipping")
            continue

        total_lmv = int(match["Count"].sum())
        pop = RTO_POP.get(key, 1_000_000)
        per_k = round(total_lmv / pop * 1000, 1)
        results.append({"pincode": pc, "cars_per_1000": per_k})

    # Noida: estimated from East Delhi (DL-7 + DL-13) with small uplift.
    # Gautam Buddha Nagar is UP but Noida's car ownership tier ≈ East Delhi suburbs.
    east_dl = rto_totals[(rto_totals["State"] == "DL") & (rto_totals["RTO"].isin([7, 13]))]
    east_lmv = int(east_dl["Count"].sum())
    east_pop = RTO_POP[("DL", 7)] + RTO_POP[("DL", 13)]
    noida_per_k = round(east_lmv / east_pop * 1000 * 1.05, 1)
    results.append({"pincode": "201301", "cars_per_1000": noida_per_k})
    print(f"\n  201301 (Noida) → East Delhi proxy × 1.05 = {noida_per_k}/1000")

    out = pd.DataFrame(results)
    out.to_csv("data/raw/vehicle_density.csv", index=False)
    print(f"\nWrote {len(out)} rows to data/raw/vehicle_density.csv")
    print(out.sort_values("cars_per_1000", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
