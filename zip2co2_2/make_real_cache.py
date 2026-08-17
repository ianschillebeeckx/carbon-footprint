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

# EIA changed the 930 schema mid-2024: Jan-Jun uses the classic fuel set
# ("Solar", "Hydropower and Pumped Storage"); Jul-Dec splits solar/wind by
# integrated-battery status (including a typo'd "Solar witho ..." column),
# breaks out Geothermal and Pumped Storage, and adds Battery/Energy Storage
# discharge columns. Missing this made H2 solar parse as zero — July noons
# showed 85% gas, which is how the bug was caught (thanks Ian).
# All names WITHOUT the " (Adjusted)" suffix; both schemas covered.
FUEL_COLS = {
    "Net Generation (MW) from Coal": "coal",
    "Net Generation (MW) from Natural Gas": "natural_gas",
    "Net Generation (MW) from Nuclear": "nuclear",
    "Net Generation (MW) from All Petroleum Products": "oil",
    "Net Generation (MW) from Hydropower and Pumped Storage": "hydro_large",
    "Net Generation (MW) from Hydropower Excluding Pumped Storage": "hydro_large",
    "Net Generation (MW) from Solar": "solar",
    "Net Generation (MW) from Solar without Integrated Battery Storage": "solar",
    "Net Generation (MW) from Solar with Integrated Battery Storage": "solar",
    "Net Generation (MW) from Solar witho Integrated Battery Storage": "solar",  # EIA's typo, real column
    "Net Generation (MW) from Wind": "wind",
    "Net Generation (MW) from Wind without Integrated Battery Storage": "wind",
    "Net Generation (MW) from Wind with Integrated Battery Storage": "wind",
    "Net Generation (MW) from Geothermal": "geothermal",
    # Storage discharge serves load with stored (in CAISO, mostly midday solar)
    # energy: zero combustion, solar-like upstream + round-trip losses and
    # battery embodied (fuel_factors.csv "storage" row). Charging hours are
    # negative and clipped by core.reconstruct.
    "Net Generation (MW) from Pumped Storage ": "storage",   # note EIA's double space
    "Net Generation (MW) from Pumped Storage": "storage",
    "Net Generation (MW) from Battery Storage": "storage",
    "Net Generation (MW) from Other Energy Storage": "storage",
    "Net Generation (MW) from Unknown Energy Storage": "storage",
    # Unknown = untracked market purchases; gas-like factor via "other" is closer
    # than dropping the MWh (which would inflate every other fuel's share).
    "Net Generation (MW) from Other Fuel Sources": "other",
    "Net Generation (MW) from Unknown Fuel Sources": "other",
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

# Standard-time UTC offsets per BA. TMY3 hours are LOCAL time; the cache index
# is UTC (matching EIA-930), so each load array must be rolled by the offset
# before writing — position j (UTC) must carry the load of local hour j+offset.
# Getting this wrong shifts the residential evening peak to midday and silently
# corrupts the load-intensity covariance. DST (±1h) is ignored; TMY3 is a
# standard-time construct.
BA_UTC_OFFSET = {
    "CISO": -8, "LDWP": -8, "BANC": -8, "IID": -8, "TIDC": -8, "PACW": -8,
    "BPAT": -8, "PGE": -8, "SCL": -8, "PSEI": -8, "TPWR": -8, "AVA": -8,
    "CHPD": -8, "DOPD": -8, "GCPD": -8, "AVRN": -8, "GRID": -8, "NEVP": -8,
    "WAUW": -7, "NWMT": -7, "IPCO": -7, "PACE": -7, "AZPS": -7, "SRP": -7,
    "TEPC": -7, "WALC": -7, "PNM": -7, "EPE": -7, "PSCO": -7, "WACM": -7,
    "GWA": -7, "WWA": -7, "DEAA": -7, "HGMA": -7, "GRIF": -7,
    "ERCO": -6, "SWPP": -6, "MISO": -6, "AECI": -6, "SPA": -6, "EEI": -6,
    "AEC": -6, "LGEE": -5, "OVEC": -5, "PJM": -5, "NYIS": -5, "ISNE": -5,
    "NBSO": -5, "SOCO": -5, "TVA": -5, "DUK": -5, "CPLE": -5, "CPLW": -5,
    "SCEG": -5, "SC": -5, "SCP": -5, "YAD": -5, "SEPA": -5,
    "FPL": -5, "FPC": -5, "TEC": -5, "JEA": -5, "FMPP": -5, "SEC": -5,
    "TAL": -5, "GVL": -5, "HST": -5, "NSB": -5,
}


def load_eia930():
    frames = []
    adj = {k + " (Adjusted)": v for k, v in FUEL_COLS.items()}
    for half in ("Jan_Jun", "Jul_Dec"):
        p = os.path.join(CACHE_RAW, f"EIA930_BALANCE_{YEAR}_{half}.csv")
        keep = ["Balancing Authority", "UTC Time at End of Hour", TI_COL] + list(adj)
        df = pd.read_csv(p, usecols=lambda c: c in keep, thousands=",", low_memory=False)
        matched = [c for c in df.columns if c in adj]
        # Canonicalize + SUM columns mapping to the same fuel (solar w/ + w/o
        # battery, other + unknown, …) — assignment-overwrite here was the bug
        # that dropped H1's Other Fuel Sources.
        out = pd.DataFrame({"Balancing Authority": df["Balancing Authority"],
                            "UTC Time at End of Hour": df["UTC Time at End of Hour"],
                            TI_COL: pd.to_numeric(df[TI_COL], errors="coerce")})
        for c in matched:
            fuel = adj[c]
            v = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
            out[fuel] = out[fuel] + v if fuel in out else v
        frames.append(out)
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
        fuels = [f for f in set(FUEL_COLS.values()) if f in g.columns]
        gen = g[fuels].fillna(0.0)
        # Storage hiding in "Other": some respondents (CISO foremost) never
        # populate EIA's dedicated battery column — their batteries land in
        # Other Fuel Sources, cycling negative at noon (charging) and positive
        # in the evening (discharging). Pricing that at the gas-like Other
        # factor counts discharged midday solar as gas. Split per local day:
        # base = max(0, daily min) stays Other (geo/bio/waste baseload); the
        # excess above base is storage. Flat fossil "other" has cycle≈0 and is
        # untouched; the split only activates where a daily cycle exists.
        if "other" in gen.columns:
            off = BA_UTC_OFFSET.get(ba, -6)
            day = (gen.index + pd.Timedelta(hours=off)).date
            other = gen["other"]
            base = other.groupby(day).transform("min").clip(lower=0.0)
            cycle = (other - base).clip(lower=0.0)
            gen["other"] = other.clip(upper=base.where(other > base, other)).clip(lower=0.0)
            gen["storage"] = gen.get("storage", 0.0) + cycle
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
        # TMY3 is a non-leap synthetic year (8760) in LOCAL time. Roll to UTC
        # alignment (see BA_UTC_OFFSET), then align by position; leap years
        # repeat the final day. w_h is a shape — day-off-by-one is immaterial,
        # the hour-of-day alignment is not.
        off = BA_UTC_OFFSET.get(ba, -6)
        vals = np.roll(arr, -off)          # off=-8 -> roll right 8: UTC j carries local j-8
        n = len(hours)
        vals = np.resize(vals, n)
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
