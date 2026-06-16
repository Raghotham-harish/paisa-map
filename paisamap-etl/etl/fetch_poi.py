"""
Fetches real premium POI counts from the public Overpass/OSM API
for each pincode centroid and writes data/raw/poi_density.csv.
"""
import pandas as pd
import urllib.request
import json
import time
import math

OVERPASS = "https://overpass-api.de/api/interpreter"
RADIUS_M = 2000                            # 2 km search radius
AREA_KM2 = math.pi * (RADIUS_M / 1000)**2 # ~12.57 km²
DELAY_S  = 3                               # polite pause between requests

def fetch_count(lat, lng, retries=3):
    # Premium commercial activity: malls, banks, schools, hospitals,
    # fuel stations, supermarkets, gyms, jewellery — all weighted equally
    query = f"""[out:json][timeout:30];
(
  nwr["shop"="mall"](around:{RADIUS_M},{lat},{lng});
  nwr["shop"="department_store"](around:{RADIUS_M},{lat},{lng});
  nwr["shop"="supermarket"](around:{RADIUS_M},{lat},{lng});
  nwr["amenity"="bank"](around:{RADIUS_M},{lat},{lng});
  nwr["shop"="jewelry"](around:{RADIUS_M},{lat},{lng});
  nwr["leisure"="fitness_centre"](around:{RADIUS_M},{lat},{lng});
  nwr["amenity"="school"](around:{RADIUS_M},{lat},{lng});
  nwr["amenity"="hospital"](around:{RADIUS_M},{lat},{lng});
  nwr["amenity"="fuel"](around:{RADIUS_M},{lat},{lng});
  nwr["amenity"="pharmacy"](around:{RADIUS_M},{lat},{lng});
  nwr["amenity"="restaurant"](around:{RADIUS_M},{lat},{lng});
);
out count;"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                OVERPASS,
                data=query.encode(),
                headers={"Content-Type": "text/plain", "User-Agent": "PaisaMap-ETL/2.0"}
            )
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read())
            return int(data["elements"][0]["tags"]["total"])
        except Exception as e:
            print(f"  retry {attempt+1}: {e}")
            time.sleep(5)
    return None

coords = pd.read_csv("data/raw/pincode_coords.csv", dtype={"pincode": str})
results = []

print(f"Querying {len(coords)} pincodes via Overpass API (~{len(coords)*DELAY_S//60+1} min)…\n")
for _, row in coords.iterrows():
    count = fetch_count(row["lat"], row["lng"])
    if count is None:
        print(f"  {row['pincode']}: FAILED — keeping synthetic value")
        continue
    density = round(count / AREA_KM2, 1)
    results.append({"pincode": row["pincode"], "premium_poi_per_km2": density})
    print(f"  {row['pincode']:>8}  {count:>4} POIs → {density:>5}/km²")
    time.sleep(DELAY_S)

df = pd.DataFrame(results)
df.to_csv("data/raw/poi_density.csv", index=False)
print(f"\nDone → {len(df)} pincodes written to data/raw/poi_density.csv")
