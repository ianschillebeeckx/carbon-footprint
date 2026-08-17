"""
Generates a SYNTHETIC cache so the library runs end-to-end without network access.

The numbers are fabricated. The STRUCTURE is real: correct column names, correct units,
correct index, correct sign conventions - so `python -m gridcarbon.sources --refresh` drops
real data straight in with no code changes.

Shapes are built to be physically plausible so the covariance math exercises properly:
solar peaking at noon with a seasonal envelope, gas filling the evening ramp, nuclear flat,
residential load with a morning bump and a bigger evening peak plus summer AC.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gridcarbon.sources import write_cache  # noqa: E402

YEAR = 2024
rng = np.random.default_rng(7)


def hours(year=YEAR):
    return pd.date_range(f"{year}-01-01", f"{year+1}-01-01", freq="h", tz="UTC")[:-1]


TZ = {"CISO": "US/Pacific", "LDWP": "US/Pacific",
      "ERCO": "US/Central", "ISNE": "US/Eastern"}


def diurnal(idx, peak_hour, width=4.0, tz="US/Pacific"):
    h = idx.tz_convert(tz).hour.values
    d = np.minimum(np.abs(h - peak_hour), 24 - np.abs(h - peak_hour))
    return np.exp(-0.5 * (d / width) ** 2)


def seasonal(idx, peak_doy, amp=0.3):
    doy = idx.dayofyear.values
    return 1 + amp * np.cos(2 * np.pi * (doy - peak_doy) / 365)


def make_ba(ba, profile):
    tz = TZ[ba]
    idx = hours()
    n = len(idx)
    noise = lambda s: 1 + rng.normal(0, s, n)  # noqa: E731
    g = pd.DataFrame(index=idx)
    for fuel, (level, kind) in profile.items():
        if kind == "solar":
            v = level * diurnal(idx, 12.5, 3.0, tz) * seasonal(idx, 172, 0.45) * noise(0.12)
        elif kind == "flat":
            v = level * np.ones(n) * noise(0.02)
        elif kind == "evening":
            v = level * (0.45 + 0.9 * diurnal(idx, 19, 4.5, tz)) * seasonal(idx, 200, 0.25) * noise(0.15)
        elif kind == "wind":
            v = level * (0.5 + 0.8 * diurnal(idx, 21, 6.0, tz)) * noise(0.45)
        elif kind == "hydro":
            v = level * (0.7 + 0.5 * diurnal(idx, 18, 5.0, tz)) * seasonal(idx, 120, 0.35) * noise(0.15)
        else:
            v = level * np.ones(n) * noise(0.1)
        g[fuel] = np.clip(v, 0, None)
    g["imports_mwh"] = np.clip(
        profile.get("_imports", 3000) * (0.6 + 0.8 * diurnal(idx, 19, 5.0, tz)) * noise(0.2), 0, None
    ) if "_imports" not in profile else 0
    return g


PROFILES = {
    # level in MW, roughly scaled to real BA size
    "CISO": {"natural_gas": (9000, "evening"), "solar": (13000, "solar"),
             "wind": (2200, "wind"), "nuclear": (2200, "flat"),
             "hydro_large": (3000, "hydro"), "other": (1400, "flat"),
             "coal": (30, "flat"), "oil": (10, "flat")},
    "LDWP": {"natural_gas": (1800, "evening"), "solar": (700, "solar"),
             "wind": (300, "wind"), "nuclear": (380, "flat"),
             "hydro_large": (300, "hydro"), "coal": (700, "flat"),
             "other": (100, "flat"), "oil": (5, "flat")},
    "ERCO": {"natural_gas": (18000, "evening"), "solar": (7000, "solar"),
             "wind": (11000, "wind"), "nuclear": (4900, "flat"),
             "coal": (7000, "flat"), "hydro_large": (100, "hydro"),
             "other": (500, "flat"), "oil": (20, "flat")},
    "ISNE": {"natural_gas": (6000, "evening"), "solar": (600, "solar"),
             "wind": (500, "wind"), "nuclear": (3300, "flat"),
             "hydro_large": (900, "hydro"), "coal": (30, "flat"),
             "other": (700, "flat"), "oil": (60, "flat")},
}

# Residential load shape: morning bump, larger evening peak, summer AC envelope.
LOAD_ENVELOPE = {
    "CISO": (172, 0.32), "LDWP": (200, 0.45), "ERCO": (200, 0.55), "ISNE": (190, 0.30),
}


def make_load(ba):
    tz = TZ[ba]
    idx = hours()
    n = len(idx)
    morning = 0.55 * diurnal(idx, 7.5, 2.0, tz)
    evening = 1.0 * diurnal(idx, 19.5, 3.0, tz)
    base = 0.42
    peak_doy, amp = LOAD_ENVELOPE[ba]
    seas = seasonal(idx, peak_doy, amp)
    v = (base + morning + evening) * seas * (1 + rng.normal(0, 0.08, n))
    v = np.clip(v, 0.02, None)
    v = v / v.sum() * 7000.0  # scale to a ~7000 kWh/yr home; only the shape is used
    return pd.DataFrame({"load_kwh": v}, index=idx)


if __name__ == "__main__":
    for ba, prof in PROFILES.items():
        write_cache(f"gen_{ba}_{YEAR}.csv.gz", make_ba(ba, prof))
        write_cache(f"load_{ba}.csv.gz", make_load(ba))
        print(f"wrote synthetic cache for {ba}")
