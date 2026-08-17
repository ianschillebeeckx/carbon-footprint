"""
Public API.

    from gridcarbon import GridCarbon
    gc = GridCarbon()

    gc.estimate("94110", annual_kwh=4800)      # -> Estimate
    gc.profile("94110")                        # -> Profile, for UI display
    gc.compare("94110", 4800)                  # -> flat vs load-weighted, for the explainer
"""

from __future__ import annotations

import csv
import gzip
import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .build import DIST, _read_table
from .sources import DATA

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class Estimate:
    zip: str
    ba: str
    subregion: str
    annual_kwh: float
    kg_co2e: float
    kg_per_kwh: float
    kg_per_kwh_flat: float
    uplift_pct: float
    monthly_kg_co2e: dict
    alpha: float
    upstream_kg_per_kwh: float
    year: int
    confidence: str
    warnings: list = field(default_factory=list)

    def __repr__(self):
        return (f"<{self.kg_co2e:,.0f} kg CO2e/yr | {self.kg_per_kwh:.4f} kg/kWh "
                f"| {self.ba} | {self.uplift_pct:+.1f}% vs flat>")


@dataclass
class Profile:
    """Everything a UI needs to draw the duck curve and the load shape against it."""
    ba: str
    zip: str
    year: int
    index: pd.DatetimeIndex
    I_h: np.ndarray              # kg/kWh, 8760
    w_h: np.ndarray              # unitless, sums to 1, 8760
    diurnal_intensity: np.ndarray   # 24, mean kg/kWh by local hour
    diurnal_weight: np.ndarray      # 24, mean load fraction by local hour
    monthly_intensity: np.ndarray   # 12
    monthly_weight: np.ndarray      # 12
    alpha: float
    rho: float

    def to_dict(self):
        """JSON-safe, for handing to a frontend."""
        return {
            "ba": self.ba, "zip": self.zip, "year": self.year, "alpha": self.alpha,
            "rho": self.rho,
            "diurnal": [
                {"hour": h,
                 "kg_per_kwh": round(float(self.diurnal_intensity[h]), 5),
                 "load_fraction": round(float(self.diurnal_weight[h]), 6)}
                for h in range(24)],
            "monthly": [
                {"month": MONTH_NAMES[m],
                 "kg_per_kwh": round(float(self.monthly_intensity[m]), 5),
                 "load_fraction": round(float(self.monthly_weight[m]), 6)}
                for m in range(12)],
        }


BA_TZ = {"CISO": "US/Pacific", "LDWP": "US/Pacific",
         "ERCO": "US/Central", "ISNE": "US/Eastern", "_default": "US/Central"}


class GridCarbon:
    def __init__(self, dist=DIST):
        self.dist = dist
        self.zip_ba = {r["zip"]: r for r in _read_table("zip_ba.csv")}
        self.summary = {}
        with open(os.path.join(dist, "summary.csv")) as f:
            for r in csv.DictReader(f):
                self.summary[r["ba"]] = r
        self._profiles = {}

    # ---------- resolution ----------
    def _resolve(self, zip_code):
        z = str(zip_code).strip()[:5]
        row = self.zip_ba.get(z)
        if row is None:
            raise KeyError(
                f"ZIP {z} not in zip_ba.csv. Populate it from NREL zip->utility joined to "
                "EIA-861 utility->BA, or fall back to the annual subregion factor.")
        ba = row["ba"]
        if ba not in self.summary:
            raise KeyError(f"No built profile for BA {ba}. Run `python -m gridcarbon.build`.")
        return z, row, self.summary[ba]

    # ---------- main entry point ----------
    def estimate(self, zip_code, annual_kwh, monthly_kwh=None) -> Estimate:
        """
        annual_kwh : total kWh for the year. The load SHAPE is supplied by the library; the
                     user only provides scale. E = annual_kwh * sum(w_h * I_h).
        monthly_kwh: optional dict {1..12: kWh}. If given, each month uses its own
                     within-month-normalized factor, which captures seasonal covariance
                     (summer = more AC AND more gas) that an annual number averages away.
                     This is the larger of the two corrections - prefer it when you have bills.
        """
        z, row, s = self._resolve(zip_code)
        monthly_factors = {int(k): v for k, v in json.loads(s["monthly_kg_per_kwh"]).items()}
        warnings = []

        if monthly_kwh:
            per_month = {m: monthly_kwh.get(m, 0.0) * monthly_factors[m] for m in range(1, 13)}
            total = sum(per_month.values())
            used_kwh = sum(monthly_kwh.values())
            eff = total / used_kwh if used_kwh else float("nan")
            conf = "high"
        else:
            eff = float(s["kg_per_kwh_load_weighted"])
            total = annual_kwh * eff
            # Split the annual total across months by the shape's own monthly load fractions.
            # Illustrative only - it cannot know the user's real seasonality.
            p = self.profile(zip_code)
            per_month = {m + 1: total * float(p.monthly_weight[m]) for m in range(12)}
            used_kwh = annual_kwh
            conf = "medium"
            warnings.append(
                "Monthly split is derived from the default load shape, not from the user's "
                "bills. Pass monthly_kwh for a real seasonal breakdown.")

        if s["warnings"]:
            warnings.append(s["warnings"])
        if float(s["alpha"]) < 0.9 or float(s["alpha"]) > 1.1:
            warnings.append(
                f"alpha={s['alpha']} - the fuel-factor reconstruction is off by "
                f"{abs(100*(1-float(s['alpha']))):.0f}% before calibration. Fine (that is what "
                "alpha is for) but worth watching if it drifts between vintages.")

        return Estimate(
            zip=z, ba=row["ba"], subregion=s.get("subregion", ""),
            annual_kwh=used_kwh, kg_co2e=round(total, 1), kg_per_kwh=round(eff, 6),
            kg_per_kwh_flat=float(s["kg_per_kwh_flat"]),
            uplift_pct=round(100 * (eff / float(s["kg_per_kwh_flat"]) - 1), 2),
            monthly_kg_co2e={m: round(v, 1) for m, v in per_month.items()},
            alpha=float(s["alpha"]), upstream_kg_per_kwh=float(s["upstream_kg_per_kwh"]),
            year=int(s["year"]), confidence=conf, warnings=warnings)

    # ---------- UI accessors ----------
    def profile(self, zip_code) -> Profile:
        """
        I_h and w_h for the BA serving this ZIP, plus the aggregates a chart actually wants.

        The 24-element diurnal arrays are the plottable ones: I_h traces the duck curve,
        w_h traces the household load shape, and the gap between where they peak IS the
        covariance term made visible.
        """
        z, row, s = self._resolve(zip_code)
        ba = row["ba"]
        if ba not in self._profiles:
            with gzip.open(os.path.join(self.dist, f"profile_{ba}.csv.gz"), "rt") as f:
                self._profiles[ba] = pd.read_csv(f, index_col=0, parse_dates=True)
        df = self._profiles[ba]
        tz = BA_TZ.get(ba, BA_TZ["_default"])
        local = df.index.tz_convert(tz)

        di = df.groupby(local.hour)["I_h"].mean().reindex(range(24)).to_numpy()
        dw = df.groupby(local.hour)["w_h"].mean().reindex(range(24)).to_numpy()
        mi = df.groupby(local.month)["I_h"].mean().reindex(range(1, 13)).to_numpy()
        mw = df.groupby(local.month)["w_h"].sum().reindex(range(1, 13)).to_numpy()

        return Profile(
            ba=ba, zip=z, year=int(s["year"]), index=df.index,
            I_h=df["I_h"].to_numpy(), w_h=df["w_h"].to_numpy(),
            diurnal_intensity=di, diurnal_weight=dw,
            monthly_intensity=mi, monthly_weight=mw,
            alpha=float(s["alpha"]), rho=float(s["rho"]))

    def compare(self, zip_code, annual_kwh) -> dict:
        """
        Flat-rate vs load-weighted, for the UI explainer. The delta is the covariance term:
        residential load and grid intensity are both evening-peaked, so flat-rate accounting
        underestimates. It is a small number - do not oversell it.
        """
        e = self.estimate(zip_code, annual_kwh)
        flat = annual_kwh * e.kg_per_kwh_flat
        return {
            "flat_kg": round(flat, 1),
            "load_weighted_kg": e.kg_co2e,
            "difference_kg": round(e.kg_co2e - flat, 1),
            "uplift_pct": e.uplift_pct,
            "explanation": (
                "A flat annual factor assumes you use power evenly around the clock. "
                "Household use peaks in the evening, when the grid is dirtiest, so flat-rate "
                "accounting understates emissions."),
        }

    def bas(self):
        return sorted(self.summary)
