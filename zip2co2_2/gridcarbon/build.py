"""
Build step. Per BA: reconstruct -> calibrate -> weight, then persist.

Outputs to dist/:
    profile_{BA}.csv.gz   8760 rows: timestamp, I_h (kg/kWh), w_h (unitless). For UI plotting.
    summary.csv           one row per BA: annual and monthly factors, alpha, diagnostics.

Run once after a data refresh. Runtime then never touches this code.
"""

from __future__ import annotations

import csv
import gzip
import json
import os

import numpy as np
import pandas as pd

from . import core
from .sources import DATA, read_cache

DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")


def _fuel_factors(include_upstream=True, gas_leakage=1.0):
    """
    Per-fuel g CO2e/kWh. Combustion always; upstream optionally.

    Note the interaction with alpha: adding upstream raises the reconstruction's mean, so
    alpha shrinks to compensate and the ANNUAL total barely moves. That is correct - eGRID is
    stack-only, so if you want upstream in your totals you must add it AFTER calibration, not
    inside it. This function feeds the shape; upstream_kg_per_kwh in the summary is the adder
    applied on top. Keeping them separate is what stops the double-count.
    """
    out = {}
    with open(os.path.join(DATA, "fuel_factors.csv")) as f:
        rows = [ln for ln in f if not ln.lstrip().startswith("#")]
    for r in csv.DictReader(rows):
        comb = float(r["combustion_g_co2e_per_kwh"] or 0)
        up = float(r["g_co2e_per_kwh"] or 0)
        if r["fuel"] == "natural_gas":
            up *= gas_leakage
        out[r["fuel"]] = (comb, up)
    return out


def _read_table(name):
    with open(os.path.join(DATA, name)) as f:
        rows = [ln for ln in f if not ln.lstrip().startswith("#")]
    return list(csv.DictReader(rows))


def build_ba(ba: str, year: int, egrid_kg: float, gas_leakage=1.0, use_imports=False):
    """
    DEVIATION from the original import treatment (documented in core.reconstruct):
    use_imports defaults to FALSE — the shipped intensity is PRODUCTION-based
    (in-BA generation only), matching how eGRID location-based rates are defined
    and used everywhere (GHG Protocol location-based = eGRID). Two reasons:

    1. The calibration anchor (eGRID BA rate) is production-based. Reconstructing
       WITH imports at CARB's 428 g CA-market default and then calibrating to a
       production-only target pushed alpha out of its sanity band for a third of
       BAs on real 2024 data (CISO 0.65, PACW 0.39, BPAT 2.18) — the gate firing
       on exactly the structural mismatch it was designed to catch.
    2. 428 g/kWh is a California market convention. Applying it to, say, Seattle
       City Light's BPA-hydro imports would be plainly wrong; there is no
       defensible national import intensity short of a full trade-flow model.

    Cost: for import-heavy BAs the consumption intensity is understated at the
    level (CISO by roughly 10-15%), and the import contribution to the evening
    shape is lost (second-order: the in-BA gas ramp already carries that shape).
    """
    gen = read_cache(f"gen_{ba}_{year}.csv.gz")
    load = read_cache(f"load_{ba}.csv.gz")["load_kwh"]

    imports = gen.pop("imports_mwh") if "imports_mwh" in gen else None
    ff = _fuel_factors(gas_leakage=gas_leakage)
    fuels = [c for c in gen.columns if c in ff]
    comb_vec = np.array([ff[c][0] for c in fuels])

    G = gen[fuels].to_numpy(float)
    imp = imports.to_numpy(float) if (imports is not None and use_imports) else None

    ihat = core.reconstruct(G, comb_vec, imp)
    gen_total = G.sum(axis=1) + (imp if imp is not None else 0)
    alpha, warns = core.calibrate(ihat, gen_total, egrid_kg)
    I = alpha * ihat

    # Upstream added AFTER calibration, mix-weighted by hour. eGRID is stack-only, so folding
    # upstream into the calibration target would silently cancel it.
    up_vec = np.array([ff[c][1] for c in fuels])
    with np.errstate(invalid="ignore", divide="ignore"):
        up_h = np.where(gen_total > 0, (G @ up_vec) / gen_total, np.nan) / 1000.0
    I_total = I + up_h

    load = load.reindex(gen.index)
    months = gen.index.month.values
    w_annual = core.normalize_shape(load.to_numpy(float))
    w_monthly = core.normalize_shape(load.to_numpy(float), months)

    flat = float(np.average(I_total[np.isfinite(I_total)],
                            weights=gen_total[np.isfinite(I_total)]))
    lw = core.load_weighted_mean(I_total, w_annual)
    cov = core.covariance_uplift(I_total, load.to_numpy(float))

    monthly = {}
    for m in range(1, 13):
        sel = months == m
        monthly[m] = core.load_weighted_mean(I_total[sel], w_monthly[sel])

    profile = pd.DataFrame(
        {"I_h": np.round(I_total, 6), "w_h": np.round(w_annual, 10),
         "w_h_monthnorm": np.round(w_monthly, 10)}, index=gen.index)

    summary = {
        "ba": ba, "year": year, "alpha": round(alpha, 5),
        "egrid_kg_per_kwh": egrid_kg,
        "kg_per_kwh_flat": round(flat, 6),
        "kg_per_kwh_load_weighted": round(lw, 6),
        "uplift_pct": round(100 * (lw / flat - 1), 3),
        "predicted_uplift_pct": round(100 * cov["predicted_uplift"], 3),
        "rho": round(cov["rho"], 4),
        "cv_load": round(cov["cv_load"], 4),
        "cv_intensity": round(cov["cv_intensity"], 4),
        "upstream_kg_per_kwh": round(float(np.nanmean(up_h)), 6),
        "monthly_kg_per_kwh": json.dumps({k: round(v, 6) for k, v in monthly.items()}),
        "warnings": " | ".join(warns),
    }
    return profile, summary


def build_all(year=2024, gas_leakage=1.0):
    os.makedirs(DIST, exist_ok=True)
    summaries = []
    for r in _read_table("egrid_ba_annual.csv"):
        ba = r["ba"]
        try:
            prof, summ = build_ba(ba, year, float(r["kg_co2e_per_kwh"]), gas_leakage)
        except FileNotFoundError:
            print(f"  skip {ba}: no cache")
            continue
        with gzip.open(os.path.join(DIST, f"profile_{ba}.csv.gz"), "wt") as f:
            prof.to_csv(f)
        summ["subregion"] = r["subregion"]
        summaries.append(summ)
        print(f"  {ba}: alpha={summ['alpha']:.3f}  flat={summ['kg_per_kwh_flat']:.4f}  "
              f"load-wtd={summ['kg_per_kwh_load_weighted']:.4f}  "
              f"uplift={summ['uplift_pct']:+.2f}%  (identity predicts "
              f"{summ['predicted_uplift_pct']:+.2f}%)")
        if summ["warnings"]:
            print(f"    ! {summ['warnings']}")
    pd.DataFrame(summaries).to_csv(os.path.join(DIST, "summary.csv"), index=False)
    return summaries


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, default=2024)
    p.add_argument("--gas-leakage", type=float, default=1.0,
                   help="scales the natural gas upstream factor; sweep 0.6-1.8")
    a = p.parse_args()
    build_all(a.year, a.gas_leakage)
