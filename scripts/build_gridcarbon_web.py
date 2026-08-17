"""Precompute web/data/gridcarbon.json — hourly-aware electricity factors.

Replaces the zip2co2 supplier dataset (build_zip2co2_web.py, retired): per the
grid-average decision there is no provider dimension — every household in a
balancing authority gets that BA's residential-load-weighted grid intensity.

Per BA (from zip2co2_2/gridcarbon dist):
    kg    : load-weighted kg CO2e/kWh (mix-weighted upstream included) x the
            US-average delivery-loss gross-up (eGRID grid gross loss — EIA-930
            generation and eGRID BA rates are busbar; the user's kWh is metered)
    flat  : same but flat (generation-weighted) — the "vs flat" explainer
    di/dw : 24-value diurnal intensity (kg/kWh) and load-share arrays — tooltip
    mo    : 12 monthly load-weighted factors

Shape-trust policy (see build notes in zip2co2_2/gridcarbon/build.py): a BA
ships its hourly shape only if alpha is in [0.70, 1.45], |uplift| <= 12%, and
>= 90% of hours reconstructed. Otherwise it degrades to the flat eGRID+upstream
level with no diurnal claim (di=null) — honest, if less interesting.

Fallback layer: ZIPs with no BA mapping (PR/AK/HI, small munis) resolve to a
flat eGRID-subregion factor computed here: EPA's subregion CO2e rate +
mix-weighted upstream (fuel_factors.csv) x that subregion's own loss gross-up.
Inputs (gridcarbon/data): zip_subregion.csv + egrid_subregion_factors.csv,
both parsed from EPA sources by the retired zip2co2 module's build (see git
history for the provenance chain; the CSVs carry source comments).

Run:  .venv/bin/python scripts/build_gridcarbon_web.py
"""

import csv
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
GC = ROOT / "zip2co2_2" / "gridcarbon"
OUT = ROOT / "web" / "data" / "gridcarbon.json"
LB_TO_KG = 0.45359237

ALPHA_BAND = (0.70, 1.45)
UPLIFT_MAX = 12.0        # |%|
COVERAGE_MIN = 0.90
US_LOSS_GROSS_UP = 1 / (1 - 0.042)   # eGRID2023 US grid gross loss 4.2%

BA_TZ = {"CISO": -8, "LDWP": -8, "BANC": -8, "IID": -8, "TIDC": -8,
         "PACW": -8, "BPAT": -8, "PGE": -8, "SCL": -8, "PSEI": -8, "TPWR": -8,
         "AVA": -8, "CHPD": -8, "DOPD": -8, "GCPD": -8, "AVRN": -8, "GRID": -8,
         "NEVP": -8, "WAUW": -7, "NWMT": -7, "IPCO": -7, "PACE": -7,
         "AZPS": -7, "SRP": -7, "TEPC": -7, "WALC": -7, "PNM": -7, "EPE": -7,
         "PSCO": -7, "WACM": -7, "GWA": -7, "WWA": -7, "DEAA": -7, "HGMA": -7,
         "GRIF": -7, "ERCO": -6, "SWPP": -6, "MISO": -6, "AECI": -6, "SPA": -6,
         "EEI": -6, "LGEE": -5, "OVEC": -5, "PJM": -5, "NYIS": -5, "ISNE": -5,
         "NBSO": -5, "SOCO": -5, "TVA": -5, "DUK": -5, "CPLE": -5, "CPLW": -5,
         "SCEG": -5, "SC": -5, "SCP": -5, "YAD": -5, "SEPA": -5, "AEC": -6,
         "FPL": -5, "FPC": -5, "TEC": -5, "JEA": -5, "FMPP": -5, "SEC": -5,
         "TAL": -5, "GVL": -5, "HST": -5, "NSB": -5}


def read_commented(path):
    return list(csv.DictReader(ln for ln in open(path) if not ln.lstrip().startswith("#")))


def diurnal(df, ba):
    off = BA_TZ.get(ba, -6)
    hours = (df.index.hour + off) % 24
    di = df.groupby(hours)["I_h"].mean().reindex(range(24))
    dw = df.groupby(hours)["w_h"].mean().reindex(range(24))
    dw = dw / dw.sum()
    return di.to_numpy(), dw.to_numpy()


def hourly_local(df, ba):
    """8784-value intensity array indexed by LOCAL hour-of-year (standard time,
    2024 is a leap year) for the advanced upload path: index = (day_of_year-1)*24
    + local_hour, so a browser can join user interval data by calendar position
    without timezone libraries. None where the hour wasn't reconstructed."""
    off = BA_TZ.get(ba, -6)
    local = df.index + pd.Timedelta(hours=off)
    idx = (local.dayofyear - 1) * 24 + local.hour
    out = [None] * 8784
    vals = df["I_h"].to_numpy()
    for i, v in zip(idx, vals):
        if 0 <= i < 8784 and np.isfinite(v):
            out[i] = round(float(v) * US_LOSS_GROSS_UP, 4)
    return out


def main():
    summary = {r["ba"]: r for r in csv.DictReader(open(GC / "dist" / "summary.csv"))}
    bas, ba_idx = [], {}
    shaped = flat_only = 0
    for ba, s in sorted(summary.items()):
        alpha, uplift = float(s["alpha"]), float(s["uplift_pct"])
        with gzip.open(GC / "dist" / f"profile_{ba}.csv.gz", "rt") as f:
            df = pd.read_csv(f, index_col=0, parse_dates=True)
        coverage = float(np.isfinite(df["I_h"]).mean())
        lw, fl = float(s["kg_per_kwh_load_weighted"]), float(s["kg_per_kwh_flat"])
        egrid = max(0.0, float(s["egrid_kg_per_kwh"]))
        up = float(s["upstream_kg_per_kwh"])
        ok = (ALPHA_BAND[0] <= alpha <= ALPHA_BAND[1]
              and abs(uplift) <= UPLIFT_MAX and coverage >= COVERAGE_MIN)
        o = {"ba": ba, "name": s.get("ba_name") or ba}
        if ok:
            di, dw = diurnal(df, ba)
            mo = {int(k): v for k, v in json.loads(s["monthly_kg_per_kwh"]).items()}
            o.update({
                "kg": round(lw * US_LOSS_GROSS_UP, 4),
                "flat": round(fl * US_LOSS_GROSS_UP, 4),
                "uplift": round(uplift, 1),
                "di": [round(float(x) * US_LOSS_GROSS_UP, 4) for x in di],
                "dw": [round(float(x), 5) for x in dw],
                "mo": [round(mo[m] * US_LOSS_GROSS_UP, 4) for m in range(1, 13)],
                "hourly": True,   # web/data/hourly/{BA}.json exists (advanced upload)
            })
            hdir = OUT.parent / "hourly"
            hdir.mkdir(parents=True, exist_ok=True)
            (hdir / f"{ba}.json").write_text(json.dumps(
                {"ba": ba, "year": 2024, "note": "kg CO2e/kWh delivered, indexed by "
                 "local (standard-time) hour of year: (doy-1)*24+hour",
                 "I": hourly_local(df, ba)}, separators=(",", ":")))
            shaped += 1
        else:
            # Degraded: eGRID level + upstream, flat across hours; no diurnal claim.
            kg = (egrid + up) * US_LOSS_GROSS_UP
            o.update({"kg": round(kg, 4), "flat": round(kg, 4), "uplift": 0.0,
                      "di": None, "dw": None, "mo": [round(kg, 4)] * 12})
            flat_only += 1
        ba_idx[ba] = len(bas)
        bas.append(o)

    # BA names from the eGRID extraction (summary lacks them)
    for r in read_commented(GC / "data" / "egrid_ba_annual.csv"):
        if r["ba"] in ba_idx:
            bas[ba_idx[r["ba"]]]["name"] = r["ba_name"]

    zips, utils, util_idx = {}, [], {}
    for r in read_commented(GC / "data" / "zip_ba.csv"):
        if r["ba"] not in ba_idx:
            continue
        u = r["utility"]
        if u not in util_idx:
            util_idx[u] = len(utils)
            utils.append(u)
        zips[r["zip"]] = [ba_idx[r["ba"]], util_idx[u]]

    # ---- eGRID-subregion flat fallback for unmapped ZIPs ----
    # kg/kWh = subregion CO2e rate + mix-weighted upstream, grossed up by the
    # subregion's own grid loss (delivered basis, like the BA factors).
    up_g = {}   # fuel -> upstream g/kWh
    for r in read_commented(GC / "data" / "fuel_factors.csv"):
        up_g[r["fuel"]] = float(r["g_co2e_per_kwh"] or 0)
    egrid_mix_fuel = {"pct_coal": "coal", "pct_natural_gas": "natural_gas", "pct_oil": "oil",
                      "pct_nuclear": "nuclear", "pct_hydro": "hydro_large", "pct_wind": "wind",
                      "pct_solar": "solar", "pct_geothermal": "geothermal",
                      "pct_biomass": "biomass", "pct_other": "other"}
    sub_kg = {}
    for r in read_commented(GC / "data" / "egrid_subregion_factors.csv"):
        comb = float(r["co2e_lb_per_mwh"]) * LB_TO_KG / 1000.0
        upstream = sum(float(r[c] or 0) / 100.0 * up_g[f] for c, f in egrid_mix_fuel.items()) / 1000.0
        loss = 1.0 / (1.0 - float(r["grid_gross_loss_pct"]) / 100.0)
        sub_kg[r["subregion"]] = round((comb + upstream) * loss, 4)
    fb_entries, fb_idx = [], {}
    fb_zips = {}
    for r in read_commented(GC / "data" / "zip_subregion.csv"):
        if r["zip"] in zips:
            continue
        subs = [r[f"subregion_{i}"] for i in (1, 2, 3) if r.get(f"subregion_{i}")]
        subs = [s for s in subs if s in sub_kg]
        if not subs:
            continue
        key = "+".join(subs)
        if key not in fb_idx:
            fb_idx[key] = len(fb_entries)
            fb_entries.append({"sub": key, "kg": round(sum(sub_kg[s] for s in subs) / len(subs), 4)})
        fb_zips[r["zip"]] = fb_idx[key]
    us_flat = sub_kg["US"]

    OUT.write_text(json.dumps({
        "year": 2024, "egrid_year": 2023,
        "bas": bas, "zips": zips, "utils": utils,
        "fb": fb_entries, "fb_zips": fb_zips, "us_kg": us_flat,
        "sources": "EIA-930 hourly generation 2024; eGRID2023 BA rates (calibration) "
                   "+ 4.2% US grid loss; OpenEI TMY3 residential load shapes; "
                   "IPCC AR5 upstream. Fallback: eGRID2023 subregion.",
    }, separators=(",", ":")))
    print(f"Wrote {OUT}: {OUT.stat().st_size:,}b — {len(bas)} BAs "
          f"({shaped} with hourly shape, {flat_only} flat-degraded), "
          f"{len(zips):,} ZIPs direct, {len(fb_zips):,} fallback ZIPs")

    ciso = bas[ba_idx["CISO"]]
    print(f"CISO: {ciso['kg']} kg/kWh lw (flat {ciso['flat']}, +{ciso['uplift']}%), "
          f"evening peak {max(ciso['di']):.3f} vs midday {min(ciso['di']):.3f}")
    assert zips["94110"][0] == ba_idx["CISO"]
    assert 0.15 < ciso["kg"] < 0.35 and ciso["di"][19] > ciso["di"][12]


if __name__ == "__main__":
    main()
