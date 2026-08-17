"""Generate web/data/sample_hourly_portland.csv — a synthetic year of hourly
consumption for an ELECTRICALLY-HEATED Portland, OR home, as a demo for the
advanced electricity upload.

Why Portland: it is the clearest real case where hourly-exact accounting moves
the answer DOWN. PGE's hydro-backed grid is dirtiest in late summer (reservoirs
low, gas filling in, ~500 g/kWh) and cleanest in spring runoff (~216 g), while
an electric-heat home peaks hard in winter and uses little in the dirty months
— the anti-covariance the TMY3 default only partially captures (electric heat
is winter-peakier than the average TMY3 home, so the discount deepens).

Shape: heating-degree-driven baseload + morning (7-9am) and evening (6-10pm)
heating peaks scaled by a seasonal cold curve; no AC (mild PNW summer);
lighting/plug base with a small evening bump year-round; deterministic noise.
~6,000 kWh/yr. Timestamps are 2024 local standard time, hourly, Green-Button-
style two columns.

Run:  .venv/bin/python scripts/make_sample_hourly.py
"""

import csv
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent.parent / "web" / "data" / "sample_hourly_portland.csv"
rng = np.random.default_rng(42)

rows = []
t = datetime(2024, 1, 1, 0, 0)
total = 0.0
while t.year == 2024:
    doy = t.timetuple().tm_yday
    h = t.hour
    # seasonal cold factor: 1 at New Year, ~0 midsummer (day 200)
    cold = max(0.0, np.cos(2 * np.pi * (doy - 15) / 366)) ** 1.5
    base = 0.28 + 0.10 * (1 if 7 <= h <= 23 else 0)           # plugs, fridge, lights
    evening = 0.25 * np.exp(-0.5 * ((h - 20) / 2.0) ** 2)      # cooking/TV bump
    heat = cold * (0.55                                        # background heat
                   + 1.05 * np.exp(-0.5 * ((h - 7.5) / 1.6) ** 2)   # morning warm-up
                   + 1.25 * np.exp(-0.5 * ((h - 19.5) / 2.4) ** 2)) # evening heat
    kwh = (base + evening + heat) * float(rng.normal(1.0, 0.10))
    kwh = max(0.05, kwh)
    rows.append((t.strftime("%Y-%m-%d %H:%M"), round(kwh, 3)))
    total += kwh
    t += timedelta(hours=1)

with open(OUT, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["timestamp", "kwh"])
    w.writerows(rows)
print(f"Wrote {OUT}: {len(rows)} hours, {total:,.0f} kWh/yr")
jan = sum(v for ts, v in rows if ts.startswith("2024-01"))
jul = sum(v for ts, v in rows if ts.startswith("2024-07"))
print(f"Jan {jan:,.0f} kWh vs Jul {jul:,.0f} kWh (ratio {jan/jul:.1f}x — electric heat)")
