"""Download + parse the 90 CEC 2023 Power Content Label PDFs into
data/cec_psd_supplier_factors.csv.

No aggregated dataset exists (confirmed against the PSD program pages and the
electricity-data almanac) — the per-supplier PDFs on the "Annual Power Content
Labels for 2023" page are the publication of record, so we parse those.

The labels are generated from one template, which makes them parseable:
  - Each resource row reads "Solar 20.2% 59.0% 100.0% 100.0% 17.0%" — one
    percentage per product, the last column always the 2023 CA Power Mix.
  - The GHG intensity column renders as a run of bare integers embedded in the
    table text (e.g. "12 6 0 0 373"), one per product plus the CA utility
    average. In 2023 that average is 373 lbs CO2e/MWh, which doubles as a
    checksum: a parse is accepted only if the last value is 373.
  - Product names appear line-by-line in the chart legend that precedes the
    trailing "2023 CA Utility Average" line.

basis_denominator: retail_sales for every row. CEC PSD methodology computes the
label intensity as portfolio GHG emissions divided by annual retail sales (that
is why 100%-renewable products print 0), so T&D losses are already embedded and
tier2_factors.py must not gross them up again (its decision 5).

Run:  .venv/bin/python zip2co2/cec_labels.py         (downloads into cache/pcl/)
"""

import csv
import re
import time
import urllib.request
from pathlib import Path

from pypdf import PdfReader

HERE = Path(__file__).parent
CACHE = HERE / "cache" / "pcl"
OUT = HERE / "data" / "cec_psd_supplier_factors.csv"
PSD_YEAR = 2023
CA_AVG_INTENSITY = 373          # 2023 CA utility average, the parse checksum
BASE = "https://www.energy.ca.gov/filebrowser/download/"
UA = {"User-Agent": "Mozilla/5.0 (personal carbon calculator; data build script)"}

# The complete listing from the "Annual Power Content Labels for 2023" page.
SUPPLIERS = [
    ("3 Phases Renewables", 7225), ("Alameda Municipal Power", 7226),
    ("Anaheim Public Utilities", 7227), ("Anza Electric Cooperative Inc.", 7228),
    ("Apple Valley Choice Energy", 7229), ("Ava Community Energy", 7230),
    ("Azusa Light and Water", 7231), ("Bear Valley Electric Service Inc.", 7232),
    ("BP Energy Retail Company California, LLC", 7233), ("Burbank Water and Power", 7234),
    ("Calpine Energy Solutions, LLC", 7235), ("Calpine PowerAmerica CA, LLC", 7236),
    ("Central Coast Community Energy", 7237), ("Cerritos Electric Utility", 7238),
    ("City of Banning Electric Utility", 7391), ("City of Biggs Electric Utility", 7372),
    ("City of Colton Electric Utility", 7392), ("City of Corona Utilities Department", 7393),
    ("City of Gridley Electric Utility", 7394), ("City of Healdsburg", 7395),
    ("City of Lompoc", 7396), ("City of Moreno Valley", 7314),
    ("City of Needles", 7397), ("City of Palo Alto Utilities", 7398),
    ("City of Pasadena", 7399), ("City of Rancho Cucamonga", 7400),
    ("City of Redding Electric Utility", 7401),
    ("City of Riverside, Riverside Public Utilities", 7402),
    ("City of Roseville", 7403), ("City of Santa Clara dba Silicon Valley Power", 7404),
    ("City of Shasta Lake", 7405), ("City of Ukiah Electric Utility", 7406),
    ("Clean Energy Alliance", 7408), ("Clean Power Alliance of Southern California", 7409),
    ("CleanPowerSF", 7407), ("Commercial Energy of California", 7410),
    ("Constellation NewEnergy, Inc.", 7260), ("Desert Community Energy", 7261),
    ("Direct Energy Business, LLC", 7262), ("Eastside Power Authority", 7263),
    ("Energy for Palmdales Independent Choice", 7264), ("Glendale Water and Power", 7265),
    ("Imperial Irrigation District", 7266), ("Industry Public Utilities", 7267),
    ("King City Community Power", 7268), ("Kirkwood Meadows Public Utility District", 7269),
    ("Lancaster Choice Energy", 7270), ("Lassen Municipal Utility District", 7271),
    ("Lathrop Irrigation District", 7272), ("Liberty Utilities (CalPeco Electric), LLC", 7273),
    ("Lodi Electric Utility", 7274), ("Los Angeles Department of Water and Power", 7275),
    ("MCE", 7276), ("Merced Irrigation District", 7277),
    ("Modesto Irrigation District", 7278), ("Orange County Power Authority", 7279),
    ("Pacific Gas and Electric Company", 7281), ("PacifiCorp", 7280),
    ("Peninsula Clean Energy Authority", 7282), ("Pico Rivera Innovative Municipal Energy", 7283),
    ("Pilot Power Group, LLC", 7284), ("Pioneer Community Energy", 7285),
    ("Pittsburg Power Company", 7286), ("Plumas Sierra Rural Electric Cooperative", 7287),
    ("Pomona Choice Energy", 7288), ("Port of Oakland", 7289),
    ("Power & Water Resources Pooling Authority", 7290), ("Rancho Mirage Energy Authority", 7291),
    ("Redwood Coast Energy Authority", 7292), ("Sacramento Municipal Utility District", 7293),
    ("San Diego Community Power", 7295), ("San Diego Gas & Electric", 7354),
    ("San Francisco Bay Area Rapid Transit District (BART)", 7355),
    ("San Jacinto Power", 7356), ("San José Clean Energy", 7357),
    ("Santa Barbara Clean Energy", 7353), ("SFPUC - Hetch Hetchy Power", 7358),
    ("Shell Energy North America (US), L.P. dba Shell Energy Solutions", 7359),
    ("Silicon Valley Clean Energy", 7360), ("Sonoma Clean Power Authority", 7361),
    ("Southern California Edison Company", 7362), ("Stockton Port Authority", 7363),
    ("Surprise Valley Electrification Corp.", 7364),
    ("The Regents of the University of California", 7365),
    ("Truckee Donner Public Utility", 7366), ("Turlock Irrigation District", 7367),
    ("Valley Clean Energy Alliance", 7368), ("Valley Electric Association, Inc.", 7369),
    ("Vernon Public Utilities", 7370), ("Victorville Municpal Utility Services", 7371),
]

# Label resource rows -> our CSV columns, in label order.
ROW_MAP = [
    ("Eligible Renewable", "pct_eligible_renewable"),
    ("Biomass & Biowaste", "pct_biomass"),
    ("Geothermal", "pct_geothermal"),
    ("Eligible Hydroelectric", "pct_small_hydro"),
    ("Solar", "pct_solar"),
    ("Wind", "pct_wind"),
    ("Coal", "pct_coal"),
    ("Large Hydroelectric", "pct_large_hydro"),
    ("Natural Gas", "pct_natural_gas"),
    ("Nuclear", "pct_nuclear"),
    ("Other", "pct_other"),
    ("Unspecified Power", "pct_unspecified"),
]

# Product names in COLUMN ORDER for labels whose chart legend defeats the
# generic walker (names wrap lines / legend order differs). Read off each
# label's table header by hand — see the session notes in git history.
PRODUCT_OVERRIDES = {
    7225: ["3PR 100% Renewable Product", "3PR 50% Renewable Product", "3PR Minimum Product"],
    7227: ["Anaheim", "Green Power Program"],
    7229: ["Core Choice", "More Choice"],
    7230: ["Renewable 100", "Bright Choice"],
    7234: ["Standard", "Green Choice Program"],
    7237: ["3Cchoice", "3Cprime"],
    7261: ["DCE Carbon Free", "DCE Desert Saver"],
    7264: ["EPIC Power", "EPIC Power100"],
    7270: ["Clear Choice", "Smart Choice"],
    7275: ["LADWP Power Mix", "Green Power for Green LA"],
    7279: ["Basic Choice", "Smart Choice", "100% Renewable Choice"],
    7280: ["Standard (Default) Electricity", "BlueSky Portfolio"],
    7283: ["PRIME Power", "PRIME Future"],
    7285: ["Base Service", "Green100"],
    7288: ["Pomona Choice", "Pomona Choice 100"],
    7290: ["Standard Water Portfolio", "Zero Carbon Water Portfolio"],
    7291: ["Base Choice", "Premium Renewable Choice"],
    7292: ["REpower", "REpower+"],
    7295: ["PowerOn", "Power100"],
    7353: ["SBCE 100% Green", "SBCE Green Start"],
    7356: ["PrimePower", "PureGreen"],
    7358: ["General Service", "Premium Service"],
    7361: ["CleanStart", "EverGreen"],
    7367: ["Retail Power Supply", "BGreen Power"],
    7395: ["Standard Rate", "Green Rate"],
    7398: ["Palo Alto Green", "CPAU Standard Rate"],
    7399: ["PWP Power Mix", "Green Program Mix"],
    7402: ["RPU General Power Mix", "RPU 100% Renewable"],
    7403: ["Roseville", "Roseville Community Solar"],
    7408: ["Clean Impact Plus", "Clean Impact", "Green Impact"],
    7404: ["Residential", "Non-Residential", "Palo Alto Networks",
           "Roche Molecular Systems", "ServiceNow"],
    7410: ["Standard", "Renewable"],
}


def slug(name):
    s = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    return s[:40]


def download_all():
    CACHE.mkdir(parents=True, exist_ok=True)
    for name, fid in SUPPLIERS:
        p = CACHE / f"{fid}.pdf"
        if p.exists() and p.stat().st_size > 10000:
            continue
        req = urllib.request.Request(BASE + str(fid), headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            p.write_bytes(r.read())
        print(f"  fetched {name} ({p.stat().st_size:,}b)")
        time.sleep(0.4)


PCT = r"(-?\d+(?:\.\d+)?)\s*%"

def parse_label(path, name, fid=None):
    """Returns (products, {col: [values per product]}, [intensities]) or raises."""
    text = "\n".join(page.extract_text() for page in PdfReader(path).pages)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Resource rows appear in template order, but PDF extraction can splice the
    # vertical GHG-intensity column into the middle of a row's line (e.g.
    # "Biomass 23.1% 2.1% \n105 373 Geothermal 34.8% ..."), so line-anchored
    # matching drops rows. Instead: walk the joined text with sequential
    # anchors — each row's values are the percentages between its label and the
    # next row's label. Bare integers in a segment (the intensity column) are
    # ignored by the % regex and recovered separately below.
    joined_all = re.sub(r"\s+", " ", text)
    rows = {}
    pos = joined_all.find("Energy Resources")
    pos = pos if pos >= 0 else 0
    anchors = [(label, col) for label, col in ROW_MAP] + [("TOTAL", None)]
    starts = []
    for label, col in anchors:
        i = joined_all.find(label, pos)
        if i < 0:
            starts.append(None)
            continue
        starts.append(i)
        pos = i + len(label)
    for k, (label, col) in enumerate(ROW_MAP):
        if starts[k] is None:
            continue
        nxt = next((s for s in starts[k + 1:] if s is not None), len(joined_all))
        seg = joined_all[starts[k] + len(label):nxt]
        vals = [float(v) for v in re.findall(PCT, seg)]
        if vals:
            rows[col] = vals
    if not rows:
        # Fallback for the two hand-made single-product layouts (Corona, VEA):
        # resource names and values are separated by the extractor, but the
        # "own% camix%" pairs still stream in template row order, and the CA-mix
        # column is a fingerprint we can verify against the standard label.
        pairs = re.findall(rf"{PCT}\s*{PCT}", text)
        vals = [(float(a), float(b)) for a, b in pairs]
        ca_mix = [36.9, 2.1, 4.8, 1.8, 17.0, 11.2, 1.8, 11.7, 36.6, 9.3, 0.1, 3.7]
        if len(vals) >= 12 and all(abs(vals[i][1] - ca_mix[i]) < 0.2 for i in range(12)):
            rows = {col: [vals[i][0], vals[i][1]] for i, (_, col) in enumerate(ROW_MAP)}
    counts = {len(v) for v in rows.values()}
    if len(counts) != 1:
        raise ValueError(f"inconsistent row widths {sorted(counts)} in {rows.keys()}")
    ncols = counts.pop()
    nprod = ncols - 1                      # last column = 2023 CA Power Mix
    if nprod < 1:
        raise ValueError("no product columns")

    # GHG intensity run: nprod+1 bare integers ending in the CA average (373).
    # Prefer the table region (before TOTAL); the odd hand-made layouts stream
    # values after TOTAL, so fall back to the whole text — the 373 checksum
    # keeps chart-axis numbers from matching.
    intensities = None
    pat = r"(?<![\d.%])((?:-?\d{1,4} ){" + str(nprod) + r"}-?\d{1,4})(?![\d.%])"
    for region in (text.split("TOTAL")[0], text):
        joined = re.sub(r"[ \n]+", " ", region).replace(",", "")
        for m in re.finditer(pat, joined):
            nums = [int(x) for x in m.group(1).split()]
            if nums[-1] == CA_AVG_INTENSITY:
                intensities = nums[:-1]
                break
        if intensities is not None:
            break
    if intensities is None:
        raise ValueError(f"no intensity run of {nprod}+1 ints ending {CA_AVG_INTENSITY}")

    # Product names: single-product labels are all just the supplier's standard
    # offering — the header text is the utility's own name more often than a
    # product name, so normalize. Multi-product labels: hand-curated override
    # first (column order read off each label's table header), else the
    # chart-legend lines right before the LAST "CA Utility Average".
    if nprod == 1:
        return ["Standard"], {c: v[:1] for c, v in rows.items()}, intensities
    if fid in PRODUCT_OVERRIDES:
        prods = PRODUCT_OVERRIDES[fid]
        if len(prods) != nprod:
            raise ValueError(f"override has {len(prods)} names, label has {nprod} products")
        return prods, {c: v[:nprod] for c, v in rows.items()}, intensities
    prods = None
    for i in range(len(lines) - 1, -1, -1):
        if "CA Utility Average" in lines[i]:
            cand, j = [], i - 1
            while j >= 0 and len(cand) < nprod:
                ln = lines[j]
                if re.fullmatch(r"[\d,. ]+", ln):   # chart axis numbers
                    j -= 1
                    continue
                if "CA Utility Average" in ln or "Saver" == ln.split()[-1] and False:
                    break
                cand.append(ln)
                j -= 1
            if len(cand) == nprod:
                prods = list(reversed(cand))
            break
    if prods is None:
        prods = [f"Product {i+1}" for i in range(nprod)]

    return prods, {c: v[:nprod] for c, v in rows.items()}, intensities


def build():
    download_all()
    cols = [c for _, c in ROW_MAP]
    ok, failed = 0, []
    with open(OUT, "w", newline="") as f:
        f.write("# Source: CEC 2023 Power Content Labels (Power Source Disclosure program),\n")
        f.write("# parsed from the per-supplier PDFs at energy.ca.gov (annual-power-4 listing)\n")
        f.write("# by cec_labels.py. Intensity checksum: each parse must end at the 2023 CA\n")
        f.write("# utility average (373 lbs CO2e/MWh) or the label is rejected.\n")
        f.write("# basis_denominator=retail_sales: PSD intensity = portfolio emissions / annual\n")
        f.write("# retail sales, so T&D losses are already in the denominator (no gross-up).\n")
        w = csv.writer(f)
        w.writerow(["psd_year", "state", "supplier_id", "supplier_name", "product",
                    "ghg_intensity_lb_co2e_per_mwh", "basis_denominator"] + cols
                   + ["data_quality"])
        for name, fid in SUPPLIERS:
            try:
                prods, rows, intens = parse_label(CACHE / f"{fid}.pdf", name, fid)
            except Exception as e:
                failed.append((name, fid, str(e)))
                continue
            for i, prod in enumerate(prods):
                vals = {c: (rows[c][i] if c in rows else 0.0) for c in cols}
                # Label quirk: "Roseville Community Solar" reports 100% eligible
                # renewable with every subcategory zero. It is a solar product.
                if slug(name) == "CITY_OF_ROSEVILLE" and prod == "Roseville Community Solar":
                    vals["pct_solar"] = 100.0
                w.writerow([PSD_YEAR, "CA", slug(name), name, prod, intens[i],
                            "retail_sales"] + [vals[c] for c in cols]
                           + ["PUBLISHED"])
                ok += 1
    print(f"cec_psd_supplier_factors.csv: {ok} product rows from {len(SUPPLIERS) - len(failed)} suppliers")
    for name, fid, err in failed:
        print(f"  FAILED {name} ({fid}): {err}")


if __name__ == "__main__":
    build()
