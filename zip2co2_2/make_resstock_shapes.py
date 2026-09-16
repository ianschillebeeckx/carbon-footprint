"""Build residential hourly load shapes from NREL ResStock (2025 release).

Replaces the OpenEI TMY3 residential profiles, which OpenEI itself has
deprecated: submission 153 now carries the notice "This dataset has been
superseded ... contains several errors and limitations. It is recommended that
users of this dataset transition to the updated version." Two of the documented
defects land squarely on stations this project uses -- the TMY3 residential BASE
profiles have no air conditioning in the Marine climate region (our Portland and
Seattle stations) and apply a hard-coded Tampa heating season across Hot-Humid
(our Dallas station).

Source: End-Use Load Profiles for the U.S. Building Stock, 2025 release 1,
AMY2018 weather, CC BY 4.0. State-level timeseries aggregates, all five
residential building types summed, so the shape is stock-weighted rather than
five prototype houses. State is the finest pre-aggregated geography in the
2024/2025 releases (county and PUMA exist only in the 2021 release).

What this writes is a SHAPE, normalised to mean 1.0: it decides which hours of
the grid's intensity curve count more, never how many kWh the user consumed.
That scale comes entirely from the user's own annual total.

Output: cache/resstock_{STATE}.csv.gz, 8760 normalised values in local standard
time (make_real_cache.py rolls them to UTC per BA_UTC_OFFSET).

Run:  python make_resstock_shapes.py            # all states in STATIONS
      python make_resstock_shapes.py CA OR      # just these
"""

import gzip
import os
import sys
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")

BASE = ("https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
        "end-use-load-profiles-for-us-building-stock/2025/resstock_amy2018_release_1/"
        "timeseries_aggregates/by_state/upgrade=0/state={ST}/up00-{st}-{bt}.csv")

# The five residential building types ResStock aggregates by state. Summing all
# five gives the state's occupied-stock total, which is the weighting we want --
# a single-family-only shape would misrepresent dense urban BAs badly.
BUILDING_TYPES = [
    "single-family_detached",
    "single-family_attached",
    "multi-family_with_2_-_4_units",
    "multi-family_with_5plus_units",
    "mobile_home",
]

TS_COL = "timestamp"
KWH_COL = "out.electricity.total.energy_consumption..kwh"


def fetch_shape(state: str) -> np.ndarray:
    """Sum the five building types for one state -> 8760 normalised hourly values."""
    quarter = np.zeros(35040)          # 15-minute intervals over a non-leap year
    got = 0
    for bt in BUILDING_TYPES:
        url = BASE.format(ST=state.upper(), st=state.lower(), bt=bt)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "carbon-ledger/1.0"})
            with urllib.request.urlopen(req, timeout=300) as r:
                head = r.readline().decode().rstrip("\r\n").split(",")
                try:
                    i_kwh = head.index(KWH_COL)
                except ValueError:
                    print(f"  ! {state}/{bt}: no {KWH_COL} column", file=sys.stderr)
                    continue
                vals = np.zeros(35040)
                n = 0
                for line in r:
                    parts = line.decode().rstrip("\r\n").split(",")
                    if n >= 35040:
                        break
                    try:
                        vals[n] = float(parts[i_kwh])
                    except (ValueError, IndexError):
                        pass
                    n += 1
                if n < 35040:
                    print(f"  ! {state}/{bt}: {n} rows, expected 35040", file=sys.stderr)
                    continue
                quarter += vals
                got += 1
        except Exception as e:                       # noqa: BLE001 - report and continue
            print(f"  ! {state}/{bt}: {e}", file=sys.stderr)
    if not got:
        raise RuntimeError(f"{state}: no building types fetched")

    # 15-min -> hourly. Timestamps are interval-ENDING (first row is 00:15, i.e.
    # midnight-to-00:15), so consecutive groups of four starting at row 0 are
    # hours 0..8759 with no offset.
    hourly = quarter.reshape(8760, 4).sum(axis=1)
    mean = hourly.mean()
    if mean <= 0:
        raise RuntimeError(f"{state}: zero mean load")
    return hourly / mean, got


def main(states):
    os.makedirs(CACHE, exist_ok=True)
    for st in states:
        out = os.path.join(CACHE, f"resstock_{st.upper()}.csv.gz")
        if os.path.exists(out):
            print(f"  {st}: cached")
            continue
        shape, got = fetch_shape(st)
        with gzip.open(out, "wt") as f:
            f.write("load_norm\n")
            for v in shape:
                f.write(f"{v:.6f}\n")
        peak = int(np.argmax(shape.reshape(365, 24).mean(axis=0)))
        print(f"  {st}: {got}/5 types, mean-day peak hour {peak:02d}:00 local -> {out}")


if __name__ == "__main__":
    import re
    if len(sys.argv) > 1:
        want = [s.upper() for s in sys.argv[1:]]
    else:
        src = open(os.path.join(HERE, "make_real_cache.py")).read()
        block = re.search(r"STATIONS = \{(.*?)\n\}", src, re.S).group(1)
        want = sorted({s for _, s in re.findall(r'"(\w+)":\s*"([A-Z]{2})_', block)})
    print(f"ResStock 2025 shapes for {len(want)} states: {' '.join(want)}")
    main(want)
