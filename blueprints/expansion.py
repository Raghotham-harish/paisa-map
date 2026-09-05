"""
expansion.py — Phase 05 round 2: GET /api/expansion/drivers, GET /api/expansion/recommend.

Both are read-only, computed-on-demand over a project's own customer_locations
(round 1's upload pipeline) joined against the same nationwide signals dataset
every other intelligence endpoint uses (_signals_data.load_ppi_signals_rows()).
No new tables, no new ML model — plain-Python statistics at a scale (a business's
own store count, capped at 500 rows/upload) where numpy/pandas would be overkill
and would have meant a new venv-flask dependency for no real benefit.

**Deliberately does NOT call intelligence.py's _score_payload/
compute_location_intelligence_batch for candidate scoring** — that function's
benchmark section (_group_means/_nearest_pincodes) is O(N) per pincode, fine for
its existing callers (≤8 pincodes, or a user's own shortlist), but scoring the
full ~15,559-pincode candidate universe through it would be O(N²), the same class
of hotspot already found and fixed once in ml_refinement.py. Only the genuinely
O(1)-per-call pieces (opportunity_assessment, risk_assessment) are reused here;
economic_score is recomputed directly via the same bisect percentile formula
_score_payload uses, without the expensive parts.
"""

import bisect
import sys
from pathlib import Path

from flask import Blueprint, request, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
import _signals_data  # noqa: E402

from ._session import require_login, _auth_db
from .intelligence import opportunity_assessment, risk_assessment  # noqa: E402

expansion_bp = Blueprint("expansion", __name__, url_prefix="/api/expansion")

DRIVER_MIN_SAMPLES = 5
DRIVER_TOP_N = 5
QUALITY_GATE_OPPORTUNITY = 50.0  # same threshold as the "Suitable" tier elsewhere
RECOMMEND_FALLBACK_TOP_N = 10


def _num(v):
    v = _signals_data.coerce(v)
    return v if isinstance(v, (int, float)) else None


def _business_profile(project):
    """Same shaping as reports.py's own _business_profile — small enough that
    duplicating it here beats adding a shared-helper module for one function."""
    if not (project.get("avg_ticket") or project.get("target_segment") or project.get("business_type")):
        return None
    return {
        "avg_ticket": project.get("avg_ticket"),
        "target_segment": project.get("target_segment"),
        "business_type": project.get("business_type"),
    }


def _pearson_r(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x * var_y) ** 0.5


def _compute_drivers(locations, rows_by_pincode):
    """Pearson correlation between each nationwide signal column and this
    project's own store revenue. Returns the same shape whether called
    directly (GET /drivers) or inline from /recommend."""
    qualifying = [loc for loc in locations
                  if loc.get("pincode") in rows_by_pincode and loc.get("revenue") is not None]
    sample_size = len(qualifying)
    if sample_size < DRIVER_MIN_SAMPLES:
        return {"sufficient_data": False, "sample_size": sample_size,
                "min_samples_required": DRIVER_MIN_SAMPLES, "drivers": []}

    candidate_columns = ["ppi_ml"] + [c for _, cols in _signals_data.EXPORT_SIGNAL_FILES for c in cols]
    scored = []
    for col in candidate_columns:
        pairs = []
        for loc in qualifying:
            val = _num(rows_by_pincode[loc["pincode"]].get(col))
            if val is not None:
                pairs.append((val, loc["revenue"]))
        if len(pairs) < DRIVER_MIN_SAMPLES:
            continue
        xs, ys = zip(*pairs)
        r = _pearson_r(list(xs), list(ys))
        if r is None:
            continue
        scored.append({
            "signal": col,
            "label": _signals_data.SIGNAL_LABELS.get(col, col),
            "correlation": round(r, 3),
            "direction": "positive" if r >= 0 else "negative",
            "sample_size": len(pairs),
        })
    scored.sort(key=lambda d: abs(d["correlation"]), reverse=True)
    return {"sufficient_data": True, "sample_size": sample_size,
            "min_samples_required": DRIVER_MIN_SAMPLES, "drivers": scored[:DRIVER_TOP_N]}


def _estimate_store_sqft(locations, rows_by_pincode):
    """Round 1's canonical upload fields don't include store size — infer an
    average from rent ÷ this pincode's own rate_per_sqft, across whichever
    stores have both. Returns None (not a fabricated default) if nothing
    qualifies, so callers can degrade the CapEx estimate honestly."""
    sizes = []
    for loc in locations:
        rent = loc.get("rent")
        row = rows_by_pincode.get(loc.get("pincode"))
        if rent is None or not row:
            continue
        rate = _num(row.get("rate_per_sqft"))
        if rate:
            sizes.append(rent / rate)
    return sum(sizes) / len(sizes) if sizes else None


@expansion_bp.route("/drivers", methods=["GET"])
@require_login
def drivers(user_id):
    project_id = request.args.get("project_id", type=int)
    if project_id is None or _auth_db.get_project(project_id, user_id) is None:
        return jsonify({"error": "project not_found"}), 404

    locations = _auth_db.list_customer_locations(user_id, project_id)
    rows_by_pincode, _source = _signals_data.load_ppi_signals_rows()
    result = _compute_drivers(locations, rows_by_pincode)
    result["note"] = ("Correlation, not causation — these are the signals that moved "
                       "together with your stores' revenue, not a proven cause.")
    return jsonify(result)


@expansion_bp.route("/recommend", methods=["GET"])
@require_login
def recommend(user_id):
    project_id = request.args.get("project_id", type=int)
    budget = request.args.get("budget", type=float)
    project = _auth_db.get_project(project_id, user_id) if project_id is not None else None
    if project is None:
        return jsonify({"error": "project not_found"}), 404
    if budget is None or budget <= 0:
        return jsonify({"error": "budget must be a positive number"}), 400

    locations = _auth_db.list_customer_locations(user_id, project_id)
    owned_pincodes = {loc["pincode"] for loc in locations if loc.get("pincode")}
    rows_by_pincode, _source = _signals_data.load_ppi_signals_rows()

    driver_result = _compute_drivers(locations, rows_by_pincode)
    top_drivers = driver_result["drivers"] if driver_result["sufficient_data"] else []
    driver_weighted = bool(top_drivers)

    # Percentile ranks for each driver signal, needed to blend differently-scaled
    # columns (rupees vs. a 0-1 share) into one comparable 0-100 fit score. A
    # negatively-correlated driver is inverted (100 - percentile) so "higher fit"
    # always means "more like what already works for this business" regardless
    # of the correlation's sign.
    driver_sorted = {
        d["signal"]: sorted(v for v in (_num(r.get(d["signal"])) for r in rows_by_pincode.values())
                             if v is not None)
        for d in top_drivers
    }

    assumed_sqft = _estimate_store_sqft(locations, rows_by_pincode)
    capex_available = assumed_sqft is not None
    business = _business_profile(project)
    diagnostics = _signals_data.load_diagnostics()

    sorted_ppis = sorted(v for v in (_num(r.get("ppi_ml")) for r in rows_by_pincode.values())
                          if v is not None)

    candidates = []
    for pincode, row in rows_by_pincode.items():
        if pincode in owned_pincodes:
            continue
        ppi = _num(row.get("ppi_ml"))
        if ppi is None or not sorted_ppis:
            continue
        economic_score = round(bisect.bisect_right(sorted_ppis, ppi) / len(sorted_ppis) * 100, 1)

        payload = {"economic_score": economic_score, "spend": _num(row.get("est_monthly_spend_hh"))}
        opportunity_score = (opportunity_assessment(payload, business)["opportunity_score"]
                              if business else economic_score)
        if opportunity_score is None or opportunity_score < QUALITY_GATE_OPPORTUNITY:
            continue

        driver_fit_score = None
        if driver_weighted:
            fits = []
            for d in top_drivers:
                col = d["signal"]
                bucket = driver_sorted[col]
                val = _num(row.get(col))
                if val is None or not bucket:
                    continue
                pct = bisect.bisect_right(bucket, val) / len(bucket) * 100
                fits.append(100 - pct if d["direction"] == "negative" else pct)
            if fits:
                driver_fit_score = round(sum(fits) / len(fits), 1)

        combined_score = (round(opportunity_score * 0.5 + driver_fit_score * 0.5, 1)
                           if driver_fit_score is not None else opportunity_score)

        estimated_capex = None
        if capex_available:
            rate = _num(row.get("rate_per_sqft"))
            if rate:
                estimated_capex = round(rate * assumed_sqft, 0)

        candidates.append({
            "pincode": pincode,
            "name": row.get("name") or pincode,
            "economic_score": economic_score,
            "opportunity_score": opportunity_score,
            "driver_fit_score": driver_fit_score,
            "combined_score": combined_score,
            "estimated_capex": estimated_capex,
            "risk": risk_assessment(pincode, diagnostics),
        })

    portfolio = []
    total_capex = 0.0
    if capex_available:
        priced = [c for c in candidates if c["estimated_capex"]]
        # Value-density greedy (score per rupee of CapEx) — a standard, transparent
        # approximation, not a true knapsack solver. "continue" rather than "break"
        # on a miss so a later, cheaper candidate still gets a chance to fit —
        # the point is filling the budget with a good portfolio, not stopping at
        # the first item that doesn't.
        priced.sort(key=lambda c: c["combined_score"] / c["estimated_capex"], reverse=True)
        for c in priced:
            if total_capex + c["estimated_capex"] > budget:
                continue
            portfolio.append(c)
            total_capex += c["estimated_capex"]
    else:
        candidates.sort(key=lambda c: c["combined_score"], reverse=True)
        portfolio = candidates[:RECOMMEND_FALLBACK_TOP_N]

    return jsonify({
        "budget": budget,
        "capex_estimation": "available" if capex_available else "unavailable",
        "assumed_store_sqft": round(assumed_sqft, 1) if capex_available else None,
        "driver_weighted": driver_weighted,
        "quality_gate": f"opportunity_score >= {QUALITY_GATE_OPPORTUNITY}",
        "portfolio": portfolio,
        "total_estimated_capex": round(total_capex, 0) if capex_available else None,
        "candidates_considered": len(rows_by_pincode),
        "detail": None if capex_available else (
            "We can't estimate CapEx per site without rent data for at least one "
            "of your existing stores — showing the top opportunities instead, "
            "unconstrained by budget."
        ),
    })
