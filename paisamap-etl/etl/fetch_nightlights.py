"""
Fetches real VIIRS nighttime radiance values via NASA AppEEARS API.
Requires a free NASA Earthdata account: https://urs.earthdata.nasa.gov/users/new

Usage:
    python etl/fetch_nightlights.py --user YOUR_NASA_USERNAME --password YOUR_PASSWORD
"""
import argparse
import json
import time
import urllib.request
import urllib.parse
import base64
import io
import csv
import pandas as pd

APPEEARS = "https://appeears.earthdatacloud.nasa.gov/api"

def auth_header(user, pwd):
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return {"Authorization": f"Basic {token}"}

def bearer_header(token):
    return {"Authorization": f"Bearer {token}"}

def api(method, path, headers, body=None):
    url = f"{APPEEARS}/{path}"
    data = json.dumps(body).encode() if body else None
    hdrs = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    coords = pd.read_csv("data/raw/pincode_coords.csv", dtype={"pincode": str})

    # 1 — login
    print("Logging in to NASA AppEEARS…")
    resp = api("POST", "login", auth_header(args.user, args.password))
    token = resp["token"]
    hdrs  = bearer_header(token)
    print("  OK")

    # 2 — submit point-sample task
    print("Submitting task for VIIRS annual nighttime lights (VNP46A4)…")
    coordinates = [
        {"id": row["pincode"], "latitude": row["lat"], "longitude": row["lng"]}
        for _, row in coords.iterrows()
    ]
    task = {
        "task_type": "point",
        "task_name": "paisamap_nightlights",
        "params": {
            "dates": [{"startDate": "01-01-2023", "endDate": "12-31-2023"}],
            "layers": [{"product": "VNP46A4", "layer": "NearNadir_Composite_Snow_Free"}],
            "coordinates": coordinates,
        }
    }
    result = api("POST", "task", hdrs, task)
    task_id = result["task_id"]
    print(f"  task_id = {task_id}")

    # 3 — poll until done
    print("Waiting for AppEEARS to process (usually 2-5 min)…")
    while True:
        status = api("GET", f"task/{task_id}", hdrs)["status"]
        print(f"  status: {status}")
        if status == "done":
            break
        if status == "error":
            raise RuntimeError("AppEEARS task failed")
        time.sleep(30)

    # 4 — download results CSV
    print("Downloading results…")
    bundle = api("GET", f"bundle/{task_id}", hdrs)
    csv_file = next(f for f in bundle["files"] if f["file_name"].endswith(".csv")
                    and "NearNadir" in f["file_name"])
    dl_url = f"{APPEEARS}/bundle/{task_id}/{csv_file['file_id']}"
    req = urllib.request.Request(dl_url, headers=hdrs["Authorization"] and
                                  {"Authorization": hdrs["Authorization"]})
    req.add_header("Authorization", hdrs["Authorization"])
    with urllib.request.urlopen(req, timeout=120) as resp:
        content = resp.read().decode()

    # 5 — parse and write output
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    # AppEEARS returns one row per date per point; take mean across dates
    df = pd.DataFrame(rows)
    df["value"] = pd.to_numeric(df["NearNadir_Composite_Snow_Free"], errors="coerce")
    df = df[df["value"] > 0]  # drop fill values
    agg = df.groupby("ID")["value"].mean().round(1).reset_index()
    agg.columns = ["pincode", "radiance_mean"]
    agg.to_csv("data/raw/nightlights.csv", index=False)
    print(f"\nDone → {len(agg)} pincodes written to data/raw/nightlights.csv")
    print(agg.sort_values("radiance_mean", ascending=False).to_string(index=False))

if __name__ == "__main__":
    main()
