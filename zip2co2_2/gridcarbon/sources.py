"""
Data acquisition. Real code against real endpoints, but NOT RUN HERE - this sandbox reaches
package registries only, not eia.gov or openei.org. Everything in data/cache/ is synthetic.
Run `python -m gridcarbon.sources --refresh --eia-key=...` locally to replace it.

Sources:
    I_h   EIA Hourly Electric Grid Monitor v2   hourly generation by fuel, by BA, 2018-present
    alpha eGRID BA sheet                        annual kg/kWh, by BA
    w_h   OpenEI TMY3 residential load profiles hourly kWh by climate location
"""

from __future__ import annotations

import gzip
import io
import os
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE = os.path.join(DATA, "cache")

EIA_BASE = "https://api.eia.gov/v2/electricity/rto"

# EIA fuel codes -> keys in fuel_factors.csv.
#
# Two lossy joins, both absorbed by alpha:
#   OTH lumps geothermal and biomass together. CAISO geothermal is 4-5% of generation, so this
#     is not negligible for a carbon-free percentage - though it barely moves intensity, since
#     both are ~0 combustion. Use CAISO's own feed if you need the split.
#   WAT cannot be divided large/small hourly. No intensity effect; breaks cf_rps_ca only.
EIA_FUEL_MAP = {
    "COL": "coal", "NG": "natural_gas", "OIL": "oil", "NUC": "nuclear",
    "WAT": "hydro_large", "SUN": "solar", "WND": "wind", "OTH": "other",
}


def fetch_eia_fuel_mix(ba: str, start: str, end: str, api_key: str) -> pd.DataFrame:
    """Hourly generation by fuel for one BA. Returns wide frame indexed by UTC timestamp."""
    q = [("frequency", "hourly"), ("data[0]", "value"), ("facets[respondent][]", ba),
         ("start", start), ("end", end), ("sort[0][column]", "period"),
         ("sort[0][direction]", "asc"), ("length", "5000"), ("api_key", api_key)]
    rows, offset = [], 0
    while True:
        url = f"{EIA_BASE}/fuel-type-data/data/?" + urllib.parse.urlencode(
            q + [("offset", str(offset))])
        with urllib.request.urlopen(url, timeout=60) as r:
            page = pd.read_json(io.BytesIO(r.read()))["response"]["data"]
        if not page:
            break
        rows.extend(page)
        offset += 5000
    df = pd.DataFrame(rows)
    df["period"] = pd.to_datetime(df["period"], utc=True)
    wide = df.pivot_table(index="period", columns="fueltype", values="value", aggfunc="sum")
    return wide.rename(columns=EIA_FUEL_MAP)


def fetch_eia_interchange(ba: str, start: str, end: str, api_key: str) -> pd.Series:
    """
    Net total interchange (TI). Positive = net export in EIA's sign convention, so imports are
    -TI. Separate endpoint from fuel mix; skipping it biases import-heavy BAs low.
    """
    q = [("frequency", "hourly"), ("data[0]", "value"), ("facets[respondent][]", ba),
         ("facets[type][]", "TI"), ("start", start), ("end", end),
         ("length", "5000"), ("api_key", api_key)]
    url = f"{EIA_BASE}/region-data/data/?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=60) as r:
        data = pd.read_json(io.BytesIO(r.read()))["response"]["data"]
    df = pd.DataFrame(data)
    df["period"] = pd.to_datetime(df["period"], utc=True)
    return (-df.set_index("period")["value"]).clip(lower=0).rename("imports_mwh")


def fetch_openei_load_shape(usaf_station: str) -> pd.Series:
    """
    OpenEI 'Commercial and Residential Hourly Load Profiles for all TMY3 Locations'.
    8760 hourly kWh for a representative single-family home.

    KNOWN LIMITATION: TMY3 is a synthetic composite year. Its hot days do not line up with any
    real intensity year's hot days, so the synoptic covariance term (heat wave = high load AND
    high intensity on the same day) averages to zero. You get diurnal and seasonal only. For
    the full covariance, use ResStock actual-meteorological-year runs, or EIA's own BA demand
    series - wrong sector, but at least the weather is aligned.
    """
    url = ("https://data.openei.org/files/961/"
           f"USA_{usaf_station}_TMY3_BASE.csv")
    with urllib.request.urlopen(url, timeout=60) as r:
        df = pd.read_csv(io.BytesIO(r.read()))
    col = [c for c in df.columns if "Electricity:Facility" in c][0]
    return df[col].astype(float)


def read_cache(name: str) -> pd.DataFrame:
    path = os.path.join(CACHE, name)
    with gzip.open(path, "rt") as f:
        return pd.read_csv(f, index_col=0, parse_dates=True)


def write_cache(name: str, df: pd.DataFrame) -> None:
    os.makedirs(CACHE, exist_ok=True)
    with gzip.open(os.path.join(CACHE, name), "wt") as f:
        df.to_csv(f)


def refresh(bas, year, api_key, stations):
    """Pull everything and write the cache. Run locally."""
    start, end = f"{year}-01-01T00", f"{year+1}-01-01T00"
    for ba in bas:
        gen = fetch_eia_fuel_mix(ba, start, end, api_key)
        gen["imports_mwh"] = fetch_eia_interchange(ba, start, end, api_key).reindex(gen.index)
        write_cache(f"gen_{ba}_{year}.csv.gz", gen)
    for ba, st in stations.items():
        write_cache(f"load_{ba}.csv.gz",
                    fetch_openei_load_shape(st).to_frame("load_kwh"))


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--eia-key", required=False)
    p.add_argument("--year", type=int, default=2024)
    p.add_argument("--bas", default="CISO,LDWP,ERCO,ISNE")
    a = p.parse_args()
    if a.refresh:
        if not a.eia_key:
            raise SystemExit("--eia-key required. Free at https://www.eia.gov/opendata/")
        refresh(a.bas.split(","), a.year, a.eia_key, {})
    else:
        print(__doc__)
