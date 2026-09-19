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
    "forecast": 8,
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


# ═════════════════════════════════════════════════════════════════════════════
# PRICE BOOK v2 — the decided model from docs/PRICING_MODEL.md (rev 6).
#
# ADDITIVE ONLY. Nothing above this line changed, and nothing below is read by
# live billing yet: the placeholders above (PLAN_PRICES pro/team, CREDIT_PACKS,
# ...) are still what checkout charges. These constants exist so the billing-v2
# work (company-level plan/credits, subscriptions, monthly grants, seat and
# signal-tier enforcement) has one decided source of truth to build against,
# and so the numbers can be checked against the doc by tests before anything
# starts depending on them. Do not edit the placeholders above in place —
# that changes live prices; cut over deliberately, with a new PRICE_BOOK_VERSION.
#
# PRICE_BOOK_VERSION is stamped onto every order so cohorts that saw different
# prices stay separable later. The doc has no elasticity/willingness-to-pay
# data (there are no customers yet), so the first real price experiments need
# this tag to be comparable.
# ═════════════════════════════════════════════════════════════════════════════

PRICE_BOOK_VERSION = "2026-09-rev6"

TRIAL_DAYS = 7
TRIAL_CREDITS = 500            # total for the trial, not per month
ANNUAL_DISCOUNT = 0.20         # -20% on the monthly price
ANNUAL_CREDIT_BONUS = 0.05     # +5% credits every month on an annual term
PLAN_CREDIT_ROLLOVER_DAYS = 31     # plan credits roll one month, then expire
TOPUP_CREDIT_ROLLOVER_DAYS = 60
DUNNING_DAYS = 3               # failed renewal: retry + email, then soft-lock
UPI_AUTOPAY_MAX_PAISE = 1_500_000  # RBI e-mandate cap: Rs 15,000 per cycle

# Dashboard tiers. Prices in INR paise (monthly). `annual_monthly_paise` is the
# effective per-month price on an annual term (= monthly x 0.8). Enterprise is
# "from" pricing and negotiated: None means custom/unlimited/negotiated.
# `self_serve` is False where the RBI e-mandate cap (or policy) forces the
# sales-assisted invoice/NEFT path (decision #12).
TIERS = {
    "trial": {
        "label": "Trial", "price_paise": 0, "annual_monthly_paise": None,
        "credits_per_month": 0, "companies": 1, "extra_company_paise": None,
        "seats": 2, "extra_seat_paise": None, "keywords_per_project": 5,
        "export_api": None, "self_serve": True,
    },
    "starter": {
        "label": "Starter", "price_paise": 500_000, "annual_monthly_paise": 400_000,
        "credits_per_month": 1_000, "companies": 1, "extra_company_paise": 250_000,
        "seats": 3, "extra_seat_paise": 90_000, "keywords_per_project": 15,
        "export_api": None, "self_serve": True,
    },
    "growth": {
        "label": "Growth", "price_paise": 1_200_000, "annual_monthly_paise": 960_000,
        "credits_per_month": 3_000, "companies": 1, "extra_company_paise": 250_000,
        "seats": 6, "extra_seat_paise": 90_000, "keywords_per_project": 15,
        "export_api": "rate_limited", "self_serve": True,
    },
    "scale": {
        "label": "Scale", "price_paise": 2_500_000, "annual_monthly_paise": 2_000_000,
        "credits_per_month": 7_000, "companies": 3, "extra_company_paise": 300_000,
        "seats": 12, "extra_seat_paise": 80_000, "keywords_per_project": 30,
        "export_api": "full", "self_serve": False,
    },
    "pro": {
        "label": "Pro", "price_paise": 5_000_000, "annual_monthly_paise": 4_000_000,
        "credits_per_month": 16_000, "companies": 10, "extra_company_paise": 500_000,
        "seats": 25, "extra_seat_paise": 70_000, "keywords_per_project": 50,
        "export_api": "full", "self_serve": False,
    },
    "enterprise": {
        "label": "Enterprise", "price_paise": 10_000_000, "annual_monthly_paise": None,
        "credits_per_month": 40_000, "companies": None, "extra_company_paise": None,
        "seats": None, "extra_seat_paise": None, "keywords_per_project": None,
        "export_api": "full", "self_serve": False,
    },
}
TIER_ORDER = ["free", "trial", "starter", "growth", "scale", "pro", "enterprise"]

# The signal-only ladder (no dashboard, no credits, month-to-month, no annual).
# A dashboard tier supersedes these — all 20 signals are included.
CORE_SIGNALS = ("ppi_ml", "est_monthly_income_hh", "est_monthly_spend_hh")
SIGNAL_TIERS = {
    "lite": {
        "label": "Signals Lite", "price_paise": 20_000,
        # PROPOSAL from the doc (§2) — still an open question (§10 #1).
        "extra_signals": ("bank_branches_per_lakh", "upi_txn_value_per_capita",
                          "deposits_per_capita", "msme_per_lakh", "nsdp_per_capita",
                          "premium_poi_per_km2", "radiance_mean", "cars_per_1000",
                          "car_2w_ratio", "luxury_share"),
    },
    "pro": {"label": "Signals Pro", "price_paise": 50_000, "extra_signals": "all"},
}

# Metered actions beyond the three already in CREDIT_COSTS above.
KEYWORD_RESEARCH_CREDITS = 12
EXTRA_PROJECT_CREDITS = 100
SELF_INTELLIGENCE_RUN_CREDITS = 15

# Top-up packs, priced 10-20% over the plan's implied per-credit rate so
# upgrading is the cheaper path past a threshold. Unvalidated against real
# usage (doc §10 #2).
TOPUP_PACKS = {
    "topup_500":  {"credits": 500,  "price_paise": 300_000,   "label": "500 credits"},
    "topup_2000": {"credits": 2000, "price_paise": 1_000_000, "label": "2,000 credits"},
    "topup_5000": {"credits": 5000, "price_paise": 2_200_000, "label": "5,000 credits"},
}

# ── Two plan vocabularies share one column — keep them unambiguous ──────────
# Pre-v2 accounts hold 'free' / 'pro' / 'team' (users.plan has a CHECK
# constraint pinning exactly those). v2 has its own tier called "Pro"
# (Rs 50,000/mo) — the same string as the legacy Rs 999 'pro' plan. Because
# organizations.plan will hold BOTH vocabularies over the migration, a bare
# 'pro' there is ambiguous and would silently mis-bill in one direction or
# the other. So v2 ids are STORED namespaced as 'v2_<tier>' ('v2_pro',
# 'v2_starter', ...); 'free' means the same thing in both and stays bare.
# Always go through plan_id_v2()/parse_plan() — never compare raw strings
# against TIERS keys.
V2_PREFIX = "v2_"


def plan_id_v2(tier: str) -> str:
    """Stored plan id for a v2 tier: 'starter' -> 'v2_starter'."""
    if tier not in TIERS:
        raise ValueError(f"unknown v2 tier: {tier!r}")
    return V2_PREFIX + tier


def parse_plan(plan_id: str):
    """('v2', tier) | ('legacy', plan) | ('free', 'free') | ('unknown', plan_id).
    Fails closed: anything unrecognised is 'unknown', never assumed paid."""
    if plan_id == "free":
        return ("free", "free")
    if isinstance(plan_id, str) and plan_id.startswith(V2_PREFIX) and plan_id[len(V2_PREFIX):] in TIERS:
        return ("v2", plan_id[len(V2_PREFIX):])
    if plan_id in PLAN_ORDER:            # legacy: 'pro' / 'team' (and 'free' handled above)
        return ("legacy", plan_id)
    return ("unknown", plan_id)


# Grandfathering: a legacy plan is treated as AT LEAST this v2 tier until a
# customer is deliberately migrated (the owner's own account is a plain
# plan='pro' flip). This only names the floor so code can reason about a
# legacy plan without inventing a mapping; the real mapping is a decision.
LEGACY_PLAN_FLOOR = {"pro": "starter", "team": "growth"}


def tier_rank(plan_id: str) -> int:
    """Rank across both vocabularies on the TIER_ORDER scale (free=0, trial=1,
    starter=2 ... enterprise=6). Legacy plans rank at their grandfathered
    floor; unknown ranks as free (fail closed)."""
    kind, name = parse_plan(plan_id)
    if kind == "v2":
        return TIER_ORDER.index(name)
    if kind == "legacy":
        return TIER_ORDER.index(LEGACY_PLAN_FLOOR[name])
    return 0


def is_dashboard_tier(plan_id: str) -> bool:
    """True if this plan includes the dashboard (trial or any paid tier).
    Free, unknown and the signal-only SKUs do not. Legacy 'pro'/'team' count as
    dashboard plans (grandfathered)."""
    return tier_rank(plan_id) >= TIER_ORDER.index("trial")


# ── What a plan actually unlocks TODAY (the enforced surface) ────────────────
# Only two things are gated by plan in the running product: the Pro signal
# columns on /api/export (and in the map's Pro rows), and the raised rate limit
# on an API key. Everything else is gated by credits, not plan. Both plan
# vocabularies answer through here so no gate compares a raw plan string.
#   legacy 'pro' / 'team'  -> both (exactly what they had before v2)
#   v2 dashboard tiers     -> the Pro columns (a dashboard tier includes all 20
#                             signals); the raised API limit only where the
#                             tier's price-book row includes export_api
#   free / unknown         -> neither (fail closed)
def entitlements(plan_id) -> dict:
    kind, name = parse_plan(plan_id)
    if kind == "legacy":
        return {"pro_columns": True, "api_elevated": True}
    if kind == "v2":
        return {"pro_columns": True, "api_elevated": TIERS[name]["export_api"] is not None}
    return {"pro_columns": False, "api_elevated": False}


def plan_strength(plan_id) -> tuple:
    """Total order across both vocabularies, used to pick the BEST of several
    plans and to prove a mapping never lowers anyone: more entitlements first,
    then the higher tier."""
    e = entitlements(plan_id)
    return (int(e["pro_columns"]) + int(e["api_elevated"]), tier_rank(plan_id))


def best_plan(*plans) -> str:
    """The strongest of these plan ids (a v2 tier wins a tie against a legacy plan, else the first); 'free' if none."""
    best = "free"
    for p in plans:
        gain, held = plan_strength(p), plan_strength(best)
        # On a tie a v2 tier wins over a legacy plan, so an account that has been
        # mapped (or bought a v2 tier) reads as its v2 tier everywhere.
        if gain > held or (gain == held and parse_plan(p)[0] == "v2" and parse_plan(best)[0] != "v2"):
            best = p
    return best


def compat_plan(plan_id) -> str:
    """The plan as the pre-v2 surfaces understand it — 'free' | 'pro' | 'team' —
    for /api/auth/me's `plan`, the map's Pro pill and require_plan(). A v2
    tier from Scale up reads as 'team', any other dashboard tier as 'pro'."""
    kind, name = parse_plan(plan_id)
    if kind == "legacy":
        return name
    if kind == "v2":
        return "team" if tier_rank(plan_id) >= TIER_ORDER.index("scale") else "pro"
    return "free"


def tier_label(plan_id) -> str:
    """Human label for any plan id ('Growth', 'Pro (legacy)', 'Free')."""
    kind, name = parse_plan(plan_id)
    if kind == "v2":
        return TIERS[name]["label"]
    if kind == "legacy":
        return f"{PLAN_PRICES[name]['label']} (legacy)"
    return "Free"


def annual_credits_per_month(tier: str) -> int:
    """Monthly credit grant on an annual term (+5%, rounded up). Takes a bare
    v2 tier name ('starter'), not a stored plan id."""
    import math
    return math.ceil(TIERS[tier]["credits_per_month"] * (1 + ANNUAL_CREDIT_BONUS))


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
        "price_book_version": PRICE_BOOK_VERSION,
    }
