"""
_forecast_model.py — the "investment → growth" model behind GET /api/forecast
(map-first workspace, P3). Pure Python, no Flask / no blueprint imports: the
blueprint (blueprints/forecast.py) loads the signals/geography/diagnostics data,
computes revenue drivers via the existing expansion._compute_drivers, and hands
everything here as plain arguments.

WHAT THIS IS (and is NOT)
========================
There is **no time-series store-ramp data** anywhere in this codebase — a
customer upload (customer_locations) is a single revenue/rent/capex snapshot per
store, not a monthly history. So the saturation curve is NOT fitted on a ramp.
Instead the model is fully **cross-sectional**, and produces a real forecast for
EVERY project by default — driven by its chosen signals, target location(s) and
budget — with uploaded stores layering on as an optional calibration boost, not
a requirement:

  1. Reachable household spend per pincode
       households(pincode) x est_monthly_spend_hh(pincode)
     Households are estimated by splitting district census population across the
     district's pincodes, weighted by a density proxy (night-lights + premium
     POI + financial-branch density), / AVG_HOUSEHOLD_SIZE. This estimate has a
     real absolute error, but a *systematic* one — which the capture-rate
     calibration in step 3 largely cancels out.

  2. Catchment aggregation
       sum, over every pincode whose centroid is within `catchment_km`, of that
       pincode's reachable spend x a linear distance-decay weight.

  3. Capture rate — three tiers, all producing a real forecast (fit_capture_model,
     benchmark_capture_model):
       capture_i = monthly_revenue_i / catchment_reachable_spend_i
     >= REGRESSION_MIN_STORES (8) usable stores -> regressed (pure-Python OLS,
     <=2 predictors) on PPI percentile + driver-fit score. >= MIN_STORES (3) ->
     the pooled median capture. Fewer (including zero) -> DEFAULT_CAPTURE_RATE,
     a documented benchmark, nudged by each location's own PPI percentile —
     labelled `calibration: "benchmark"` everywhere it's surfaced so it's never
     confused with a real fit. Upload store data to move up a tier.

  4. Diminishing returns — two mechanisms, both real:
       (a) the portfolio is filled greedily by predicted-revenue-per-rupee, so
           each additional rupee buys a less productive site (the curve bends);
       (b) a new store inside an existing/selected store's catchment has its
           reachable spend discounted for the overlap (cannibalisation).

  5. Outputs: the reach curve, the recommended rupee split by state, payback
     (needs the project's gross_margin_pct + revenue_period), market-capture %,
     a lever-fit radar (do the project's chosen signals actually move revenue?),
     a rule-based budget-split-by-lever allocation (compute_lever_split), and a
     per-site SWOT of composite signal-proxy factors (location_swot) for the
     top few recommended sites. `recommended_portfolio.sites` is ranked
     best-first regardless of whether the budget can actually afford each one
     (each carries `within_budget`) — so "which locations yield well" and their
     SWOT stay informative even when the budget is too small to fund a site.

Every constant is in ASSUMPTIONS and echoed in the response. Nothing here is
presented as more precise than "a modelled projection" — and nothing here
(manufacturing favorability, raw-material availability, footfall, etc.) is
presented as a literal measurement the codebase doesn't have; every such
composite factor carries a `basis` string naming exactly which real signals
it's derived from.
"""

import bisect
import math
import csv

import _signals_data

MIN_STORES = 3              # below this: benchmark capture (not calibrated on your stores)
REGRESSION_MIN_STORES = 8   # below this: pooled-median capture, not a fit
REGRESSION_MIN_R2 = 0.15    # a fit weaker than this is no better than the pooled median
AVG_HOUSEHOLD_SIZE = 4.6    # Census of India 2011 national average household size
DEFAULT_GROSS_MARGIN_PCT = 35.0   # blended retail gross margin, used only if the project has none
# Benchmark capture, when there's no store revenue to calibrate on (< MIN_STORES), is
# built as an explicit, auditable chain rather than one opaque number:
#   effective take = CATEGORY_WALLET_SHARE  x  ENTRANT_CAPTURE
# i.e. "of all household spend in the catchment, this retail category commands ~5% of
# the wallet, and a new entrant wins ~0.8% of that category spend against incumbents."
# Net ~0.04% of total catchment spend — ~12x lower than the old flat 0.5%, which
# implied a single dense-urban store doing >Rs 5cr/month (15-30x real single-store retail).
CATEGORY_WALLET_SHARE = 0.05      # share of the total household wallet a mid-size retail category commands
ENTRANT_CAPTURE = 0.010          # a new entrant's share of that category spend within the catchment
DEFAULT_CAPTURE_RATE = CATEGORY_WALLET_SHARE * ENTRANT_CAPTURE   # ~= 0.0005; nudged +/-40% by PPI in benchmark_capture_model
REVENUE_INCOME_CAP_MULT = 0.15   # hard ceiling: one store's modelled monthly revenue can't exceed this x the
                                  # catchment's modelled monthly household INCOME — a backstop against a very
                                  # dense catchment implying an implausible single-store figure.
# The district-population -> pincode household split systematically over-credits
# pincodes in districts with few pincodes (a Haridwar pincode can end up modelled
# with ~all of the district's ~1.9M people). Cap the modelled catchment population
# at a dense-metro ceiling derived from the catchment's own geometry, so reach
# (and every figure downstream) stays physically plausible.
MAX_CATCHMENT_DENSITY = 16000     # persons / km^2 — ceiling on modelled catchment population density
CATCHMENT_DECAY_INTEGRAL = 1.0 / 3.0   # area-average of the linear distance-decay weight over the disc
NATIONAL_SEED_TOP_N = 500         # candidate seed size when a project has neither stores nor target_pincodes
RAMP_MONTHS = 12           # a new store is assumed to reach modelled steady-state revenue linearly over this many months
CURVE_STEPS = 14
DIMINISHING_RETURNS_RATIO = 0.5   # marginal revenue/rupee below this fraction of the first segment's => "diminishing" zone
EXPANSION_MARGIN_KM = 25.0       # how far beyond the catchment radius to look for candidate pincodes
MAX_CANDIDATES = 3000            # hard cap on the candidate universe (keeps the request O(seconds))
DENSITY_PROXY_COLS = ("radiance_mean", "premium_poi_per_km2", "fin_density_per_km2")
CANNIBALISATION_SHARE = 0.5      # fraction of an overlap that a new store cedes to the incumbent
DEFAULT_STORE_SQFT = 800         # typical small-format retail footprint — used only when neither your
                                  # own stores' capex nor their rent/rate ratio is available (e.g. a
                                  # brand-new project with no store data yet)

# lever-split allocator — six spend categories a business can put budget behind.
LEVER_CATEGORIES = (
    "advertising", "sales_distribution", "promotions", "rnd_product", "production_supply_chain", "ops_capex",
)
LEVER_LABELS = {
    "advertising": "Advertising & Marketing",
    "sales_distribution": "Sales & Distribution",
    "promotions": "Promotions & Discounts",
    "rnd_product": "R&D / Product Development",
    "production_supply_chain": "Production & Supply Chain",
    "ops_capex": "Store Ops & CapEx",
}
# "lean" (small budget) vs "scale" (large budget) allocation profiles — interpolated
# on a log scale between LEAN_BUDGET/SCALE_BUDGET. Both are hand-set starting points,
# then tilted by the candidate locations' own signal profile (see compute_lever_split).
LEVER_LEAN_PROFILE = {
    "advertising": 30, "sales_distribution": 22, "promotions": 18,
    "rnd_product": 8, "production_supply_chain": 12, "ops_capex": 10,
}
LEVER_SCALE_PROFILE = {
    "advertising": 18, "sales_distribution": 16, "promotions": 10,
    "rnd_product": 18, "production_supply_chain": 24, "ops_capex": 14,
}
LEAN_BUDGET = 5e4     # ₹50k — fully "lean" profile at/below this
SCALE_BUDGET = 5e7    # ₹5cr — fully "scale" profile at/above this

# SWOT composite proxies — percentile thresholds for bucketing.
SWOT_STRENGTH_PCT = 65.0
SWOT_WEAKNESS_PCT = 35.0

ASSUMPTIONS = {
    "avg_household_size": AVG_HOUSEHOLD_SIZE,
    "household_estimation": (
        "District census population split across the district's pincodes, weighted by "
        "a density proxy (night-lights radiance, premium-POI density, financial-branch "
        "density); pincodes in an unmatched district get their state's median estimate."
    ),
    "reachable_spend": (
        "Total modelled monthly household spend inside the catchment (linear distance "
        "decay to the radius edge). This is ALL household spend, not just this category "
        "— the capture rate calibrated on your own stores absorbs your category's share."
    ),
    "capture_rate": (
        f"Fitted on your stores: capture = revenue / catchment reachable spend. "
        f">= {REGRESSION_MIN_STORES} stores -> regression on PPI percentile + driver fit; "
        f"{MIN_STORES}-{REGRESSION_MIN_STORES - 1} -> your stores' median capture; "
        f"fewer than {MIN_STORES} -> a benchmark rate ({DEFAULT_CAPTURE_RATE * 100:.3f}% of catchment "
        f"spend), nudged by each location's purchasing-power percentile — upload store data for a rate "
        f"fitted on your own performance."
    ),
    "category_wallet_share": (
        f"{CATEGORY_WALLET_SHARE * 100:.0f}% — the assumed share of total household wallet a mid-size "
        f"retail category commands. One of the two factors behind the benchmark capture rate."
    ),
    "new_entrant_capture": (
        f"{ENTRANT_CAPTURE * 100:.1f}% — the assumed share of that category spend a new entrant wins "
        f"against incumbents in a competitive catchment. The second factor behind the benchmark rate."
    ),
    "revenue_ceiling": (
        f"A single store's modelled monthly revenue is capped at {REVENUE_INCOME_CAP_MULT * 100:.0f}% "
        f"of its catchment's modelled monthly household income — a backstop against dense catchments."
    ),
    "capex_estimate": (
        f"Your stores' median fit-out cost if available; else assumed store size x local property rate; "
        f"else (no store data at all) a default {DEFAULT_STORE_SQFT}-sqft store x local property rate."
    ),
    "diminishing_returns": (
        "The portfolio is filled best-revenue-per-rupee first, and a new store inside "
        "another store's catchment is discounted for the overlap — so each extra rupee "
        "reaches less incremental spend than the last."
    ),
    "ramp_months": RAMP_MONTHS,
    "default_gross_margin_pct": DEFAULT_GROSS_MARGIN_PCT,
}


# ── district population ───────────────────────────────────────────────────────
_district_pop_cache = None


def load_district_population():
    """(state_code, normalised district) -> population, from the same census
    reference file the ETL already ships. Cached like load_geography()."""
    global _district_pop_cache
    if _district_pop_cache is not None:
        return _district_pop_cache
    out = {}
    path = _signals_data.REFERENCE / "district_population_census.csv"
    if path.exists():
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                sc = (r.get("state_code") or "").strip().upper()
                dist = _norm_district(r.get("district"))
                pop = _signals_data.coerce(r.get("population"))
                if sc and dist and isinstance(pop, (int, float)) and pop > 0:
                    out[(sc, dist)] = pop
    _district_pop_cache = out
    return out


def _norm_district(name):
    if not name:
        return None
    n = name.strip().lower()
    for suffix in (" district", " dist", " (rural)", " (urban)"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return n.strip() or None


def _num(v):
    v = _signals_data.coerce(v)
    return v if isinstance(v, (int, float)) else None


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx * vy) ** 0.5


# ── household estimation ──────────────────────────────────────────────────────
def estimate_households(rows_by_pincode, geography, district_pop):
    """pincode -> estimated household count (float) or None when even the state
    fallback has nothing. See ASSUMPTIONS['household_estimation']."""
    # group pincodes by (state_code, normalised district)
    groups = {}
    for pc, row in rows_by_pincode.items():
        geo = geography.get(pc)
        if not geo:
            continue
        sc = (geo.get("state_code") or "").strip().upper()
        dist = _norm_district(geo.get("district"))
        if not sc or not dist:
            continue
        groups.setdefault((sc, dist), []).append(pc)

    hh = {}
    state_estimates = {}  # state_code -> list of per-pincode hh, for the fallback median
    for (sc, dist), pcs in groups.items():
        pop = district_pop.get((sc, dist))
        if pop is None:
            continue
        # density proxy per pincode, normalised within the district
        proxies = {}
        col_max = {c: 0.0 for c in DENSITY_PROXY_COLS}
        for pc in pcs:
            row = rows_by_pincode[pc]
            for c in DENSITY_PROXY_COLS:
                v = _num(row.get(c))
                if v is not None and v > col_max[c]:
                    col_max[c] = v
        for pc in pcs:
            row = rows_by_pincode[pc]
            parts = [_num(row.get(c)) / col_max[c] for c in DENSITY_PROXY_COLS
                     if col_max[c] > 0 and _num(row.get(c)) is not None]
            proxies[pc] = sum(parts) / len(parts) if parts else None
        known = [v for v in proxies.values() if v is not None]
        fill = sum(known) / len(known) if known else 1.0
        weights = {pc: (proxies[pc] if proxies[pc] is not None else fill) for pc in pcs}
        total_w = sum(weights.values()) or len(pcs)
        for pc in pcs:
            h = pop * (weights[pc] / total_w) / AVG_HOUSEHOLD_SIZE
            hh[pc] = h
            state_estimates.setdefault(sc, []).append(h)

    state_median = {sc: _median(v) for sc, v in state_estimates.items() if v}
    for pc, row in rows_by_pincode.items():
        if pc in hh:
            continue
        geo = geography.get(pc) or {}
        sc = (geo.get("state_code") or "").strip().upper()
        if sc in state_median:
            hh[pc] = state_median[sc]
    return hh


def _estimate_store_sqft(locations, rows_by_pincode):
    """Infer an average store size from rent / local rate_per_sqft, across
    whichever stores have both. None (not a fabricated default) if nothing
    qualifies. Same heuristic as expansion._estimate_store_sqft — duplicated
    (10 lines) rather than importing a blueprint back into an etl module."""
    sizes = []
    for loc in locations:
        rent = _num(loc.get("rent"))
        row = rows_by_pincode.get(loc.get("pincode"))
        if rent is None or not row:
            continue
        rate = _num(row.get("rate_per_sqft"))
        if rate:
            sizes.append(rent / rate)
    return sum(sizes) / len(sizes) if sizes else None


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# ── spatial index ────────────────────────────────────────────────────────────
_CELL_DEG = 0.5


def _cell(lat, lng):
    return (math.floor(lat / _CELL_DEG), math.floor(lng / _CELL_DEG))


def build_spatial_index(rows_by_pincode, hh_by_pincode):
    """cell -> list of (pincode, lat, lng, spend_unit, income_unit) where the units
    are that pincode's own monthly household spend / income (households x per-hh)."""
    index = {}
    for pc, row in rows_by_pincode.items():
        lat, lng = _num(row.get("lat")), _num(row.get("lng"))
        hh = hh_by_pincode.get(pc)
        spend = _num(row.get("est_monthly_spend_hh"))
        if lat is None or lng is None or hh is None or spend is None:
            continue
        income = _num(row.get("est_monthly_income_hh"))
        index.setdefault(_cell(lat, lng), []).append(
            (pc, lat, lng, hh * spend, hh * income if income is not None else hh * spend))
    return index


def _nearby(index, lat, lng, radius_km):
    span = int(math.ceil(radius_km / 111.0 / _CELL_DEG)) + 1
    c0 = _cell(lat, lng)
    for di in range(-span, span + 1):
        for dj in range(-span, span + 1):
            for entry in index.get((c0[0] + di, c0[1] + dj), ()):
                yield entry


def catchment_reachable_spend(lat, lng, catchment_km, index, spend_hh=None, income_hh=None):
    """Sum of spend/income units x linear distance-decay over pincodes within the
    catchment, capped at a geometric dense-metro ceiling when the centre pincode's
    per-household spend/income is known. Returns (total_spend, total_income,
    [pincodes touched])."""
    total = 0.0
    total_income = 0.0
    touched = []
    for pc, plat, plng, unit, income_unit in _nearby(index, lat, lng, catchment_km):
        d = _signals_data.haversine_km(lat, lng, plat, plng)
        if d <= catchment_km:
            w = max(0.0, 1.0 - d / catchment_km)
            total += unit * w
            total_income += income_unit * w
            touched.append(pc)
    max_hh = (math.pi * catchment_km ** 2) * MAX_CATCHMENT_DENSITY / AVG_HOUSEHOLD_SIZE * CATCHMENT_DECAY_INTEGRAL
    if spend_hh:
        total = min(total, max_hh * spend_hh)
    if income_hh:
        total_income = min(total_income, max_hh * income_hh)
    return total, total_income, touched


# ── circle overlap (cannibalisation) ─────────────────────────────────────────
def _overlap_fraction(d, r):
    """Fraction of one radius-r circle's area covered by another radius-r circle
    whose centre is distance d away."""
    if d <= 0:
        return 1.0
    if d >= 2 * r:
        return 0.0
    lens = 2 * r * r * math.acos(d / (2 * r)) - (d / 2) * math.sqrt(max(0.0, 4 * r * r - d * d))
    return max(0.0, min(1.0, lens / (math.pi * r * r)))


def _cannibalisation_discount(lat, lng, others, catchment_km):
    """others: list of (lat, lng). Product of (1 - CANNIBALISATION_SHARE x overlap)."""
    disc = 1.0
    for olat, olng in others:
        d = _signals_data.haversine_km(lat, lng, olat, olng)
        if d < 2 * catchment_km:
            disc *= (1.0 - CANNIBALISATION_SHARE * _overlap_fraction(d, catchment_km))
    return max(0.2, disc)


# ── tiny OLS (normal equations, <=3 columns incl. intercept) ─────────────────
def _solve(a, b):
    """Gaussian elimination for a small square system. Returns None if singular."""
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col] / m[col][col]
            for k in range(col, n + 1):
                m[r][k] -= f * m[col][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def _ols(rows_x, ys):
    """rows_x: list of feature tuples (WITHOUT intercept). Returns (coeffs incl.
    intercept as [0], r2) or None."""
    n = len(ys)
    k = len(rows_x[0])
    design = [(1.0,) + tuple(r) for r in rows_x]
    ata = [[sum(design[p][i] * design[p][j] for p in range(n)) for j in range(k + 1)] for i in range(k + 1)]
    aty = [sum(design[p][i] * ys[p] for p in range(n)) for i in range(k + 1)]
    coef = _solve(ata, aty)
    if coef is None:
        return None
    mean_y = sum(ys) / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((ys[p] - sum(coef[i] * design[p][i] for i in range(k + 1))) ** 2 for p in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return coef, r2


class CaptureModel:
    def __init__(self, method, predict_fn, median_capture, residual_cv, r2, n_used):
        self.method = method
        self._predict = predict_fn
        self.median_capture = median_capture
        self.residual_cv = residual_cv
        self.r2 = r2
        self.n_used = n_used

    def predict(self, ppi_pct, driver_fit):
        cap = self._predict(ppi_pct, driver_fit)
        lo, hi = 0.25 * self.median_capture, 4.0 * self.median_capture
        return max(lo, min(hi, cap))

    def as_dict(self):
        return {
            "method": self.method,
            "median_capture_rate": round(self.median_capture, 6),
            "r2": round(self.r2, 3) if self.r2 is not None else None,
            "confidence_band_pct": round(self.residual_cv * 100, 1),
            "stores_used": self.n_used,
        }


def fit_capture_model(observations):
    """observations: list of {capture, ppi_pct, driver_fit}. driver_fit may be
    None. Returns a CaptureModel or None (insufficient)."""
    obs = [o for o in observations if o["capture"] is not None and o["capture"] > 0]
    if len(obs) < MIN_STORES:
        return None
    captures = [o["capture"] for o in obs]
    med = _median(captures)
    have_drivers = all(o["driver_fit"] is not None for o in obs)

    if len(obs) >= REGRESSION_MIN_STORES:
        feats = [(o["ppi_pct"], o["driver_fit"]) for o in obs] if have_drivers else [(o["ppi_pct"],) for o in obs]
        # need real variation in every predictor, else OLS is degenerate
        if all(len({round(f[i], 4) for f in feats}) > 1 for i in range(len(feats[0]))):
            fit = _ols(feats, captures)
            if fit is not None and fit[1] >= REGRESSION_MIN_R2:
                coef, r2 = fit
                def _p(ppi_pct, driver_fit, _c=coef, _d=have_drivers):
                    x = [1.0, ppi_pct] + ([driver_fit if driver_fit is not None else 0.5] if _d else [])
                    return sum(_c[i] * x[i] for i in range(len(_c)))
                resid = [captures[p] - _p(feats[p][0], feats[p][1] if have_drivers else None) for p in range(len(obs))]
                cv = (_median([abs(r) for r in resid]) or 0.0) / med if med else 0.35
                return CaptureModel("regression", _p, med, min(cv, 0.6), r2, len(obs))

    # pooled median
    spread = [abs(c - med) for c in captures]
    cv = (_median(spread) or 0.0) / med if med else 0.35
    return CaptureModel("pooled_median", lambda a, b: med, med, min(max(cv, 0.15), 0.6), None, len(obs))


def benchmark_capture_model():
    """No store revenue to calibrate on (< MIN_STORES usable stores) — DEFAULT_CAPTURE_RATE,
    nudged +/-40% by the location's own PPI percentile (better purchasing power => assumed to
    convert reachable spend a bit better). Not fitted on anything; `method == "benchmark"`
    everywhere this is surfaced so it's never confused with a real fit."""
    def _predict(ppi_pct, driver_fit):
        tilt = ((ppi_pct if ppi_pct is not None else 0.5) - 0.5) * 0.8  # +/-0.4 at the extremes
        return DEFAULT_CAPTURE_RATE * (1.0 + tilt)
    return CaptureModel("benchmark", _predict, DEFAULT_CAPTURE_RATE, 0.5, None, 0)


# ── driver-fit percentile helper ────────────────────────────────────────────
def _driver_fit_scorer(top_drivers, rows_by_pincode):
    """Returns fit(row) -> 0..1, the average percentile of the row across the
    project's revenue drivers (inverted for negatively-correlated ones). Mirrors
    expansion.recommend's driver_fit_score, on a 0-1 scale."""
    buckets = {}
    for d in top_drivers:
        col = d["signal"]
        vals = sorted(v for v in (_num(r.get(col)) for r in rows_by_pincode.values()) if v is not None)
        if vals:
            buckets[col] = vals
    if not buckets:
        return None

    def fit(row):
        parts = []
        for d in top_drivers:
            col = d["signal"]
            b = buckets.get(col)
            v = _num(row.get(col))
            if not b or v is None:
                continue
            pct = bisect.bisect_right(b, v) / len(b)
            parts.append(1.0 - pct if d["direction"] == "negative" else pct)
        return sum(parts) / len(parts) if parts else None

    return fit


AFFORDABILITY_FLOOR_PCT = 2.0   # ticket <= this % of monthly household spend -> no affordability penalty
AFFORDABILITY_CEIL_PCT = 20.0   # ticket >= this % of monthly household spend -> affordability_fit floors at 0


def _opportunity_score(economic_score_100, avg_ticket, spend):
    """economic_score (0-100) blended with ticket-size affordability fit, same formula as
    blueprints/intelligence.py's opportunity_assessment — duplicated (a dozen lines) rather
    than importing a blueprint back into this etl module, same convention as
    _estimate_store_sqft above. Returns (opportunity_score, ticket_pct_of_spend|None)."""
    if not avg_ticket or not spend:
        return economic_score_100, None
    ticket_pct = avg_ticket / spend * 100
    if ticket_pct <= AFFORDABILITY_FLOOR_PCT:
        fit = 100.0
    elif ticket_pct >= AFFORDABILITY_CEIL_PCT:
        fit = 0.0
    else:
        span = AFFORDABILITY_CEIL_PCT - AFFORDABILITY_FLOOR_PCT
        fit = 100 * (1 - (ticket_pct - AFFORDABILITY_FLOOR_PCT) / span)
    return round(economic_score_100 * 0.7 + fit * 0.3, 1), round(ticket_pct, 2)


def _risk_level(diagnostics, pincode):
    """Duplicated (same convention, ~8 lines) from intelligence.py's risk_assessment —
    just the level + score, the narrative text lives only in the intelligence endpoints."""
    anomaly = (diagnostics.get("anomalies") or {}).get(pincode) if diagnostics else None
    score = anomaly.get("anomaly_score") if anomaly else None
    if score is None:
        return "Unknown", None
    if score >= 0.65:
        return "High", score
    if score >= 0.4:
        return "Medium", score
    return "Low", score


# ── lever-split allocator ─────────────────────────────────────────────────────
def compute_lever_split(budget, rows_by_pincode, candidate_pincodes):
    """How the budget should split across 6 spend levers. A rule-based allocation
    framework, NOT a performance-calibrated model (there's no data source in this
    codebase for "how well does a rupee of R&D spend perform") — label it as such
    wherever it's surfaced. Two real, documented inputs tilt the two hand-set base
    profiles (LEVER_LEAN_PROFILE for a small budget, LEVER_SCALE_PROFILE for a large
    one): the budget size itself (log-interpolated between LEAN_BUDGET/SCALE_BUDGET),
    and the recommended locations' own signal profile. Recomputes on every call, so
    it naturally recalibrates whenever the budget or portfolio changes."""
    b = max(LEAN_BUDGET, min(SCALE_BUDGET, budget))
    t = (math.log(b) - math.log(LEAN_BUDGET)) / (math.log(SCALE_BUDGET) - math.log(LEAN_BUDGET))
    weighted = {k: LEVER_LEAN_PROFILE[k] * (1 - t) + LEVER_SCALE_PROFILE[k] * t for k in LEVER_CATEGORIES}

    rows = [rows_by_pincode[pc] for pc in candidate_pincodes if pc in rows_by_pincode]

    def _pct_avg(col):
        vals = sorted(v for v in (_num(r.get(col)) for r in rows_by_pincode.values()) if v is not None)
        if not vals or not rows:
            return 0.5
        pcts = [bisect.bisect_right(vals, _num(r.get(col))) / len(vals) for r in rows if _num(r.get(col)) is not None]
        return sum(pcts) / len(pcts) if pcts else 0.5

    manufacturing = (_pct_avg("factories_per_lakh") + _pct_avg("msme_per_lakh")) / 2
    distribution = (_pct_avg("bank_branches_per_lakh") + _pct_avg("fin_density_per_km2")) / 2
    demand_density = (_pct_avg("premium_poi_per_km2") + _pct_avg("est_monthly_income_hh")) / 2

    # tilts shift weight toward the lever a location's own profile favours; the
    # equal-and-opposite shift always lands on ops_capex so the six numbers keep
    # summing to (approximately) 100 before the final normalisation pass below.
    tilts = {
        "production_supply_chain": (manufacturing - 0.5) * 12,
        "rnd_product": (manufacturing - 0.5) * 6,
        "sales_distribution": (0.5 - distribution) * 12,   # weak distribution -> spend MORE building it
        "advertising": (demand_density - 0.5) * 10,
        "promotions": (demand_density - 0.5) * 6,
    }
    for k, dv in tilts.items():
        weighted[k] += dv
    weighted["ops_capex"] -= sum(tilts.values())

    total = sum(weighted.values()) or 1.0
    levers = [{"category": k, "label": LEVER_LABELS[k], "pct": max(2.0, weighted[k] / total * 100)}
              for k in LEVER_CATEGORIES]
    norm = sum(l["pct"] for l in levers) or 1.0
    for l in levers:
        l["pct"] = round(l["pct"] / norm * 100, 1)
    drift = round(100 - sum(l["pct"] for l in levers), 1)
    if drift:
        levers.sort(key=lambda l: l["pct"], reverse=True)
        levers[0]["pct"] = round(levers[0]["pct"] + drift, 1)

    return {
        "levers": levers,
        "basis": ("A rule-based allocation framework using your budget size and the recommended "
                  "locations' own signal profile (manufacturing, financial-distribution and "
                  "commercial-density percentiles) — not a performance-calibrated model."),
    }


# ── location SWOT composite ───────────────────────────────────────────────────
SWOT_PROXY_COLS = ("est_monthly_income_hh", "est_monthly_spend_hh", "car_2w_ratio", "luxury_share", "ev_share",
                    "factories_per_lakh", "msme_per_lakh", "cropping_intensity_pct",
                    "bank_branches_per_lakh", "fin_density_per_km2",
                    "cars_per_1000", "lmv_per_1000", "radiance_mean", "premium_poi_per_km2")


def _swot_percentile_lookup(rows_by_pincode):
    """Precompute one sorted-values list per proxy column (not per site) so scoring the
    top-3 sites' SWOT is O(sites x columns x log n), not O(sites x columns x n)."""
    sorted_vals = {}
    for col in SWOT_PROXY_COLS:
        vals = sorted(v for v in (_num(r.get(col)) for r in rows_by_pincode.values()) if v is not None)
        if vals:
            sorted_vals[col] = vals

    def pct(row, col):
        vals = sorted_vals.get(col)
        v = _num(row.get(col))
        return round(bisect.bisect_right(vals, v) / len(vals) * 100, 1) if (vals and v is not None) else None

    return pct


def location_swot(row, pct, opportunity_score, cannibalisation_discount, diagnostics, pincode):
    """Composite proxy scores (0-100 percentile) for a candidate location, bucketed
    SWOT-style. None of these are literal measurements of supply chain / manufacturing
    / footfall etc. — this codebase has no dataset for any of that — every factor
    carries a `basis` string naming exactly which real signals it's derived from."""
    def avg(*vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    factors = [
        {"key": "opportunity", "label": "Opportunity score", "score": opportunity_score,
         "basis": "Purchasing-power percentile blended with your ticket-size affordability fit"},
        {"key": "audience_fit", "label": "Audience demographics fit",
         "score": avg(pct(row, "est_monthly_income_hh"), pct(row, "est_monthly_spend_hh"),
                      pct(row, "car_2w_ratio"), pct(row, "luxury_share"), pct(row, "ev_share")),
         "basis": "Income/spend + vehicle-ownership percentile composite"},
        {"key": "manufacturing", "label": "Manufacturing favorability",
         "score": avg(pct(row, "factories_per_lakh"), pct(row, "msme_per_lakh")),
         "basis": "Factories + MSME density percentile composite"},
        {"key": "raw_material", "label": "Raw-material / resource proxy",
         "score": pct(row, "cropping_intensity_pct"),
         "basis": "Cropping-intensity percentile — an agri-linked proxy, most relevant to "
                  "agriculture/food businesses, not a literal sourcing survey"},
        {"key": "distribution", "label": "Financial distribution access",
         "score": avg(pct(row, "bank_branches_per_lakh"), pct(row, "fin_density_per_km2")),
         "basis": "Bank-branch + financial-institution density percentile composite"},
        {"key": "transport", "label": "Transport & logistics density",
         "score": avg(pct(row, "cars_per_1000"), pct(row, "lmv_per_1000")),
         "basis": "Car + light-commercial-vehicle density percentile composite"},
        {"key": "geography", "label": "Geographic / infrastructure benefit",
         "score": avg(pct(row, "radiance_mean"), pct(row, "premium_poi_per_km2")),
         "basis": "Night-lights + commercial-POI density percentile composite"},
        {"key": "footfall", "label": "Commercial density (footfall proxy)",
         "score": pct(row, "premium_poi_per_km2"),
         "basis": "Commercial POI density percentile — a footfall PROXY, not measured foot traffic"},
    ]

    strengths = [f for f in factors if f["score"] is not None and f["score"] >= SWOT_STRENGTH_PCT]
    weaknesses = [f for f in factors if f["score"] is not None and f["score"] <= SWOT_WEAKNESS_PCT]

    mid = [f for f in factors if f["score"] is not None and SWOT_WEAKNESS_PCT < f["score"] < SWOT_STRENGTH_PCT]
    opportunities = [{"key": f["key"], "label": f"Room to grow: {f['label']}", "basis": f["basis"]}
                      for f in sorted(mid, key=lambda f: -f["score"])[:2]]

    threats = []
    risk_level, anomaly_score = _risk_level(diagnostics, pincode)
    if risk_level in ("High", "Medium"):
        threats.append({"key": "data_reliability", "label": f"{risk_level} data-volatility risk",
                         "basis": f"Anomaly score {round(anomaly_score, 2)} vs. its surrounding area — "
                                  "treat the modelled figures here with extra caution."})
    if cannibalisation_discount is not None and cannibalisation_discount < 0.85:
        threats.append({"key": "cannibalisation", "label": "Overlaps another site's catchment",
                         "basis": f"Reachable spend discounted {round((1 - cannibalisation_discount) * 100)}% "
                                  "for catchment overlap with another site in your portfolio."})

    return {"factors": factors, "strengths": strengths, "weaknesses": weaknesses,
            "opportunities": opportunities, "threats": threats}


# ── per-location operating factors (for the factor table + comparison radar) ──
# Each is a composite percentile (0-100) over real signal columns — NONE is a
# literal survey (this codebase has no supply-chain / footfall / EoDB dataset);
# every one carries a `basis` naming its underlying signals.
FACTOR_DEFS = (
    ("supply_chain", "Supply chain", ("factories_per_lakh", "msme_per_lakh"),
     "Factories + MSME density percentile — a manufacturing / vendor-base proxy"),
    ("distribution", "Distribution access", ("bank_branches_per_lakh", "fin_density_per_km2"),
     "Bank-branch + financial-institution density percentile"),
    ("transport", "Transport & logistics", ("cars_per_1000", "lmv_per_1000"),
     "Car + light-commercial-vehicle density percentile"),
    ("mobility", "Mobility & connectivity", ("cars_per_1000", "radiance_mean"),
     "Vehicle-ownership + night-lights percentile — a road-mobility / built-up-corridor proxy"),
    ("eodb", "Ease of doing business", ("msme_per_lakh", "bank_branches_per_lakh"),
     "MSME formation + bank-branch (credit access) density percentile — a business-environment "
     "proxy, not an official EoDB score"),
    ("geo_infra", "Geo-infra / residential mix", ("premium_poi_per_km2", "luxury_share"),
     "Commercial-POI density + luxury-spend share composite — a proxy for a dense, affluent "
     "residential/retail mix (apartments, malls, schools) vs. a sparse one"),
)


def _factor_score(pct_fn, row, cols):
    vals = [pct_fn(row, c) for c in cols]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def location_factors(pct_fn, row, store_means):
    """List of the FACTOR_DEFS composites for one location, each with its delta
    vs the national median (50) and, when we have store data, vs your stores' mean."""
    out = []
    for key, label, cols, basis in FACTOR_DEFS:
        score = _factor_score(pct_fn, row, cols)
        item = {"key": key, "label": label, "score": score, "basis": basis,
                "delta_vs_median": round(score - 50, 1) if score is not None else None,
                "delta_vs_your_stores": None}
        if store_means and store_means.get(key) is not None and score is not None:
            item["delta_vs_your_stores"] = round(score - store_means[key], 1)
        out.append(item)
    return out


# ── main entry point ─────────────────────────────────────────────────────────
def build_forecast(project, locations, budget, rows_by_pincode, geography, diagnostics,
                   district_pop, drivers):
    """drivers: the dict returned by expansion._compute_drivers (may be
    sufficient_data:false). Everything else is loaded by the blueprint."""
    catchment_km = _num(project.get("catchment_km")) or 3.0
    horizon_months = int(_num(project.get("time_horizon_months")) or 18)
    gross_margin = (_num(project.get("gross_margin_pct")) or DEFAULT_GROSS_MARGIN_PCT) / 100.0
    period = (project.get("revenue_period") or "monthly").lower()
    rev_to_monthly = (1.0 / 12.0) if period == "annual" else 1.0
    budget = float(budget)

    sorted_ppis = sorted(v for v in (_num(r.get("ppi_ml")) for r in rows_by_pincode.values()) if v is not None)

    def ppi_pct(row):
        p = _num(row.get("ppi_ml"))
        return bisect.bisect_right(sorted_ppis, p) / len(sorted_ppis) if (p is not None and sorted_ppis) else None

    # stores with a usable location + revenue
    stores = []
    for loc in locations:
        pc = loc.get("pincode")
        row = rows_by_pincode.get(pc) if pc else None
        rev = _num(loc.get("revenue"))
        lat, lng = _num(loc.get("lat")), _num(loc.get("lng"))
        if row is None or rev is None or rev <= 0:
            continue
        if lat is None or lng is None:
            lat, lng = _num(row.get("lat")), _num(row.get("lng"))
        if lat is None or lng is None:
            continue
        stores.append({"pincode": pc, "lat": lat, "lng": lng,
                       "monthly_revenue": rev * rev_to_monthly,
                       "capex": _num(loc.get("capex")), "row": row})

    hh_by_pincode = estimate_households(rows_by_pincode, geography, district_pop)
    index = build_spatial_index(rows_by_pincode, hh_by_pincode)

    top_drivers = drivers.get("drivers", []) if drivers.get("sufficient_data") else []
    fit_scorer = _driver_fit_scorer(top_drivers, rows_by_pincode)

    # calibrate capture on the customer's stores
    observations = []
    for s in stores:
        reach, _income, _ = catchment_reachable_spend(
            s["lat"], s["lng"], catchment_km, index,
            spend_hh=_num(s["row"].get("est_monthly_spend_hh")),
            income_hh=_num(s["row"].get("est_monthly_income_hh")))
        s["reach"] = reach
        observations.append({
            "capture": (s["monthly_revenue"] / reach) if reach > 0 else None,
            "ppi_pct": ppi_pct(s["row"]) if ppi_pct(s["row"]) is not None else 0.5,
            "driver_fit": fit_scorer(s["row"]) if fit_scorer else None,
        })
    # quality floor: don't recommend anywhere materially weaker (by PPI
    # percentile) than the customer's own weakest existing store — those are
    # almost always large rural catchments the calibrated capture over-credits.
    store_ppi_pcts = [o["ppi_pct"] for o in observations if o["capture"] and o["capture"] > 0]
    ppi_floor = 0.8 * min(store_ppi_pcts) if store_ppi_pcts else 0.0

    # < MIN_STORES usable stores, or the ones we have don't yield a usable capture
    # rate (all zero/missing reach) — fall back to the benchmark rate rather than
    # refusing to forecast; every project gets a real (labelled) result.
    model = fit_capture_model(observations) or benchmark_capture_model()

    owned_pincodes = {s["pincode"] for s in stores}
    owned_coords = [(s["lat"], s["lng"]) for s in stores]

    # candidate universe: pincodes near the target market (target_pincodes if
    # given, else the customer's current footprint), minus owned pincodes.
    anchors = []
    for tp in (project.get("target_pincodes") or []):
        r = rows_by_pincode.get(str(tp))
        if r and _num(r.get("lat")) is not None:
            anchors.append((_num(r.get("lat")), _num(r.get("lng"))))
    if not anchors:
        anchors = owned_coords

    if anchors:
        cand_pcs = set()
        for lat, lng in anchors:
            for pc, _plat, _plng, _u, _iu in _nearby(index, lat, lng, catchment_km + EXPANSION_MARGIN_KM):
                cand_pcs.add(pc)
    else:
        # no stores and no target_pincodes — nothing to anchor a "nearby" walk on.
        # Seed candidates directly from the top nationally-ranked pincodes by PPI
        # so a brand-new project with no data at all still gets a real forecast.
        cand_pcs = set(sorted(
            (pc for pc, r in rows_by_pincode.items()
             if _num(r.get("ppi_ml")) is not None and _num(r.get("lat")) is not None),
            key=lambda pc: _num(rows_by_pincode[pc].get("ppi_ml")),
            reverse=True,
        )[:NATIONAL_SEED_TOP_N])
    cand_pcs -= owned_pincodes
    cand_pcs = [pc for pc in cand_pcs if ppi_pct(rows_by_pincode[pc]) is not None]
    if len(cand_pcs) > MAX_CANDIDATES:
        cand_pcs.sort(key=lambda pc: _num(rows_by_pincode[pc].get("ppi_ml")) or 0, reverse=True)
        cand_pcs = cand_pcs[:MAX_CANDIDATES]

    # per-store capex: prefer the customer's own median, else sqft x local rate,
    # else (no store data at all) a default store size x local rate.
    own_capex = _median([s["capex"] for s in stores if s["capex"] and s["capex"] > 0])
    assumed_sqft = _estimate_store_sqft(locations, rows_by_pincode) if own_capex is None else None

    def capex_for(row):
        if own_capex is not None:
            return own_capex
        rate = _num(row.get("rate_per_sqft"))
        if not rate:
            return None
        return rate * (assumed_sqft if assumed_sqft is not None else DEFAULT_STORE_SQFT)

    capex_basis = ("your stores' median fit-out cost" if own_capex is not None
                   else "modelled: assumed store size x local property rate" if assumed_sqft is not None
                   else f"modelled: a default {DEFAULT_STORE_SQFT}-sqft store x local property rate "
                        "— upload your own stores' rent/fit-out cost for a sharper estimate")

    candidates = []
    for pc in cand_pcs:
        row = rows_by_pincode[pc]
        if (ppi_pct(row) or 0) < ppi_floor:
            continue
        lat, lng = _num(row.get("lat")), _num(row.get("lng"))
        reach, catch_income, _ = catchment_reachable_spend(
            lat, lng, catchment_km, index,
            spend_hh=_num(row.get("est_monthly_spend_hh")),
            income_hh=_num(row.get("est_monthly_income_hh")))
        if reach <= 0:
            continue
        cap = model.predict(ppi_pct(row) or 0.5, fit_scorer(row) if fit_scorer else None)
        capex = capex_for(row)
        rev_raw = cap * reach
        rev_ceiling = REVENUE_INCOME_CAP_MULT * catch_income if catch_income > 0 else rev_raw
        rev_gross = min(rev_raw, rev_ceiling)
        candidates.append({
            "pincode": pc, "name": row.get("name") or pc,
            "lat": lat, "lng": lng,
            "state": (geography.get(pc) or {}).get("state_name"),
            "reach_gross": reach,
            "catchment_income": catch_income,
            "capture_rate": cap,
            "revenue_gross": rev_gross,
            "revenue_clamped": rev_gross < rev_raw - 1,
            "capex": capex,
            "ppi_percentile": round((ppi_pct(row) or 0) * 100, 1),
        })

    if not candidates:
        return {
            "sufficient_data": False,
            "reason": "no_candidates",
            "detail": "No candidate pincodes with modelled household spend were found near your target market.",
        }

    # Drop candidates with no resolvable capex (missing rate_per_sqft for that
    # pincode) from the ranking pool rather than letting a handful of gaps in
    # a large candidate universe (e.g. the national seed, which spans ~500
    # pincodes and is never all uniformly covered) disable budget-constrained
    # pricing for every candidate. Only fall back to unpriced ranking when
    # NONE of them have a usable capex at all.
    priced_candidates = [c for c in candidates if c["capex"] is not None]
    priced = len(priced_candidates) > 0
    if priced:
        candidates = priced_candidates
    # rank by revenue-per-rupee when capex is known, else by raw revenue
    candidates.sort(key=lambda c: (c["revenue_gross"] / c["capex"]) if priced else c["revenue_gross"],
                    reverse=True)

    # The reach curve is drawn only over a budget-anchored window, not across every
    # candidate — a tiny budget used to produce a curve spanning ~Rs 6cr of CapEx
    # (14 arbitrary sites) with the budget line pinned at the far left and a
    # headline figure nobody could act on. Window = a bit past whichever is larger:
    # the budget, or the cost of the three cheapest sites (so even a sub-one-store
    # budget still shows the first few steps).
    three_cheapest = sorted(c["capex"] for c in candidates[:60] if c["capex"])[:3] if priced else []
    curve_ceiling = max(budget, sum(three_cheapest) if three_cheapest else budget) * 1.6

    # greedy walk with cannibalisation — build the curve over the window, then slice at budget
    selected_coords = list(owned_coords)
    cum_capex = 0.0
    cum_revenue = 0.0
    curve = [{"investment": 0.0, "monthly_revenue": 0.0, "stores": 0}]
    ranked = []
    for c in candidates:
        disc = _cannibalisation_discount(c["lat"], c["lng"], selected_coords, catchment_km)
        rev_eff = c["revenue_gross"] * disc
        entry = dict(c, cannibalisation_discount=round(disc, 3), monthly_revenue=rev_eff)
        ranked.append(entry)
        selected_coords.append((c["lat"], c["lng"]))
        cum_revenue += rev_eff
        if priced:
            cum_capex += c["capex"]
            if cum_capex <= curve_ceiling or len(curve) <= 4:
                curve.append({"investment": round(cum_capex), "monthly_revenue": round(cum_revenue),
                              "stores": len(ranked)})
        # stop once we have enough for the site list (25) AND the curve window is covered
        if len(ranked) >= 25 and (not priced or cum_capex > curve_ceiling):
            break
        if len(ranked) >= 60 or len(curve) > CURVE_STEPS + 3:
            break
        if not priced and len(ranked) >= min(len(candidates), 40):
            break

    # Pin an exact point at the budget x-position so the dashed budget line meets
    # the curve (linear interpolation between the bracketing steps).
    if priced and 0 < budget < curve[-1]["investment"]:
        for i in range(1, len(curve)):
            if curve[i - 1]["investment"] <= budget <= curve[i]["investment"]:
                lo, hi = curve[i - 1], curve[i]
                span = hi["investment"] - lo["investment"] or 1
                frac = (budget - lo["investment"]) / span
                if 0.02 < frac < 0.98:
                    curve.insert(i, {
                        "investment": round(budget),
                        "monthly_revenue": round(lo["monthly_revenue"]
                                                 + frac * (hi["monthly_revenue"] - lo["monthly_revenue"])),
                        "stores": lo["stores"], "at_budget": True,
                    })
                break

    # recommended portfolio = ranked prefix that fits the budget
    portfolio = []
    spent = 0.0
    if priced:
        for e in ranked:
            if spent + e["capex"] > budget:
                continue
            portfolio.append(e)
            spent += e["capex"]
    else:
        portfolio = ranked[:10]
    funded_pcs = {e["pincode"] for e in portfolio}

    portfolio_revenue = sum(e["monthly_revenue"] for e in portfolio)
    portfolio_gross_profit = portfolio_revenue * gross_margin

    # diminishing-returns zone on the curve
    dim_start = None
    if priced and len(curve) > 2:
        seg0 = ((curve[1]["monthly_revenue"] - curve[0]["monthly_revenue"])
                / max(curve[1]["investment"] - curve[0]["investment"], 1))
        for i in range(2, len(curve)):
            slope = ((curve[i]["monthly_revenue"] - curve[i - 1]["monthly_revenue"])
                     / max(curve[i]["investment"] - curve[i - 1]["investment"], 1))
            if seg0 > 0 and slope < DIMINISHING_RETURNS_RATIO * seg0:
                dim_start = curve[i - 1]["investment"]
                break

    # payback (linear ramp over RAMP_MONTHS): cumulative gross profit at month t
    # ~= monthly_gp x max(0, t - RAMP/2). Solve for t.
    def payback_months(capex, monthly_gp):
        if monthly_gp <= 0:
            return None
        return round(RAMP_MONTHS / 2 + capex / monthly_gp, 1)

    pf_payback = payback_months(spent, portfolio_gross_profit) if priced else None

    # market capture — denominator is total monthly household spend across the
    # addressable market (each pincode counted once, no catchment double-count).
    market_pcs = set(cand_pcs) | owned_pincodes
    total_market_spend = sum((hh_by_pincode.get(pc) or 0) * (_num(rows_by_pincode[pc].get("est_monthly_spend_hh")) or 0)
                             for pc in market_pcs if pc in rows_by_pincode)
    # capture is measured against the CATEGORY's slice of that spend, not the whole
    # wallet — so "segment market capture" is a number a category operator recognises.
    market_spend = total_market_spend * CATEGORY_WALLET_SHARE
    current_revenue = sum(s["monthly_revenue"] for s in stores)

    # recommended rupee split by state
    split = {}
    for e in portfolio:
        st = e["state"] or "Unknown"
        s = split.setdefault(st, {"state": st, "stores": 0, "investment": 0.0, "monthly_revenue": 0.0})
        s["stores"] += 1
        s["investment"] += e["capex"] or 0
        s["monthly_revenue"] += e["monthly_revenue"]
    split_list = sorted(split.values(), key=lambda s: s["monthly_revenue"], reverse=True)
    for s in split_list:
        s["investment"] = round(s["investment"])
        s["monthly_revenue"] = round(s["monthly_revenue"])
        s["investment_share_pct"] = round(s["investment"] / spent * 100, 1) if spent else None

    # lever-fit radar — do the project's chosen signals actually move revenue?
    # Computed directly here (Pearson of the signal vs. store revenue) for every
    # signal the project picked, not just the top-N _compute_drivers returns, so
    # the radar has a real value on every spoke.
    top_driver_signals = {d["signal"] for d in drivers.get("drivers", [])} if drivers.get("sufficient_data") else set()
    store_revs = [s["monthly_revenue"] for s in stores]
    radar_focus_pcs = [e["pincode"] for e in (portfolio or ranked[:5])]
    radar = []
    for sig in (project.get("signals") or ["ppi_ml"]):
        pairs = [(_num(s["row"].get(sig)), rev) for s, rev in zip(stores, store_revs)]
        pairs = [(x, y) for x, y in pairs if x is not None]
        r = _pearson([x for x, _ in pairs], [y for _, y in pairs]) if len(pairs) >= 3 else None
        fit = round(abs(r) * 100, 1) if r is not None else None
        # site-avg national percentile for this signal across the top sites — so
        # the "does each signal move revenue?" card is never empty (benchmark tier
        # has no Pearson fit, but every site still has a percentile on every signal).
        svals = sorted(v for v in (_num(x.get(sig)) for x in rows_by_pincode.values()) if v is not None)
        sap = None
        if svals:
            ps = [bisect.bisect_right(svals, _num(rows_by_pincode[pc].get(sig))) / len(svals) * 100
                  for pc in radar_focus_pcs
                  if pc in rows_by_pincode and _num(rows_by_pincode[pc].get(sig)) is not None]
            sap = round(sum(ps) / len(ps), 1) if ps else None
        verdict = ("moves revenue" if (fit is not None and fit >= 50)
                   else "weak link" if fit is not None
                   else "unproven — upload stores")
        radar.append({
            "signal": sig,
            "label": _signals_data.SIGNAL_LABELS.get(sig, sig),
            "lever_fit": fit,
            "direction": (None if r is None else "positive" if r >= 0 else "negative"),
            "sample_size": len(pairs),
            "in_top_drivers": sig in top_driver_signals,
            "site_avg_percentile": sap,
            "verdict": verdict,
        })

    horizon_gross_profit = portfolio_gross_profit * max(0.0, horizon_months - RAMP_MONTHS / 2)

    # "The nudge" — a concrete marginal-reallocation suggestion: swap the
    # weakest-by-revenue-per-rupee FUNDED site for the strongest UNFUNDED one
    # your budget didn't reach. Both lists are already ranked best-first, so
    # this reuses that ordering rather than a second optimisation pass.
    # Capex-priced only — "move ₹X" is meaningless without a capex figure.
    nudge = None
    if priced and portfolio and len(ranked) > len(portfolio):
        weakest_funded = portfolio[-1]
        best_unfunded = next((e for e in ranked if e["pincode"] not in funded_pcs), None)
        if best_unfunded and best_unfunded["pincode"] != weakest_funded["pincode"]:
            revenue_delta = best_unfunded["monthly_revenue"] - weakest_funded["monthly_revenue"]
            if revenue_delta > 0:
                old_payback = payback_months(weakest_funded["capex"], weakest_funded["monthly_revenue"] * gross_margin)
                new_payback = payback_months(best_unfunded["capex"], best_unfunded["monthly_revenue"] * gross_margin)
                nudge = {
                    "from_pincode": weakest_funded["pincode"], "from_name": weakest_funded["name"],
                    "to_pincode": best_unfunded["pincode"], "to_name": best_unfunded["name"],
                    "from_capex": round(weakest_funded["capex"]) if weakest_funded["capex"] else None,
                    "to_capex": round(best_unfunded["capex"]) if best_unfunded["capex"] else None,
                    "monthly_revenue_delta": round(revenue_delta),
                    "old_payback_months": old_payback,
                    "new_payback_months": new_payback,
                    "note": (f"{weakest_funded['name']} is your weakest funded site by revenue-per-rupee; "
                             f"{best_unfunded['name']} is the strongest site your budget didn't reach."),
                }

    # Per-candidate lever percentiles for the top 3 recommended sites — lets
    # the Forecast page overlay each candidate's OWN signal percentiles on
    # the same radar as the historical (Pearson-fit) polygon, instead of
    # only ever showing how well the project's signals fit past stores.
    # National percentile, not relative to whatever's nearby, so it's
    # comparable across candidates the same way ppi_pct() already is.
    lever_sig_ids = project.get("signals") or ["ppi_ml"]
    lever_sorted_vals = {}
    for sig in lever_sig_ids:
        vals = sorted(v for v in (_num(r.get(sig)) for r in rows_by_pincode.values()) if v is not None)
        if vals:
            lever_sorted_vals[sig] = vals

    def lever_percentiles_for(pincode):
        row = rows_by_pincode.get(pincode)
        if not row:
            return []
        out = []
        for sig in lever_sig_ids:
            vals = lever_sorted_vals.get(sig)
            v = _num(row.get(sig))
            pct = round(bisect.bisect_right(vals, v) / len(vals) * 100, 1) if (vals and v is not None) else None
            out.append({"signal": sig, "label": _signals_data.SIGNAL_LABELS.get(sig, sig), "percentile": pct})
        return out

    # budget-split lever radar and per-site SWOT — both computed off the same
    # ranked/portfolio data above, so they recalibrate automatically whenever
    # the budget changes and the portfolio it produces changes with it.
    swot_pct = _swot_percentile_lookup(rows_by_pincode)
    avg_ticket = _num(project.get("avg_ticket"))
    lever_split = compute_lever_split(budget, rows_by_pincode, [e["pincode"] for e in (portfolio or ranked[:10])])

    # your stores' own mean profile across the operating factors — the second
    # baseline for the per-location factor deltas (only when there's store data).
    store_factor_means = None
    if stores:
        acc = {}
        for key, _label, cols, _basis in FACTOR_DEFS:
            svals = [_factor_score(swot_pct, s["row"], cols) for s in stores]
            svals = [v for v in svals if v is not None]
            acc[key] = round(sum(svals) / len(svals), 1) if svals else None
        store_factor_means = acc

    focus = portfolio or ranked[:5]

    def swot_for(e):
        row = rows_by_pincode.get(e["pincode"])
        if not row:
            return None
        opp_score, _ = _opportunity_score(e["ppi_percentile"], avg_ticket, _num(row.get("est_monthly_spend_hh")))
        return location_swot(row, swot_pct, opp_score, e["cannibalisation_discount"], diagnostics, e["pincode"])

    def factors_for(e):
        row = rows_by_pincode.get(e["pincode"])
        return location_factors(swot_pct, row, store_factor_means) if row else None

    # "Smallest viable step" — the cheapest single ranked site. Drives the KPI
    # strip and the investment-split card when the budget funds nothing yet.
    cheapest = min((e for e in ranked if e.get("capex")), key=lambda e: e["capex"], default=None)
    smallest_viable = None
    if cheapest:
        sv_gp = cheapest["monthly_revenue"] * gross_margin
        smallest_viable = {
            "name": cheapest["name"], "pincode": cheapest["pincode"], "state": cheapest["state"],
            "investment": round(cheapest["capex"]), "stores": 1,
            "monthly_revenue": round(cheapest["monthly_revenue"]),
            "monthly_gross_profit": round(sv_gp),
            "payback_months": payback_months(cheapest["capex"], sv_gp),
        }

    # "What's moving this curve" — a handful of signed drivers behind the shape,
    # all from figures already computed above.
    cand_reach_med = _median([c["reach_gross"] for c in candidates]) or 0.0
    focus_reach = _median([e["reach_gross"] for e in focus]) or 0.0
    focus_ppi = _median([e["ppi_percentile"] for e in focus]) or 0.0
    min_disc = min((e["cannibalisation_discount"] for e in focus), default=1.0)
    focus_hh = _median([hh_by_pincode.get(e["pincode"]) or 0 for e in focus]) or 0.0
    curve_drivers = [
        {"key": "reachable_spend", "label": "Reachable spend per site",
         "value": round(focus_reach), "unit": "currency",
         "delta_pct": round((focus_reach / cand_reach_med - 1) * 100) if cand_reach_med else None,
         "direction": "up" if focus_reach >= cand_reach_med else "down",
         "basis": "Modelled monthly household spend inside the catchment — top sites vs. the candidate median."},
        {"key": "wallet_share", "label": "Category wallet share",
         "value": round(CATEGORY_WALLET_SHARE * 100, 1), "unit": "percent",
         "delta_pct": None, "direction": "flat",
         "basis": "Assumed share of the total household wallet this retail category commands."},
        {"key": "entrant_capture", "label": "New-entrant capture",
         "value": round(ENTRANT_CAPTURE * 100, 2), "unit": "percent",
         "delta_pct": None, "direction": "flat",
         "basis": "Assumed share of that category spend a new entrant wins against incumbents."},
        {"key": "capture_rate", "label": f"Effective capture ({model.method})",
         "value": round(model.median_capture * 100, 3), "unit": "percent",
         "delta_pct": None, "direction": "flat",
         "basis": "Wallet share x entrant capture (benchmark), or your stores' fitted rate."},
        {"key": "ppi", "label": "Purchasing-power percentile of top sites",
         "value": round(focus_ppi, 1), "unit": "index",
         "delta_pct": round(focus_ppi - round(ppi_floor * 100, 1)) if focus_ppi else None,
         "direction": "up" if focus_ppi >= round(ppi_floor * 100, 1) else "down",
         "basis": "Mean PPI percentile of the funded/top sites vs. the quality floor (0.8x your weakest store)."},
        {"key": "cannibalisation", "label": "Catchment-overlap drag",
         "value": round((1 - min_disc) * 100, 1), "unit": "percent",
         "delta_pct": None, "direction": "down" if min_disc < 0.99 else "flat",
         "basis": "Reachable spend ceded where a site's catchment overlaps another site in the portfolio."},
        {"key": "households", "label": "Households in catchment (per site)",
         "value": round(focus_hh), "unit": "count",
         "delta_pct": None, "direction": "flat",
         "basis": "Modelled households within the catchment radius of the top sites."},
    ]

    return {
        "sufficient_data": True,
        "calibration": model.method,
        "budget": round(budget),
        "catchment_km": catchment_km,
        "time_horizon_months": horizon_months,
        "gross_margin_pct": round(gross_margin * 100, 1),
        "revenue_period": period,
        "capex_priced": priced,
        "capex_basis": capex_basis,
        "capture_model": model.as_dict(),
        "confidence": ("benchmark" if model.method == "benchmark"
                       else "high" if model.method == "regression" and (model.r2 or 0) >= 0.5 and model.n_used >= REGRESSION_MIN_STORES
                       else "medium" if model.n_used >= 5 else "low"),
        "candidates_considered": len(candidates),
        "quality_floor_ppi_percentile": round(ppi_floor * 100, 1),

        "reach_curve": {
            "points": curve if priced else [],
            "diminishing_returns_from": dim_start,
            "smallest_viable": smallest_viable,
            "drivers": curve_drivers,
            "note": ("Cumulative modelled monthly revenue as investment grows, best sites first, "
                     "with catchment-overlap cannibalisation applied. Drawn over a budget-anchored "
                     "window, not the full candidate set."),
        },

        "recommended_portfolio": {
            "stores": len(portfolio),
            "investment": round(spent) if priced else None,
            "monthly_revenue": round(portfolio_revenue),
            "monthly_gross_profit": round(portfolio_gross_profit),
            "horizon_gross_profit": round(horizon_gross_profit),
            "payback_months": pf_payback,
            "within_horizon": (pf_payback is not None and pf_payback <= horizon_months),
            # Ranked best-first, NOT filtered to what the budget actually funds — a
            # tiny budget (e.g. a few thousand rupees, well under any site's fit-out
            # cost) would otherwise wipe out "which locations yield well" entirely,
            # even though that ranking has nothing to do with whether you can afford
            # to open there yet. `within_budget` says whether this site is part of
            # `investment`/`monthly_revenue` above; the rest are informational.
            "sites": [
                {
                    "pincode": e["pincode"], "name": e["name"], "state": e["state"],
                    "lat": e["lat"], "lng": e["lng"],
                    "monthly_revenue": round(e["monthly_revenue"]),
                    "reach_gross": round(e["reach_gross"]),
                    "revenue_clamped": bool(e.get("revenue_clamped")),
                    "capex": round(e["capex"]) if e["capex"] else None,
                    "capture_rate": round(e["capture_rate"], 5),
                    "ppi_percentile": e["ppi_percentile"],
                    # hotspot-chart filter fields — cheap, all 25 sites carry them
                    "income_percentile": swot_pct(rows_by_pincode.get(e["pincode"]) or {}, "est_monthly_income_hh"),
                    "footfall_percentile": swot_pct(rows_by_pincode.get(e["pincode"]) or {}, "premium_poi_per_km2"),
                    "households": round(hh_by_pincode.get(e["pincode"]) or 0),
                    "cannibalisation_discount": e["cannibalisation_discount"],
                    "payback_months": payback_months(e["capex"], e["monthly_revenue"] * gross_margin) if e["capex"] else None,
                    "within_budget": e["pincode"] in funded_pcs,
                    # Only the top 5 carry these — a comparative radar / factor
                    # table over every recommended site would be unreadable, and
                    # the rest of the site list never needs them.
                    "lever_percentiles": lever_percentiles_for(e["pincode"]) if i < 5 else None,
                    "factors": factors_for(e) if i < 5 else None,
                    "swot": swot_for(e) if i < 5 else None,
                }
                for i, e in enumerate(ranked[:25])
            ],
        },

        "nudge": nudge,

        "lever_split": lever_split,

        "investment_split": split_list,

        "market_capture": {
            "addressable_monthly_spend": round(market_spend),
            "total_household_spend": round(total_market_spend),
            "current_monthly_revenue": round(current_revenue),
            "current_capture_pct": round(current_revenue / market_spend * 100, 2) if market_spend else None,
            "projected_monthly_revenue": round(current_revenue + portfolio_revenue),
            "projected_capture_pct": round((current_revenue + portfolio_revenue) / market_spend * 100, 2) if market_spend else None,
            "note": (f"Share of the category's slice ({CATEGORY_WALLET_SHARE * 100:.0f}% of total household "
                     "spend) across the target-market pincodes."),
        },

        "lever_fit_radar": radar,
        "drivers": drivers,
        "assumptions": ASSUMPTIONS,
    }
