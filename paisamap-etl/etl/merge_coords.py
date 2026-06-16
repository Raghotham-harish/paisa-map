import pandas as pd

ppi = pd.read_csv("data/output/ppi_pincode.csv", dtype={"pincode": str})
coords = pd.read_csv("data/raw/pincode_coords.csv", dtype={"pincode": str})

merged = ppi.merge(coords, on="pincode", how="inner")
merged = merged[["pincode", "name", "lat", "lng", "ppi", "est_monthly_income_hh", "est_monthly_spend_hh", "est_annual_spend_hh"]]
merged.to_csv("data/output/ppi_map_data.csv", index=False)
print(f"Merged {len(merged)} pincodes")
print(merged[["pincode","name","ppi","lat","lng"]].to_string(index=False))
