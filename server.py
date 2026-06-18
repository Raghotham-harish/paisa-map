"""
server.py — PaisaMap local server

  python3 server.py          → serves at http://localhost:8080
  python3 server.py --port 9090

Endpoints:
  /                          static → index.html
  /data/...                  static → data/ files (CSV, etc.)
  /api/reverse?lat=&lng=     Nominatim reverse geocode proxy (server IP, avoids browser rate-limit)
  /api/search?q=QUERY        Nominatim search proxy
  /api/enrich                GET {pincode, lat, lng, name, source} — enriches new pincode
  /api/status/<pincode>      enrichment job status
  /api/enrich_stats          enrichment log summary (counts by source, recent activity)
"""

import csv
import re
import sys
import threading
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory

APP  = Path(__file__).parent
ETL  = APP / "paisamap-etl"
ENRICH_SCRIPT = ETL / "etl" / "enrich_single.py"
VENV_PY = ETL / "venv" / "bin" / "python3"
PYTHON  = str(VENV_PY) if VENV_PY.exists() else sys.executable

ENRICH_LOG     = APP / "data" / "output" / "enrichment_log.csv"
LOG_FIELDS     = ["timestamp", "pincode", "name", "lat", "lng", "source", "ppi", "income"]

app = Flask(__name__)

# Job registry: pincode → {status, ppi, log, error, source}
_jobs: dict = {}
_lock = threading.Lock()


# ── Enrichment log ────────────────────────────────────────────────────────────
def _append_log(pc, name, lat, lng, source, ppi, income):
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pincode":   pc,
        "name":      name,
        "lat":       lat,
        "lng":       lng,
        "source":    source,   # phase1 | yah | prefetch | search | manual
        "ppi":       ppi,
        "income":    income,
    }
    need_header = not ENRICH_LOG.exists() or ENRICH_LOG.stat().st_size == 0
    ENRICH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ENRICH_LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if need_header:
            w.writeheader()
        w.writerow(row)


def _parse_enrich_output(stdout):
    """Extract PPI and income from enrich_single.py stdout."""
    ppi    = re.search(r"PPI \(ML\):\s*(\d+)",           stdout)
    income = re.search(r"household income:\s*₹([\d,]+)", stdout)
    return (
        ppi.group(1)    if ppi    else "",
        income.group(1) if income else "",
    )


# ── Static files ──────────────────────────────────────────────────────────────
@app.route("/")
def root():
    return send_file(APP / "index.html")

@app.route("/data/<path:fname>")
def serve_data(fname):
    return send_from_directory(APP / "data", fname)


# ── Nominatim proxies ─────────────────────────────────────────────────────────
@app.route("/api/reverse")
def api_reverse():
    """Proxy Nominatim reverse geocode — server IP avoids browser rate-limit."""
    lat = request.args.get("lat", "").strip()
    lng = request.args.get("lng", request.args.get("lon", "")).strip()
    if not lat or not lng:
        return jsonify({"error": "lat and lng required"}), 400
    try:
        params = urllib.parse.urlencode({
            "lat": lat, "lon": lng, "zoom": 14,
            "format": "json", "addressdetails": 1,
        })
        url = f"https://nominatim.openstreetmap.org/reverse?{params}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "PaisaMap-Server/1.0",
                          "Accept-Language": "en",
                          "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read(), 200, {"Content-Type": "application/json",
                                   "Access-Control-Allow-Origin": "*"}
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    try:
        params = urllib.parse.urlencode({
            "format": "jsonv2", "limit": 6,
            "polygon_geojson": 1, "addressdetails": 1,
            "countrycodes": "in", "q": q
        })
        url = f"https://nominatim.openstreetmap.org/search?{params}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "PaisaMap-Server/1.0",
                          "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read(), 200, {"Content-Type": "application/json",
                                   "Access-Control-Allow-Origin": "*"}
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# ── Enrichment ────────────────────────────────────────────────────────────────
@app.route("/api/enrich")
def api_enrich():
    pc     = request.args.get("pincode", "").strip()
    lat    = request.args.get("lat",     "").strip()
    lng    = request.args.get("lng",     "").strip()
    name   = request.args.get("name",    pc).strip()
    source = request.args.get("source",  "yah").strip()   # yah | prefetch | search | manual

    if not pc or not lat or not lng:
        return jsonify({"error": "pincode, lat, lng are required"}), 400

    with _lock:
        job = _jobs.get(pc, {})
        if job.get("status") in ("running", "done"):
            return jsonify(job)
        _jobs[pc] = {"status": "running", "source": source}

    threading.Thread(target=_run_enrich, args=(pc, lat, lng, name, source),
                     daemon=True).start()
    return jsonify({"status": "started", "source": source})


@app.route("/api/status/<pincode>")
def api_status(pincode):
    with _lock:
        return jsonify(_jobs.get(pincode, {"status": "unknown"}))


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/enrich_stats")
def enrich_stats():
    """Return enrichment counts by source and the last 20 enriched pincodes."""
    if not ENRICH_LOG.exists():
        return jsonify({"total": 0, "by_source": {}, "recent": []})

    rows = []
    try:
        with open(ENRICH_LOG, newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        pass

    by_source: dict = {}
    for r in rows:
        src = r.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1

    recent = [
        {"pincode": r["pincode"], "name": r["name"], "source": r["source"],
         "ppi": r["ppi"], "ts": r["timestamp"]}
        for r in rows[-20:][::-1]
    ]

    return jsonify({
        "total":     len(rows),
        "by_source": by_source,
        "recent":    recent,
    })


# ── Worker ────────────────────────────────────────────────────────────────────
def _run_enrich(pc, lat, lng, name, source="yah"):
    try:
        res = subprocess.run(
            [PYTHON, str(ENRICH_SCRIPT), pc, lat, lng, name],
            capture_output=True, text=True, timeout=180, cwd=str(ETL)
        )
        ppi, income = _parse_enrich_output(res.stdout)
        with _lock:
            if res.returncode == 0:
                lines = [l for l in res.stdout.splitlines() if l.strip()]
                ppi_line = next((l for l in reversed(lines) if "PPI" in l), "")
                _jobs[pc] = {
                    "status":  "done",
                    "source":  source,
                    "ppi":     ppi,
                    "income":  income,
                    "log":     res.stdout[-3000:],
                    "summary": ppi_line,
                }
                _append_log(pc, name, lat, lng, source, ppi, income)
            else:
                _jobs[pc] = {"status": "error", "source": source,
                             "error": res.stderr[-1500:]}
    except Exception as e:
        with _lock:
            _jobs[pc] = {"status": "error", "source": source, "error": str(e)}


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = 8080
    for i, a in enumerate(sys.argv[1:]):
        if a == "--port" and i + 2 < len(sys.argv):
            port = int(sys.argv[i + 2])

    print(f"\n  PaisaMap server  →  http://localhost:{port}")
    print(f"  App:             {APP}")
    print(f"  ETL:             {ETL}")
    print(f"  Python:          {PYTHON}\n")
    app.run(port=port, debug=False, use_reloader=False)
