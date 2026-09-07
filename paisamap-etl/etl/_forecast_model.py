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
Instead the model is fully **cross-sectional**, calibrated on the customer's own
stores:

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

  3. Capture rate, calibrated on the customer's own stores
       capture_i = monthly_revenue_i / catchment_reachable_spend_i
     regressed (pure-Python OLS, <=2 predictors) on the store's PPI percentile
     and its driver-fit score. N < 8 stores, or degenerate predictors -> falls
     back to the pooled median capture. N < MIN_STORES -> the whole endpoint
     returns sufficient_data:false and charges nothing.

  4. Diminishing returns — two mechanisms, both real:
       (a) the portfolio is filled greedily by predicted-revenue-per-rupee, so
           each additional rupee buys a less productive site (the curve bends);
       (b) a new store inside an existing/selected store's catchment has its
           reachable spend discounted for the overlap (cannibalisation).

  5. Outputs: the reach curve, the recommended rupee split by state, payback
     (needs the project's gross_margin_pct + revenue_period), market-capture %,
     and a lever-fit radar (do the project's chosen signals actually move this
     customer's revenue?).

Every constant is in ASSUMPTIONS and echoed in the response. Nothing here is
presented as more precise than "a modelled projection".
"""

import bisect
import math
import csv

import _signals_data

MIN_STORES = 3              # below this: sufficient_data:false, no charge
REGRESSION_MIN_STORES = 8   # below this: pooled-median capture, not a fit
REGRESSION_MIN_R2 = 0.15    # a fit weaker than this is no better than the pooled median
AVG_HOUSEHOLD_SIZE = 4.6    # Census of India 2011 national average household size
DEFAULT_GROSS_MARGIN_PCT = 35.0   # blended retail gross margin, used only if the project has none
RAMP_MONTHS = 12           # a new store is assumed to reach modelled steady-state revenue linearly over this many months
CURVE_STEPS = 14
DIMINISHING_RETURNS_RATIO = 0.5   # marginal revenue/rupee below this fraction of the first segment's => "diminishing" zone
EXPANSION_MARGIN_KM = 25.0       # how far beyond the catchment radius to look for candidate pincodes
MAX_CANDIDATES = 3000            # hard cap on the candidate universe (keeps the request O(seconds))
DENSITY_PROXY_COLS = ("radiance_mean", "premium_poi_per_km2", "fin_density_per_km2")
CANNIBALISATION_SHARE = 0.5      # fraction of an overlap that a new store cedes to the incumbent

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
        f"fewer -> your stores' median capture."
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
    """cell -> list of (pincode, lat, lng, reachable_unit) where reachable_unit
    is that pincode's own monthly household spend (households x spend_hh)."""
    index = {}
    for pc, row in rows_by_pincode.items():
        lat, lng = _num(row.get("lat")), _num(row.get("lng"))
        hh = hh_by_pincode.get(pc)
        spend = _num(row.get("est_monthly_spend_hh"))
        if lat is None or lng is None or hh is None or spend is None:
            continue
        index.setdefault(_cell(lat, lng), []).append((pc, lat, lng, hh * spend))
    return index


def _nearby(index, lat, lng, radius_km):
    span = int(math.ceil(radius_km / 111.0 / _CELL_DEG)) + 1
    c0 = _cell(lat, lng)
    for di in range(-span, span + 1):
        for dj in range(-span, span + 1):
            for entry in index.get((c0[0] + di, c0[1] + dj), ()):
                yield entry


def catchment_reachable_spend(lat, lng, catchment_km, index):
    """Sum of reachable_unit x linear distance-decay over pincodes within the
    catchment. Returns (total_spend, [pincodes touched])."""
    total = 0.0
    touched = []
    for pc, plat, plng, unit in _nearby(index, lat, lng, catchment_km):
        d = _signals_data.haversine_km(lat, lng, plat, plng)
        if d <= catchment_km:
            total += unit * max(0.0, 1.0 - d / catchment_km)
            touched.append(pc)
    return total, touched


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

    if len(stores) < MIN_STORES:
        return {
            "sufficient_data": False,
            "reason": "not_enough_stores",
            "detail": (f"The forecast model calibrates on your own stores — it needs at least "
                       f"{MIN_STORES} with a resolved pincode and revenue. This project has {len(stores)}."),
            "stores_usable": len(stores),
            "min_stores_required": MIN_STORES,
        }

    hh_by_pincode = estimate_households(rows_by_pincode, geography, district_pop)
    index = build_spatial_index(rows_by_pincode, hh_by_pincode)

    top_drivers = drivers.get("drivers", []) if drivers.get("sufficient_data") else []
    fit_scorer = _driver_fit_scorer(top_drivers, rows_by_pincode)

    # calibrate capture on the customer's stores
    observations = []
    for s in stores:
        reach, _ = catchment_reachable_spend(s["lat"], s["lng"], catchment_km, index)
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

    model = fit_capture_model(observations)
    if model is None:
        return {
            "sufficient_data": False,
            "reason": "capture_uncalibratable",
            "detail": ("Your stores' revenue and their modelled catchment spend don't yield a usable "
                       "capture rate (all zero or missing). Check that store revenue imported correctly."),
            "stores_usable": len(stores),
        }

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

    cand_pcs = set()
    for lat, lng in anchors:
        for pc, _plat, _plng, _u in _nearby(index, lat, lng, catchment_km + EXPANSION_MARGIN_KM):
            cand_pcs.add(pc)
    cand_pcs -= owned_pincodes
    cand_pcs = [pc for pc in cand_pcs if ppi_pct(rows_by_pincode[pc]) is not None]
    if len(cand_pcs) > MAX_CANDIDATES:
        cand_pcs.sort(key=lambda pc: _num(rows_by_pincode[pc].get("ppi_ml")) or 0, reverse=True)
        cand_pcs = cand_pcs[:MAX_CANDIDATES]

    # per-store capex: prefer the customer's own median, else sqft x local rate
    own_capex = _median([s["capex"] for s in stores if s["capex"] and s["capex"] > 0])
    assumed_sqft = _estimate_store_sqft(locations, rows_by_pincode) if own_capex is None else None

    def capex_for(row):
        if own_capex is not None:
            return own_capex
        if assumed_sqft is not None:
            rate = _num(row.get("rate_per_sqft"))
            if rate:
                return rate * assumed_sqft
        return None

    capex_basis = ("your stores' median fit-out cost" if own_capex is not None
                   else ("modelled: assumed store size x local property rate" if assumed_sqft is not None
                         else None))

    candidates = []
    for pc in cand_pcs:
        row = rows_by_pincode[pc]
        if (ppi_pct(row) or 0) < ppi_floor:
            continue
        lat, lng = _num(row.get("lat")), _num(row.get("lng"))
        reach, _ = catchment_reachable_spend(lat, lng, catchment_km, index)
        if reach <= 0:
            continue
        cap = model.predict(ppi_pct(row) or 0.5, fit_scorer(row) if fit_scorer else None)
        capex = capex_for(row)
        candidates.append({
            "pincode": pc, "name": row.get("name") or pc,
            "lat": lat, "lng": lng,
            "state": (geography.get(pc) or {}).get("state_name"),
            "reach_gross": reach,
            "capture_rate": cap,
            "revenue_gross": cap * reach,
            "capex": capex,
            "ppi_percentile": round((ppi_pct(row) or 0) * 100, 1),
        })

    if not candidates:
        return {
            "sufficient_data": False,
            "reason": "no_candidates",
            "detail": "No candidate pincodes with modelled household spend were found near your target market.",
        }

    priced = all(c["capex"] for c in candidates)
    # rank by revenue-per-rupee when capex is known, else by raw revenue
    candidates.sort(key=lambda c: (c["revenue_gross"] / c["capex"]) if priced else c["revenue_gross"],
                    reverse=True)

    # greedy walk with cannibalisation — build the full curve, then slice at budget
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
            curve.append({"investment": round(cum_capex), "monthly_revenue": round(cum_revenue),
                          "stores": len(ranked)})
        # cap the curve length
        if priced and cum_capex > max(budget * 3, budget + 1) and len(curve) > CURVE_STEPS:
            break
        if not priced and len(ranked) >= min(len(candidates), 40):
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
    market_spend = sum((hh_by_pincode.get(pc) or 0) * (_num(rows_by_pincode[pc].get("est_monthly_spend_hh")) or 0)
                       for pc in market_pcs if pc in rows_by_pincode)
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
    radar = []
    for sig in (project.get("signals") or ["ppi_ml"]):
        pairs = [(_num(s["row"].get(sig)), rev) for s, rev in zip(stores, store_revs)]
        pairs = [(x, y) for x, y in pairs if x is not None]
        r = _pearson([x for x, _ in pairs], [y for _, y in pairs]) if len(pairs) >= 3 else None
        radar.append({
            "signal": sig,
            "label": _signals_data.SIGNAL_LABELS.get(sig, sig),
            "lever_fit": round(abs(r) * 100, 1) if r is not None else None,
            "direction": (None if r is None else "positive" if r >= 0 else "negative"),
            "sample_size": len(pairs),
            "in_top_drivers": sig in top_driver_signals,
        })

    horizon_gross_profit = portfolio_gross_profit * max(0.0, horizon_months - RAMP_MONTHS / 2)

    return {
        "sufficient_data": True,
        "budget": round(budget),
        "catchment_km": catchment_km,
        "time_horizon_months": horizon_months,
        "gross_margin_pct": round(gross_margin * 100, 1),
        "revenue_period": period,
        "capex_priced": priced,
        "capex_basis": capex_basis,
        "capture_model": model.as_dict(),
        "confidence": ("high" if model.method == "regression" and (model.r2 or 0) >= 0.5 and model.n_used >= REGRESSION_MIN_STORES
                       else "medium" if model.n_used >= 5 else "low"),
        "candidates_considered": len(candidates),
        "quality_floor_ppi_percentile": round(ppi_floor * 100, 1),

        "reach_curve": {
            "points": curve if priced else [],
            "diminishing_returns_from": dim_start,
            "note": ("Cumulative modelled monthly revenue as investment grows, best sites first, "
                     "with catchment-overlap cannibalisation applied."),
        },

        "recommended_portfolio": {
            "stores": len(portfolio),
            "investment": round(spent) if priced else None,
            "monthly_revenue": round(portfolio_revenue),
            "monthly_gross_profit": round(portfolio_gross_profit),
            "horizon_gross_profit": round(horizon_gross_profit),
            "payback_months": pf_payback,
            "within_horizon": (pf_payback is not None and pf_payback <= horizon_months),
            "sites": [
                {
                    "pincode": e["pincode"], "name": e["name"], "state": e["state"],
                    "monthly_revenue": round(e["monthly_revenue"]),
                    "capex": round(e["capex"]) if e["capex"] else None,
                    "capture_rate": round(e["capture_rate"], 5),
                    "ppi_percentile": e["ppi_percentile"],
                    "cannibalisation_discount": e["cannibalisation_discount"],
                    "payback_months": payback_months(e["capex"], e["monthly_revenue"] * gross_margin) if e["capex"] else None,
                }
                for e in portfolio[:25]
            ],
        },

        "investment_split": split_list,

        "market_capture": {
            "addressable_monthly_spend": round(market_spend),
            "current_monthly_revenue": round(current_revenue),
            "current_capture_pct": round(current_revenue / market_spend * 100, 3) if market_spend else None,
            "projected_monthly_revenue": round(current_revenue + portfolio_revenue),
            "projected_capture_pct": round((current_revenue + portfolio_revenue) / market_spend * 100, 3) if market_spend else None,
            "note": "Share of ALL modelled household spend in the target market — not category-specific.",
        },

        "lever_fit_radar": radar,
        "drivers": drivers,
        "assumptions": ASSUMPTIONS,
    }
