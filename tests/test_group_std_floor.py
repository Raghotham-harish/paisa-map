"""
test_group_std_floor.py — the Ballygunge knife-edge (2026-10-13): estimate_income re-standardised
each HCES group by its own std, and 350 of 503 groups had a std below 0.01 (their pincodes share
district-level inputs). Kolkata's 0.0043 turned a 0.04 gap from the group mean into z ≈ -10, so
Ballygunge sat on a PPI clamp (40 or 200) and flipped between them on tiny input changes.

    python3 tests/test_group_std_floor.py

Plain script, no DATABASE_URL, no data files: drives estimate_income with synthetic z values.
"""
import os
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ETL = os.path.join(REPO, "paisamap-etl", "etl")
sys.path.insert(0, ETL)
os.chdir(ETL)
import ml_refinement as ml  # noqa: E402

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


def ppi(z, pcs, group_key):
    return ml.estimate_income(np.asarray(z, dtype=float), pcs, group_key=group_key)["ppi_ml"]


# ── A spread-less group (the Kolkata shape) never reaches a clamp ───────────
pcs = [f"7000{i:02d}" for i in range(80)]
gk = {pc: "WEST BENGAL|KOLKATA" for pc in pcs}
rng = np.random.default_rng(0)
z = -0.19 + rng.normal(0, 0.0043, len(pcs))
z[19] = z.mean() - 0.04                          # Ballygunge: just below the mean
out = ppi(z, pcs, gk)
check(out.between(41, 199).all(), f"tight group stays off the clamps (got {out.min()}..{out.max()})")
check(abs(int(out.iloc[19]) - 100) <= 10, f"Ballygunge-shaped pin scores near its group (got {out.iloc[19]})")

# ── ... and a tiny input change no longer flips it ──────────────────────────
z2 = z.copy()
z2[19] = z.mean() + 0.04                         # the next refit's nudge: just above the mean
out2 = ppi(z2, pcs, gk)
check(abs(int(out2.iloc[19]) - int(out.iloc[19])) <= 2, f"±0.04 nudge moves it ≤2pt (was {out.iloc[19]} -> {out2.iloc[19]})")
check((ppi(z + rng.normal(0, 0.001, len(z)), pcs, gk) - out).abs().max() <= 1, "0.001 noise moves no pin >1pt")

# ── A genuinely spread group keeps its within-group standardisation ─────────
pcs_w = [f"5600{i:02d}" for i in range(60)]
gk_w = {pc: "KARNATAKA|BANGALORE URBAN" for pc in pcs_w}
z_w = rng.normal(0, 2.5, len(pcs_w))             # std > 1: floor must not bind
z_w[0] = 6.0
out_w = ppi(z_w, pcs_w, gk_w)
alpha = 60 / (60 + 50)
expect = alpha * (6.0 - z_w.mean()) / z_w.std() + (1 - alpha) * 6.0
check(int(out_w.iloc[0]) == int(np.clip(100 + 30 * expect, 40, 200).round()),
      "wide group: floor does not change the standardised score")
check(int(out_w.iloc[0]) > 150, "a real standout in a wide group still scores high")

print(f"test_group_std_floor: {passed} checks passed")
