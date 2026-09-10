"""
forecast.py — GET /api/forecast (map-first workspace, P3).

One endpoint, one page: returns the reach curve, recommended rupee split,
payback, market-capture %, lever-fit radar, budget-split lever allocation, and
per-site SWOT for a project, all from _forecast_model.build_forecast (see that
module's docstring for the model).

Works for every project by default — driven by the project's own signals,
target location(s) and budget, using a documented benchmark capture rate when
there's no store data yet. Uploaded stores (customer_locations) are an
additional calibration layer, not a requirement: >=3 usable stores moves the
capture rate to your own median, >=8 to a fitted regression. Charged in
credits like expansion_recommend — but ONLY when the model actually returns a
forecast; a genuine no-result (no candidate pincodes near the target market)
is free, same "don't burn credits on a non-result" rule reports.py/expansion.py
follow.
"""

import sys
from pathlib import Path

from flask import Blueprint, request, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
import _signals_data  # noqa: E402
import _forecast_model  # noqa: E402
import _pricing  # noqa: E402

from ._session import require_login, _auth_db, charge_credits
from .expansion import _compute_drivers  # noqa: E402
from .projects import _shape  # decodes the JSON-array project columns (signals, target_pincodes)

forecast_bp = Blueprint("forecast", __name__, url_prefix="/api/forecast")


@forecast_bp.route("", methods=["GET"])
@require_login
def forecast(user_id):
    project_id = request.args.get("project_id", type=int)
    project = _auth_db.get_project(project_id, user_id) if project_id is not None else None
    if project is None:
        return jsonify({"error": "project not_found"}), 404
    project = _shape(project)  # signals / target_pincodes -> real lists

    budget = request.args.get("budget", type=float)
    if budget is None:
        budget = _forecast_model._num(project.get("total_investment"))
    if budget is None or budget <= 0:
        return jsonify({"error": "budget_required",
                         "detail": "Pass ?budget= or set a total investment on the project."}), 400

    cost = _pricing.credit_cost("forecast")
    balance = _auth_db.get_credit_balance(user_id)
    if balance < cost:
        return jsonify({"error": "insufficient_credits", "balance": balance, "required": cost}), 402

    locations = _auth_db.list_customer_locations(user_id, project_id)
    rows_by_pincode, _source = _signals_data.load_ppi_signals_rows()
    geography = _signals_data.load_geography()
    diagnostics = _signals_data.load_diagnostics()
    district_pop = _forecast_model.load_district_population()

    drivers = _compute_drivers(locations, rows_by_pincode)
    result = _forecast_model.build_forecast(
        project, locations, budget, rows_by_pincode, geography, diagnostics, district_pop, drivers,
    )

    # Charge only for a real forecast — an honest "not enough data" is free.
    if result.get("sufficient_data"):
        charge_credits(user_id, "forecast", ref_type="project", ref_id=project_id)
        _auth_db.log_activity(user_id, "forecast_run", target_type="project", target_id=project_id,
                               metadata={"budget": budget})

    return jsonify(result)
