"""
Core math. Three steps, in order:

    1. RECONSTRUCT   Ihat_h = sum_f(G_fh * e_f) / sum_f(G_fh)      shape right, level wrong
    2. CALIBRATE     alpha  = I_egrid_annual / genwmean(Ihat)      one scalar per BA
                     I_h    = alpha * Ihat_h                        shape and level right
    3. WEIGHT        Ibar   = sum_h(w_h * I_h)                     w_h unitless, sums to 1

Then E = annual_kwh * Ibar.

Why calibrate: fuel factors are national fleet averages. CAISO's gas fleet is newer and more
CCGT-heavy than the national average, so applying 400 g/kWh to CISO overstates every hour by
roughly the same proportion - level wrong, shape fine. eGRID measured that BA's actual annual
emissions from actual plants, so alpha absorbs the fuel-factor error, the OTH bucket
approximation, and the import assumption all at once, and guarantees your annual total
reconciles to the number EPA publishes.
"""

from __future__ import annotations

import numpy as np

# CARB default for unspecified market purchases: 0.428 tCO2e/MWh = 428 g/kWh.
IMPORT_G_PER_KWH = 428.0

# alpha outside this range means the BA->subregion mapping or the fuel taxonomy join is
# structurally wrong, not just imprecise. Free integration test - see calibrate().
ALPHA_SANITY = (0.80, 1.25)


def reconstruct(gen_mwh: np.ndarray, fuel_factors_g: np.ndarray,
                imports_mwh: np.ndarray | None = None) -> np.ndarray:
    """
    Ihat_h in kg/kWh. gen_mwh is (n_hours, n_fuels); fuel_factors_g is (n_fuels,) in g/kWh.

    Imports are load, not in-BA generation, so they enter both numerator and denominator at
    IMPORT_G_PER_KWH. Dropping them biases CAISO low by a lot - imports run 25-30% of load
    and are dirtier than in-state generation. Negative net interchange (net export) is
    clipped to zero: exported power is someone else's consumption.
    """
    gen = np.clip(np.nan_to_num(gen_mwh), 0, None)
    num = gen @ fuel_factors_g
    den = gen.sum(axis=1)
    if imports_mwh is not None:
        imp = np.clip(np.nan_to_num(imports_mwh), 0, None)
        num = num + imp * IMPORT_G_PER_KWH
        den = den + imp
    with np.errstate(invalid="ignore", divide="ignore"):
        ihat = np.where(den > 0, num / den, np.nan)
    return ihat / 1000.0  # g/kWh -> kg/kWh


def calibrate(ihat: np.ndarray, gen_total_mwh: np.ndarray,
              egrid_annual_kg_per_kwh: float) -> tuple[float, list[str]]:
    """
    alpha pins the generation-weighted annual mean of the reconstruction to eGRID's published
    annual rate for the same balancing authority.

    Generation-weighted, not a simple mean, because that is how eGRID's own rate is computed -
    total emissions over total generation. A simple mean would overweight low-output hours and
    give you a systematically different number for no reason.
    """
    warn = []
    m = np.isfinite(ihat) & np.isfinite(gen_total_mwh) & (gen_total_mwh > 0)
    if m.sum() < 0.5 * len(ihat):
        warn.append(f"Only {m.sum()}/{len(ihat)} usable hours; calibration is thin.")
    baseline = np.average(ihat[m], weights=gen_total_mwh[m])
    alpha = egrid_annual_kg_per_kwh / baseline if baseline > 0 else 1.0
    if not (ALPHA_SANITY[0] <= alpha <= ALPHA_SANITY[1]):
        warn.append(
            f"alpha={alpha:.3f} outside {ALPHA_SANITY}. This is a structural problem - "
            "likely a BA/subregion mismatch, a fuel taxonomy misjoin, or the wrong eGRID "
            "vintage. Do not ship this factor."
        )
    return float(alpha), warn


def normalize_shape(load: np.ndarray, month_index: np.ndarray | None = None) -> np.ndarray:
    """
    w_h, unitless, sums to 1. If month_index is given, normalizes WITHIN each month so
    seasonal load variation does not leak into the diurnal weighting - it is carried
    separately by the monthly aggregation instead.
    """
    load = np.clip(np.nan_to_num(load), 0, None)
    if month_index is None:
        s = load.sum()
        return load / s if s > 0 else np.full_like(load, 1.0 / len(load))
    w = np.zeros_like(load, dtype=float)
    for m in np.unique(month_index):
        sel = month_index == m
        s = load[sel].sum()
        w[sel] = load[sel] / s if s > 0 else 1.0 / sel.sum()
    return w


def load_weighted_mean(intensity: np.ndarray, weights: np.ndarray) -> float:
    """Ibar = sum(w_h * I_h) / sum(w_h). Denominator handles NaN-dropped hours."""
    m = np.isfinite(intensity) & np.isfinite(weights)
    if not m.any():
        return float("nan")
    return float(np.sum(intensity[m] * weights[m]) / np.sum(weights[m]))


def covariance_uplift(intensity: np.ndarray, load: np.ndarray) -> dict:
    """
    The decomposition, for display and for sanity-checking the result:

        Ibar_load / Ibar_flat = 1 + rho * CV_L * CV_I

    If the uplift the pipeline produces disagrees with this identity, something upstream is
    inconsistent. Expect rho ~ 0.3-0.5 and an uplift of a few percent for a residential shape
    on a normal grid - not the 15-25% I guessed before working the algebra.
    """
    m = np.isfinite(intensity) & np.isfinite(load)
    I, L = intensity[m], load[m]
    cv_i = I.std() / I.mean() if I.mean() else 0.0
    cv_l = L.std() / L.mean() if L.mean() else 0.0
    rho = float(np.corrcoef(I, L)[0, 1]) if len(I) > 2 else 0.0
    return {"rho": rho, "cv_intensity": float(cv_i), "cv_load": float(cv_l),
            "predicted_uplift": float(rho * cv_i * cv_l)}
