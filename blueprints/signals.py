"""
signals.py — GET /api/signals/catalog.

Public, no @require_login — same trust level as /api/export and
/api/intelligence/*. Returns the canonical list of pincode-level signals the
map and the project-setup wizard can pick from, built from the single source of
truth in _signals_data.py (SIGNAL_LABELS / PRO_COLUMNS / EXPORT_SIGNAL_FILES) so
there is no second, drifting list to maintain.
"""

import sys
from pathlib import Path

from flask import Blueprint, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
import _signals_data  # noqa: E402

signals_bp = Blueprint("signals", __name__, url_prefix="/api/signals")

# Which EXPORT_SIGNAL_FILES bucket a column belongs to, for grouping in the UI.
# Keyed by the source csv filename so a new file only needs one entry here.
_GROUP_BY_FILE = {
    "property_rates.csv": "Property",
    "bank_deposits.csv": "Banking & UPI",
    "financial_inclusion.csv": "Banking & UPI",
    "upi_activity.csv": "Banking & UPI",
    "itr_filers.csv": "Tax & economy",
    "commercial.csv": "Tax & economy",
    "industrial.csv": "Tax & economy",
    "economic.csv": "Tax & economy",
    "agriculture.csv": "Tax & economy",
    "nightlights.csv": "Infrastructure",
    "poi_density.csv": "Infrastructure",
    "education.csv": "Infrastructure",
    "rto_enhanced.csv": "Vehicles",
    "vehicle_density.csv": "Vehicles",
}


@signals_bp.route("/catalog", methods=["GET"])
def catalog():
    groups = {}
    # ppi_ml first — it's the map's default and not in EXPORT_SIGNAL_FILES.
    for col in ("ppi_ml", "est_monthly_income_hh", "est_monthly_spend_hh"):
        groups[col] = "Purchasing power"
    for fname, cols in _signals_data.EXPORT_SIGNAL_FILES:
        for col in cols:
            groups[col] = _GROUP_BY_FILE.get(fname, "Other")

    items = [
        {
            "key": col,
            "label": _signals_data.SIGNAL_LABELS.get(col, col),
            "pro": col in _signals_data.PRO_COLUMNS,
            "group": group,
        }
        for col, group in groups.items()
    ]
    return jsonify({"signals": items})
