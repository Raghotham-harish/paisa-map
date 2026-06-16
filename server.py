"""
server.py — PaisaMap local server

  python3 server.py          → serves at http://localhost:8080
  python3 server.py --port 9090

Endpoints:
  /                          static → index.html
  /data/...                  static → data/ files (CSV, etc.)
  /api/search?q=QUERY        Nominatim proxy (avoids browser CORS restrictions)
  /api/enrich                POST/GET {pincode, lat, lng, name} — enriches new pincode
  /api/status/<pincode>      enrichment job status
"""

import sys
import threading
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory

APP  = Path(__file__).parent
ETL  = APP / "paisamap-etl"
ENRICH_SCRIPT = ETL / "etl" / "enrich_single.py"
VENV_PY = ETL / "venv" / "bin" / "python3"
PYTHON  = str(VENV_PY) if VENV_PY.exists() else sys.executable

app = Flask(__name__)

# Job registry: pincode → {status, ppi, log, error}
_jobs: dict = {}
_lock = threading.Lock()


# ── Static files ──────────────────────────────────────────────────────────────
@app.route("/")
def root():
    return send_file(APP / "index.html")

@app.route("/data/<path:fname>")
def serve_data(fname):
    return send_from_directory(APP / "data", fname)


# ── Nominatim proxy — strips CORS restriction for file:// openers ─────────────
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


# ── Enrichment: add a new pincode to the dataset ─────────────────────────────
@app.route("/api/enrich")
def api_enrich():
    pc   = request.args.get("pincode", "").strip()
    lat  = request.args.get("lat",     "").strip()
    lng  = request.args.get("lng",     "").strip()
    name = request.args.get("name",    pc ).strip()

    if not pc or not lat or not lng:
        return jsonify({"error": "pincode, lat, lng are required"}), 400

    with _lock:
        job = _jobs.get(pc, {})
        if job.get("status") in ("running", "done"):
            return jsonify(job)
        _jobs[pc] = {"status": "running"}

    threading.Thread(target=_run_enrich, args=(pc, lat, lng, name),
                     daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/status/<pincode>")
def api_status(pincode):
    with _lock:
        return jsonify(_jobs.get(pincode, {"status": "unknown"}))


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


def _run_enrich(pc, lat, lng, name):
    try:
        res = subprocess.run(
            [PYTHON, str(ENRICH_SCRIPT), pc, lat, lng, name],
            capture_output=True, text=True, timeout=180, cwd=str(ETL)
        )
        with _lock:
            if res.returncode == 0:
                lines = [l for l in res.stdout.splitlines() if l.strip()]
                ppi_line = next((l for l in reversed(lines) if "PPI" in l), "")
                _jobs[pc] = {
                    "status": "done",
                    "log": res.stdout[-3000:],
                    "summary": ppi_line,
                }
            else:
                _jobs[pc] = {"status": "error", "error": res.stderr[-1500:]}
    except Exception as e:
        with _lock:
            _jobs[pc] = {"status": "error", "error": str(e)}


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
