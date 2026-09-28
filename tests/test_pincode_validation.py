"""
test_pincode_validation.py — issue #4. Pincodes are checked for format and
for existence in the India Post / DoP master before they are saved, enriched
or imported.

    python3 tests/test_pincode_validation.py
"""

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "paisamap-etl" / "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/pincode_validation.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
# A fresh file, so a previous run's rows don't leak into this one.
db_path = os.environ["DATABASE_URL"].replace("sqlite:///", "")
if os.path.exists(db_path):
    os.remove(db_path)
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()

import pincode_master as M  # noqa: E402

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"  ok  {msg}")


ok, reason, norm = M.validate_pincode("560038")
check(ok and reason == "ok" and norm == "560038", "560038 is a real pincode")
ok, reason, norm = M.validate_pincode("560 038")
check(ok and norm == "560038", "internal spaces are normalised")
for raw, why in [("", "empty"), ("abc", "invalid_format"), ("012345", "invalid_format"),
                 ("99999", "invalid_format"), ("000000", "invalid_format")]:
    ok, reason, _norm = M.validate_pincode(raw)
    check(not ok and reason == why, f"{raw!r} → {why}, got {reason}")

ok, reason, _norm = M.validate_pincode("999999")
check(not ok and reason == "unknown_pincode", "a well-formed pin absent from the master is unknown")
check("560038" in M.pincode_master_ids(), "the master contains 560038")
check("999999" not in M.pincode_master_ids(), "the master does not contain 999999")

# ── HTTP entry points ────────────────────────────────────────────────────────
import _db  # noqa: E402
_db.init_schema()
import _auth_db  # noqa: E402
_auth_db.init_schema()
_auth_db.migrate_schema()

import server  # noqa: E402


class _Done:
    returncode, stdout, stderr = 0, "PPI (ML): 100\n  household income: ₹50,000\n", ""


server.subprocess.run = lambda *a, **k: _Done()
server._append_log = lambda *a, **k: None
server._mirror_to_static = lambda: None

app = server.app
app.testing = True
client = app.test_client()

user = _auth_db.upsert_user(google_sub="sub-pin", email="pin@test.com", name="Pin", picture_url=None)
uid = user["id"]
_auth_db.create_default_organization_for_user(uid, "Pin Workspace")


def login():
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def post_location(pincode):
    login()
    return client.post("/api/locations", json={"pincode": pincode, "name": "t", "lat": 12.97, "lng": 77.64})


for raw, why in [("", "empty"), ("abc", "invalid_format"), ("012345", "invalid_format"),
                 ("99999", "invalid_format"), ("999999", "unknown_pincode")]:
    r = post_location(raw)
    check(r.status_code == 400, f"POST /api/locations {raw!r} is 400, got {r.status_code}")
    body = r.get_json() or {}
    if why == "empty":
        check(body.get("error") == "pincode is required", "empty pincode keeps the required error")
    else:
        check(body.get("reason") == why, f"locations reason for {raw!r} is {why}, got {body.get('reason')}")

r = post_location("560038")
check(r.status_code in (200, 201), f"POST /api/locations 560038 is accepted, got {r.status_code} {r.get_json()}")

def enrich(pincode):
    return client.get("/api/enrich", query_string={
        "pincode": pincode, "lat": "12.97", "lng": "77.64", "name": "Indiranagar",
    })

r = enrich("012345")
check(r.status_code == 400 and (r.get_json() or {}).get("reason") == "invalid_format",
      "GET /api/enrich rejects a leading zero")
r = enrich("999999")
check(r.status_code == 400 and (r.get_json() or {}).get("reason") == "unknown_pincode",
      "GET /api/enrich rejects a pin that is not in the master")
r = enrich("560038")
check(r.status_code == 200, f"GET /api/enrich accepts 560038, got {r.status_code} {r.get_json()}")

project = _auth_db.create_project(uid, "Stores")
upload = _auth_db.create_customer_upload(
    uid, project["id"], "stores.csv", "csv",
    ["store", "pincode"],
    [
        {"store": "Good", "pincode": "560038"},
        {"store": "Spaced", "pincode": "560 001"},
        {"store": "Letters", "pincode": "abc"},
        {"store": "Leading zero", "pincode": "012345"},
        {"store": "Unknown", "pincode": "999999"},
    ],
)
login()
r = client.post(f"/api/customer-data/uploads/{upload['id']}/commit",
                json={"mapping": {"store_name": "store", "pincode": "pincode"}})
check(r.status_code == 200, f"upload commit accepts the file, got {r.status_code} {r.get_json()}")
report = (r.get_json() or {}).get("upload", {}).get("quality_report") or {}
flagged = {(item["row"], item["pincode"], item["reason"]) for item in report.get("invalid_pincodes") or []}
check(flagged == {(3, "abc", "invalid_format"), (4, "012345", "invalid_format"),
                  (5, "999999", "unknown_pincode")},
      f"upload report lists invalid rows, got {flagged}")
stored = {loc["pincode"] for loc in _auth_db.list_customer_locations(uid, project["id"]) if loc.get("pincode")}
check(stored == {"560038", "560001"}, f"only real pincodes are stored, got {stored}")

print(f"\n{passed} checks passed")
