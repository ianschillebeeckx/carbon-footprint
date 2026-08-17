"""Build gridcarbon's data/cache from REAL sources, replacing make_synthetic_cache.py.

Inputs (download once into cache/ — no API key needed):
    EIA930_BALANCE_2024_{Jan_Jun,Jul_Dec}.csv
        eia.gov/electricity/gridmonitor/sixMonthFiles/... — hourly net generation
        by fuel ("Adjusted" columns, EIA's cleaned series) + Total Interchange,
        per balancing authority. This is the same data the v2 API serves, in bulk.
    egrid_ba23.csv
        extracted from the eGRID2023 workbook BA23 sheet (BACODE, BAC2ERTA lb/MWh,
        BANGENAN MWh) — the calibration anchor and the BA universe.
    USA_*_TMY3_BASE.csv (fetched here, per station)
        OpenEI "Commercial and Residential Hourly Load Profiles" residential BASE
        files — 8760 hourly kWh for a representative single-family home.

Load-shape stations: one representative TMY3 station per BA, hand-picked for the
BA's dominant population center (climate drives the AC season; the diurnal
double peak is universal). load-shape resolution is deliberately coarser than
intensity resolution — w_h only reweights hours.

Import treatment: Total Interchange enters reconstruction (shape), and
calibration pins the annual mean to eGRID's production-based BA rate (level).
Post-calibration, imports therefore contribute SHAPE only — evening import-heavy
hours get relatively dirtier — while the level stays reconcilable to EPA's
published number. That is the library's documented contract (core.py).

Writes: gridcarbon/data/cache/gen_{BA}_2024.csv.gz + load_{BA}.csv.gz
        gridcarbon/data/egrid_ba_annual.csv (real, replacing the seed)

Run:  ../.venv/bin/python make_real_cache.py
"""

import csv
import io
import os
import sys
import urllib.request

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_RAW = os.path.join(HERE, "cache")
sys.path.insert(0, HERE)
from gridcarbon.sources import write_cache  # noqa: E402

YEAR = 2024

FUEL_COLS = {
    "Net Generation (MW) from Coal (Adjusted)": "coal",
    "Net Generation (MW) from Natural Gas (Adjusted)": "natural_gas",
    "Net Generation (MW) from Nuclear (Adjusted)": "nuclear",
    "Net Generation (MW) from All Petroleum Products (Adjusted)": "oil",
    "Net Generation (MW) from Hydropower and Pumped Storage (Adjusted)": "hydro_large",
    "Net Generation (MW) from Solar (Adjusted)": "solar",
    "Net Generation (MW) from Wind (Adjusted)": "wind",
    "Net Generation (MW) from Other Fuel Sources (Adjusted)": "other",
    # Unknown = untracked market purchases; gas-like factor via "other" is closer
    # than dropping the MWh (which would inflate every other fuel's share).
    "Net Generation (MW) from Unknown Fuel Sources (Adjusted)": "other",
}
TI_COL = "Total Interchange (MW) (Adjusted)"

# BA -> representative OpenEI TMY3 residential station (dominant load center).
STATIONS = {
    "CISO": "CA_San.Francisco.Intl.AP.724940",
    "LDWP": "CA_Los.Angeles.Intl.AP.722950",
    "BANC": "CA_Sacramento.Metro.AP.724839",
    "IID": "CA_Imperial.County.AP.747185",
    "TIDC": "CA_Modesto.Muni.AP.724926",
    "ERCO": "TX_Dallas-Fort.Worth.Intl.AP.722590",
    "SWPP": "OK_Oklahoma.City.Will.Rogers.World.AP.723530",
    "MISO": "IL_Chicago-OHare.Intl.AP.725300",
    "PJM": "PA_Philadelphia.Intl.AP.724080",
    "NYIS": "NY_New.York-Central.Prk.Obs.Belv.725033",
    "ISNE": "MA_Boston-Logan.Intl.AP.725090",
    "SOCO": "GA_Atlanta-Hartsfield-Jackson.Intl.AP.722190",
    "TVA": "TN_Nashville.Intl.AP.723270",
    "DUK": "NC_Charlotte-Douglas.Intl.AP.723140",
    "CPLE": "NC_Raleigh-Durham.Intl.AP.723060",
    "CPLW": "NC_Asheville.Rgnl.AP.723150",
    "SCEG": "SC_Columbia.Metro.AP.723100",
    "SC": "SC_Columbia.Metro.AP.723100",
    "SCP": "SC_Columbia.Metro.AP.723100",
    "YAD": "NC_Charlotte-Douglas.Intl.AP.723140",
    "FPL": "FL_Miami.Intl.AP.722020",
    "FPC": "FL_Tampa.Intl.AP.722110",
    "TEC": "FL_Tampa.Intl.AP.722110",
    "JEA": "FL_Jacksonville.Intl.AP.722060",
    "FMPP": "FL_Orlando.Intl.AP.722050",
    "SEC": "FL_Orlando.Intl.AP.722050",
    "TAL": "FL_Tallahassee.Rgnl.AP.722140",
    "GVL": "FL_Gainesville.Rgnl.AP.722146",
    "HST": "FL_Miami.Intl.AP.722020",
    "NSB": "FL_Daytona.Beach.Intl.AP.722056",
    "PACE": "UT_Salt.Lake.City.Intl.AP.725720",
    "PACW": "OR_Portland.Intl.AP.726980",
    "BPAT": "OR_Portland.Intl.AP.726980",
    "PGE": "OR_Portland.Intl.AP.726980",
    "SCL": "WA_Seattle-Tacoma.Intl.AP.727930",
    "PSEI": "WA_Seattle-Tacoma.Intl.AP.727930",
    "TPWR": "WA_Seattle-Tacoma.Intl.AP.727930",
    "AVA": "WA_Spokane.Intl.AP.727850",
    "CHPD": "WA_Spokane.Intl.AP.727850",
    "DOPD": "WA_Spokane.Intl.AP.727850",
    "GCPD": "WA_Spokane.Intl.AP.727850",
    "WAUW": "MT_Great.Falls.Intl.AP.727750",
    "NWMT": "MT_Billings.Logan.Intl.AP.726770",
    "IPCO": "ID_Boise.Air.Terminal.726810",
    "NEVP": "NV_Las.Vegas.McCarran.Intl.AP.723860",
    "AZPS": "AZ_Phoenix.Sky.Harbor.Intl.AP.722780",
    "SRP": "AZ_Phoenix.Sky.Harbor.Intl.AP.722780",
    "TEPC": "AZ_Tucson.Intl.AP.722740",
    "WALC": "AZ_Phoenix.Sky.Harbor.Intl.AP.722780",
    "PNM": "NM_Albuquerque.Intl.AP.723650",
    "EPE": "TX_El.Paso.Intl.AP.722700",
    "PSCO": "CO_Denver.Intl.AP.725650",
    "WACM": "CO_Denver.Intl.AP.725650",
    "AECI": "MO_Springfield.Rgnl.AP.724400",
    "LGEE": "KY_Louisville.Standiford.Field.724230",
    "AEC": "AL_Mobile.Rgnl.AP.722230",
    "SEPA": "GA_Atlanta-Hartsfield-Jackson.Intl.AP.722190",
    "GRID": "OR_Portland.Intl.AP.726980",
    "GWA": "MT_Great.Falls.Intl.AP.727750",
    "WWA": "MT_Great.Falls.Intl.AP.727750",
    "GRIF": "AZ_Phoenix.Sky.Harbor.Intl.AP.722780",
    "GLHB": "KY_Louisville.Standiford.Field.724230",
    "DEAA": "AZ_Phoenix.Sky.Harbor.Intl.AP.722780",
    "HGMA": "AZ_Phoenix.Sky.Harbor.Intl.AP.722780",
    "EEI": "IL_Chicago-OHare.Intl.AP.725300",
    "AVRN": "OR_Portland.Intl.AP.726980",
    "NBSO": "ME_Portland.Intl.Jetport.726060",
    "OVEC": "OH_Cincinnati.Muni.AP.Lunken.Field.724297",
}
TMY3_URL = ("https://openei.org/datasets/files/961/pub/"
            "RESIDENTIAL_LOAD_DATA_E_PLUS_OUTPUT/BASE/USA_{st}_TMY3_BASE.csv")


def load_eia930():
    frames = []
    for half in ("Jan_Jun", "Jul_Dec"):
        p = os.path.join(CACHE_RAW, f"EIA930_BALANCE_{YEAR}_{half}.csv")
        usecols = ["Balancing Authority", "UTC Time at End of Hour", TI_COL] + list(FUEL_COLS)
        df = pd.read_csv(p, usecols=lambda c: c in usecols, thousands=",", low_memory=False)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["UTC Time at End of Hour"], format="%m/%d/%Y %I:%M:%S %p", utc=True)
    return df


def _tmy3_catalog():
    """USAF station code -> exact filename, from the saved directory listing
    (cache/tmy3_files.txt; punctuation in the real names is inconsistent, the
    numeric station code is the reliable key)."""
    cat = {}
    with open(os.path.join(CACHE_RAW, "tmy3_files.txt")) as f:
        for name in f.read().split():
            m = name.rsplit(".", 2)          # ...AP.722780_TMY3_BASE.csv
            code = name.replace("_TMY3_BASE.csv", "").rsplit(".", 1)[-1]
            if code.isdigit():
                cat[code] = name
    return cat


def fetch_station(st, catalog):
    code = st.rsplit(".", 1)[-1]
    fname = catalog.get(code)
    if fname is None:
        raise KeyError(f"station code {code} ({st}) not in TMY3 catalog")
    p = os.path.join(CACHE_RAW, f"tmy3_{code}.csv")
    if not os.path.exists(p):
        url = ("https://openei.org/datasets/files/961/pub/"
               f"RESIDENTIAL_LOAD_DATA_E_PLUS_OUTPUT/BASE/{fname}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            open(p, "wb").write(r.read())
    df = pd.read_csv(p)
    col = [c for c in df.columns if "Electricity:Facility" in c][0]
    return df[col].astype(float).to_numpy()


def main():
    df = load_eia930()
    hours = pd.date_range(f"{YEAR}-01-01 01:00", f"{YEAR+1}-01-01 00:00", freq="h", tz="UTC")
    egrid = {r["ba"]: r for r in csv.DictReader(open(os.path.join(CACHE_RAW, "egrid_ba23.csv")))}
    built, skipped = [], []
    for ba, g in df.groupby("Balancing Authority"):
        if ba not in egrid:
            skipped.append(ba)
            continue
        g = g.drop_duplicates("ts").set_index("ts").reindex(hours)
        gen = pd.DataFrame(index=hours)
        for col, fuel in FUEL_COLS.items():
            if col in g:
                v = pd.to_numeric(g[col], errors="coerce")
                gen[fuel] = v.fillna(0.0) if fuel in gen else v.fillna(0.0) + gen.get(fuel, 0.0)
        # merge duplicate "other" columns
        gen = gen.T.groupby(level=0).sum().T
        if gen.to_numpy().sum() <= 0:
            skipped.append(ba)
            continue
        ti = pd.to_numeric(g.get(TI_COL), errors="coerce")
        gen["imports_mwh"] = (-ti).clip(lower=0).fillna(0.0)   # negative TI = net import
        write_cache(f"gen_{ba}_{YEAR}.csv.gz", gen)
        built.append(ba)

    print(f"gen caches: {len(built)} BAs ({', '.join(sorted(built)[:12])}…); "
          f"skipped {len(skipped)} without eGRID rows or generation")

    # load shapes
    catalog = _tmy3_catalog()
    station_cache = {}
    for ba in built:
        st = STATIONS.get(ba)
        if st is None:
            print(f"  ! no station for {ba}; using Dallas (national-ish default)")
            st = "TX_Dallas-Fort.Worth.Intl.AP.722590"
        if st not in station_cache:
            arr = fetch_station(st, catalog)
            station_cache[st] = arr
            print(f"  fetched TMY3 {st} ({len(arr)} hours)")
        arr = station_cache[st]
        # TMY3 is a non-leap synthetic year (8760). Align by position; leap years
        # repeat the final day. w_h is a shape — day-off-by-one is immaterial.
        n = len(hours)
        vals = np.resize(arr, n)
        write_cache(f"load_{ba}.csv.gz", pd.DataFrame({"load_kwh": vals}, index=hours))

    # real egrid_ba_annual.csv
    out = os.path.join(HERE, "gridcarbon", "data", "egrid_ba_annual.csv")
    with open(out, "w", newline="") as f:
        f.write("# eGRID2023 rev2 BA23 sheet: BACO2E annual total output rate (BAC2ERTA lb/MWh\n")
        f.write("# -> kg/kWh). Production-based; imports shape-only per make_real_cache.py note.\n")
        w = csv.writer(f)
        w.writerow(["ba", "ba_name", "subregion", "egrid_year", "kg_co2e_per_kwh", "data_quality"])
        for ba in sorted(built):
            r = egrid[ba]
            w.writerow([ba, r["ba_name"], "", 2023,
                        round(float(r["co2e_lb_per_mwh"]) * 0.45359237 / 1000, 6), "PUBLISHED"])
    print(f"egrid_ba_annual.csv: {len(built)} BAs (PUBLISHED)")


if __name__ == "__main__":
    main()
