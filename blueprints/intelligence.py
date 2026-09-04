"""
intelligence.py — GET /api/intelligence/score, GET /api/intelligence/compare.

Public, no @require_login — same trust level as /api/export and /api/search
today. ppi_ml only refits weekly (see _signals_data.py's module docstring),
so everything here is a live aggregation over already-computed data, not new
ML work: percentile rank, group means (India/state/district/nearest-20), and
two pieces of real precomputed explainability (ml_diagnostics.json's global
feature importances and per-pincode anomaly flags) that nothing in the API
layer surfaced before this.

If a session happens to exist, activity gets logged — soft-checked, not
required, so signed-out visitors can use this exactly like the rest of the
public map, matching this phase's explicit decision to defer anonymous
usage limits rather than invent rate-limiting infrastructure for one feature.
"""

import bisect
import sys
from pathlib import Path

from flask import Blueprint, request, jsonify, session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
import _signals_data  # noqa: E402

from ._session import _auth_db  # noqa: E402

intelligence_bp = Blueprint("intelligence", __name__, url_prefix="/api/intelligence")

MAX_COMPARE = 8
NEAREST_N = 20


def _log_if_signed_in(action, pincode):
    if _auth_db is None:
        return
    user_id = session.get("user_id")
    if not user_id:
        return
    try:
        if _auth_db.enabled():
            _auth_db.log_activity(user_id, action, target_type="pincode", target_id=None,
                                   metadata={"pincode": pincode})
    except Exception:
        pass  # activity logging is a nice-to-have, never blocks a score/compare response


def _num(v):
    v = _signals_data.coerce(v)
    return v if isinstance(v, (int, float)) else None


def _pct_diff(value, baseline):
    if value is None or baseline in (None, 0):
        return None
    return round((value - baseline) / abs(baseline) * 100, 1)


def _group_means(rows_by_pincode, pincodes):
    """Mean ppi_ml/income/spend across a set of pincodes (ignoring missing values)."""
    ppis, incomes, spends = [], [], []
    for pc in pincodes:
        r = rows_by_pincode.get(pc)
        if not r:
            continue
        p, i, s = _num(r.get("ppi_ml")), _num(r.get("est_monthly_income_hh")), _num(r.get("est_monthly_spend_hh"))
        if p is not None:
            ppis.append(p)
        if i is not None:
            incomes.append(i)
        if s is not None:
            spends.append(s)
    return {
        "ppi_ml": round(sum(ppis) / len(ppis), 1) if ppis else None,
        "income": round(sum(incomes) / len(incomes)) if incomes else None,
        "spend": round(sum(spends) / len(spends)) if spends else None,
        "n": len(pincodes),
    }


def _nearest_pincodes(rows_by_pincode, pincode, n=NEAREST_N):
    me = rows_by_pincode.get(pincode)
    if me is None:
        return []
    my_lat, my_lng = _num(me.get("lat")), _num(me.get("lng"))
    if my_lat is None or my_lng is None:
        return []
    dists = []
    for pc, r in rows_by_pincode.items():
        if pc == pincode:
            continue
        lat, lng = _num(r.get("lat")), _num(r.get("lng"))
        if lat is None or lng is None:
            continue
        dists.append((_signals_data.haversine_km(my_lat, my_lng, lat, lng), pc))
    dists.sort(key=lambda t: t[0])
    return [pc for _, pc in dists[:n]]


def _executive_summary(name, pincode, economic_score, benchmark, anomaly_note):
    ordinal = _ordinal(round(economic_score))
    parts = [
        f"{name} ({pincode}) scores {round(economic_score)}/100 nationally — "
        f"the {ordinal} percentile among {benchmark['india']['n']} tracked pincodes."
    ]
    state = benchmark.get("state")
    if state and state["diff_pct"] is not None:
        direction = "above" if state["diff_pct"] >= 0 else "below"
        parts.append(f"Purchasing power here is {abs(state['diff_pct'])}% {direction} "
                      f"the {state['label']} average.")
    neighbours = benchmark.get("neighbours")
    if neighbours and neighbours["diff_pct"] is not None:
        direction = "above" if neighbours["diff_pct"] >= 0 else "below"
        parts.append(f"It's {abs(neighbours['diff_pct'])}% {direction} nearby areas.")
    if anomaly_note:
        parts.append(anomaly_note)
    parts.append("Modelled estimate from PaisaMap's PPI ensemble, not a real transaction record.")
    return " ".join(parts)


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _score_payload(pincode, rows_by_pincode, sorted_ppis, geography, diagnostics):
    row = rows_by_pincode.get(pincode)
    if row is None:
        return None

    ppi = _num(row.get("ppi_ml"))
    name = row.get("name") or pincode

    # Percentile rank — count(<=) / total, using the pre-sorted list (bisect avoids
    # an O(n) scan per compared location; matters once compare has up to 8).
    rank_count = bisect.bisect_right(sorted_ppis, ppi) if ppi is not None else 0
    economic_score = round(rank_count / len(sorted_ppis) * 100, 1) if sorted_ppis else None

    geo = geography.get(pincode, {})
    india_means = _group_means(rows_by_pincode, list(rows_by_pincode.keys()))

    state_pincodes = [pc for pc, g in geography.items()
                       if g.get("state_name") == geo.get("state_name") and pc in rows_by_pincode]
    district_pincodes = [pc for pc, g in geography.items()
                          if g.get("district") == geo.get("district") and pc in rows_by_pincode]
    neighbour_pincodes = _nearest_pincodes(rows_by_pincode, pincode)

    state_means = _group_means(rows_by_pincode, state_pincodes) if state_pincodes else None
    district_means = _group_means(rows_by_pincode, district_pincodes) if district_pincodes else None
    neighbour_means = _group_means(rows_by_pincode, neighbour_pincodes) if neighbour_pincodes else None

    benchmark = {
        "india": {"label": "India", **india_means, "diff_pct": 0.0},
    }
    if state_means and geo.get("state_name"):
        benchmark["state"] = {"label": geo["state_name"], **state_means,
                               "diff_pct": _pct_diff(ppi, state_means["ppi_ml"])}
    if district_means and geo.get("district"):
        benchmark["district"] = {"label": geo["district"], **district_means,
                                  "diff_pct": _pct_diff(ppi, district_means["ppi_ml"])}
    if neighbour_means:
        benchmark["neighbours"] = {"label": f"nearest {len(neighbour_pincodes)} areas", **neighbour_means,
                                    "diff_pct": _pct_diff(ppi, neighbour_means["ppi_ml"])}

    top_signals = []
    anomaly_note = None
    if diagnostics:
        importances = diagnostics.get("model_a", {}).get("feature_importance") or {}
        top_signals = [s for s, _ in sorted(importances.items(), key=lambda kv: kv[1], reverse=True)[:3]]
        anomaly = (diagnostics.get("anomalies") or {}).get(pincode)
        if anomaly and anomaly.get("is_anomaly"):
            anomaly_note = (f"Its {anomaly['top_deviant_proxy']} signal is a notable outlier "
                             f"for its area (deviation {anomaly['deviation_magnitude']}).")

    summary = _executive_summary(name, pincode, economic_score or 0, benchmark, anomaly_note)

    return {
        "pincode": pincode,
        "name": name,
        "lat": _num(row.get("lat")),
        "lng": _num(row.get("lng")),
        "ppi_ml": ppi,
        "income": _num(row.get("est_monthly_income_hh")),
        "spend": _num(row.get("est_monthly_spend_hh")),
        "economic_score": economic_score,
        "benchmark": benchmark,
        "top_signals": top_signals,
        "anomaly_note": anomaly_note,
        "executive_summary": summary,
    }


@intelligence_bp.route("/score", methods=["GET"])
def score():
    pincode = (request.args.get("pincode") or "").strip()
    if not pincode:
        return jsonify({"error": "pincode is required"}), 400

    rows_by_pincode, _source = _signals_data.load_ppi_signals_rows()
    if pincode not in rows_by_pincode:
        return jsonify({"error": "not_found"}), 404

    sorted_ppis = sorted(v for v in (_num(r.get("ppi_ml")) for r in rows_by_pincode.values()) if v is not None)
    geography = _signals_data.load_geography()
    diagnostics = _signals_data.load_diagnostics()

    payload = _score_payload(pincode, rows_by_pincode, sorted_ppis, geography, diagnostics)
    _log_if_signed_in("location_score", pincode)
    return jsonify(payload)


@intelligence_bp.route("/compare", methods=["GET"])
def compare():
    raw = (request.args.get("pincodes") or "").strip()
    pincodes = [p.strip() for p in raw.split(",") if p.strip()]
    if len(pincodes) < 1:
        return jsonify({"error": "at least 1 pincode is required"}), 400
    if len(pincodes) > MAX_COMPARE:
        return jsonify({"error": f"at most {MAX_COMPARE} pincodes can be compared at once"}), 400

    rows_by_pincode, _source = _signals_data.load_ppi_signals_rows()
    unknown = [p for p in pincodes if p not in rows_by_pincode]
    if unknown:
        return jsonify({"error": "not_found", "unknown_pincodes": unknown}), 404

    sorted_ppis = sorted(v for v in (_num(r.get("ppi_ml")) for r in rows_by_pincode.values()) if v is not None)
    geography = _signals_data.load_geography()
    diagnostics = _signals_data.load_diagnostics()

    locations = [
        _score_payload(pc, rows_by_pincode, sorted_ppis, geography, diagnostics)
        for pc in pincodes
    ]
    locations.sort(key=lambda loc: loc["economic_score"] or 0, reverse=True)
    for i, loc in enumerate(locations, start=1):
        loc["rank"] = i

    _log_if_signed_in("location_compare", ",".join(pincodes))
    return jsonify({"locations": locations})
