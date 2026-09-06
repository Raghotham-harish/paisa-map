"""
_pricing.py — single source of truth for every credit cost, purchasable pack,
plan price, and tax rate in the monetisation system (Phase 3).

ALL NUMBERS BELOW ARE PLACEHOLDERS ("simple placeholder now, tune later" — an
explicit Phase 3 scoping decision, no live Razorpay account exists yet to
validate real pricing against). Nothing outside this module should hardcode a
price or a credit cost — blueprints/billing.py, blueprints/reports.py,
blueprints/expansion.py, and GET /api/billing/pricing (the frontend's only
source of prices) all read from here.

Amounts are in INR paise (Razorpay's native unit, 1/100 rupee) except credit
costs/counts, which are plain integers.
"""

GST_RATE = 0.18  # 18% GST, placeholder — confirm actual applicable rate/HSN once GST-registered

# ── Credit costs per action ─────────────────────────────────────────────────
CREDIT_COSTS = {
    "report_generate": 10,
    "expansion_recommend": 5,
}

# ── Purchasable credit packs: id -> {credits, price_paise, label} ──────────
CREDIT_PACKS = {
    "pack_100":  {"credits": 100,  "price_paise": 19900,  "label": "100 credits"},
    "pack_500":  {"credits": 500,  "price_paise": 79900,  "label": "500 credits"},
    "pack_2000": {"credits": 2000, "price_paise": 249900, "label": "2000 credits"},
}

# ── Plan prices: monthly, INR paise. "free" has no price (not purchasable). ─
PLAN_PRICES = {
    "pro":  {"price_paise": 99900,  "label": "Pro",  "interval": "monthly"},
    "team": {"price_paise": 299900, "label": "Team", "interval": "monthly"},
}
PLAN_ORDER = ["free", "pro", "team"]  # hierarchy — index doubles as rank

# ── One-off report purchase (bypasses the credit balance entirely) ─────────
REPORT_PURCHASE_PRICE_PAISE = 14900


def plan_rank(plan: str) -> int:
    """free=0, pro=1, team=2 — for require_plan(min_plan) comparisons. An
    unrecognized value ranks as free (fail closed, not open)."""
    return PLAN_ORDER.index(plan) if plan in PLAN_ORDER else 0


def credit_cost(action_key: str) -> int:
    return CREDIT_COSTS[action_key]


def public_pricing_payload() -> dict:
    """Everything GET /api/billing/pricing returns. The frontend must never
    hardcode a price or cost — it always reads this."""
    return {
        "gst_rate": GST_RATE,
        "credit_costs": CREDIT_COSTS,
        "credit_packs": CREDIT_PACKS,
        "plan_prices": PLAN_PRICES,
        "report_purchase_price_paise": REPORT_PURCHASE_PRICE_PAISE,
    }
