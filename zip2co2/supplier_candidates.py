"""Generate data/zip_supplier_candidates.csv — CA ZIP -> retail supplier(s).

There is no authoritative national ZIP->retail-supplier table (fetch_data.py's
warning holds). This build combines:

  1. EPA Power Profiler crosswalk (cache/power_profiler_zip.csv): per-ZIP WIRES
     utility with a predominant flag. Munis with a Power Content Label become
     supplier candidates directly (a muni's wires customer is its retail
     customer). IOU rows (PG&E / SCE / SDG&E) are where CCAs can exist.
  2. GeoNames US.zip (cache/US.txt): ZIP -> primary place + county, which the
     hand-curated CCA territory spec below is written against.
  3. CCA_SPEC: 2023 service territories per CCA — county grain with city
     include/exclude lists, assembled from CCA/CalCCA published territories.
     CURATED_APPROXIMATE by nature: county-grain over-offers a CCA to non-member
     cities in partially-covered counties (the UI's supplier picker is the
     correction path), and unincorporated communities resolve only when
     GeoNames names them.

Default rule: in CCA territory the CCA is the default (auto-enrollment,
opt-out) — is_default=TRUE on the CCA row, FALSE on the IOU bundled row.
enrollment_share is a rough CalCCA-based split (opt-out runs 5-15%): 0.88/0.12.
Special case: San Francisco's wires rows say "City & County of San Francisco";
residential default there is CleanPowerSF on PG&E wires, with SFPUC Hetch
Hetchy serving municipal accounts.

Run:  .venv/bin/python zip2co2/supplier_candidates.py
"""

import csv
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "cache"
OUT = HERE / "data" / "zip_supplier_candidates.csv"

IOU = {
    "Pacific Gas & Electric Co.": "PACIFIC_GAS_AND_ELECTRIC_COMPANY",
    "Pacific Gas & Electric Co": "PACIFIC_GAS_AND_ELECTRIC_COMPANY",
    "Southern California Edison Co": "SOUTHERN_CALIFORNIA_EDISON_COMPANY",
    "San Diego Gas & Electric Co": "SAN_DIEGO_GAS_ELECTRIC",
    # Escondido runs water, not electricity — EPA's row is an artifact; SDG&E
    # wires + Clean Energy Alliance generation serve the city.
    "City of Escondido - (CA)": "SAN_DIEGO_GAS_ELECTRIC",
}

# Wires utility (EPA naming) -> PCL supplier slug for munis/co-ops/districts.
MUNI = {
    "Los Angeles Department of Water & Power": "LOS_ANGELES_DEPARTMENT_OF_WATER_AND_POWE",
    "Sacramento Municipal Util Dist": "SACRAMENTO_MUNICIPAL_UTILITY_DISTRICT",
    "City of Pasadena - (CA)": "CITY_OF_PASADENA",
    "City of Anaheim - (CA)": "ANAHEIM_PUBLIC_UTILITIES",
    "City of Glendale - (CA)": "GLENDALE_WATER_AND_POWER",
    "City of Burbank Water and Power": "BURBANK_WATER_AND_POWER",
    "City of Riverside - (CA)": "CITY_OF_RIVERSIDE_RIVERSIDE_PUBLIC_UTILI",
    "City of Santa Clara - (CA)": "CITY_OF_SANTA_CLARA_DBA_SILICON_VALLEY_P",
    "City of Palo Alto - (CA)": "CITY_OF_PALO_ALTO_UTILITIES",
    "Alameda Municipal Power": "ALAMEDA_MUNICIPAL_POWER",
    "City of Roseville - (CA)": "CITY_OF_ROSEVILLE",
    "Imperial Irrigation District": "IMPERIAL_IRRIGATION_DISTRICT",
    "Modesto Irrigation District": "MODESTO_IRRIGATION_DISTRICT",
    "Turlock Irrigation District": "TURLOCK_IRRIGATION_DISTRICT",
    "Merced Irrigation District": "MERCED_IRRIGATION_DISTRICT",
    "PacifiCorp": "PACIFICORP",
    "Liberty Utilities": "LIBERTY_UTILITIES_CALPECO_ELECTRIC_LLC",
    "Surprise Valley Electrification": "SURPRISE_VALLEY_ELECTRIFICATION_CORP",
    "City of Colton - (CA)": "CITY_OF_COLTON_ELECTRIC_UTILITY",
    "City of Moreno Valley - (CA)": "CITY_OF_MORENO_VALLEY",
    "City of Banning - (CA)": "CITY_OF_BANNING_ELECTRIC_UTILITY",
    "City of Azusa - (CA)": "AZUSA_LIGHT_AND_WATER",
    "City of Vernon - (CA)": "VERNON_PUBLIC_UTILITIES",
    "City of Cerritos - (CA)": "CERRITOS_ELECTRIC_UTILITY",
    "City of Lodi - (CA)": "LODI_ELECTRIC_UTILITY",
    "City of Lompoc - (CA)": "CITY_OF_LOMPOC",
    "City of Healdsburg - (CA)": "CITY_OF_HEALDSBURG",
    "City of Ukiah - (CA)": "CITY_OF_UKIAH_ELECTRIC_UTILITY",
    "City of Redding - (CA)": "CITY_OF_REDDING_ELECTRIC_UTILITY",
    "City of Shasta Lake - (CA)": "CITY_OF_SHASTA_LAKE",
    "City of Corona - (CA)": "CITY_OF_CORONA_UTILITIES_DEPARTMENT",
    "City of Needles - (CA)": "CITY_OF_NEEDLES",
    "City of Gridley - (CA)": "CITY_OF_GRIDLEY_ELECTRIC_UTILITY",
    "City of Biggs - (CA)": "CITY_OF_BIGGS_ELECTRIC_UTILITY",
    "City of Rancho Cucamonga - (CA)": "CITY_OF_RANCHO_CUCAMONGA",
    "City of Victorville - (CA)": "VICTORVILLE_MUNICPAL_UTILITY_SERVICES",
    "City of Industry - (CA)": "INDUSTRY_PUBLIC_UTILITIES",
    "Anza Electric Coop Inc": "ANZA_ELECTRIC_COOPERATIVE_INC",
    "Bear Valley Electric Service": "BEAR_VALLEY_ELECTRIC_SERVICE_INC",
    "Truckee Donner P U D": "TRUCKEE_DONNER_PUBLIC_UTILITY",
    "Plumas-Sierra Rural Elec Coop": "PLUMAS_SIERRA_RURAL_ELECTRIC_COOPERATIVE",
    "Lassen Municipal Utility Dist": "LASSEN_MUNICIPAL_UTILITY_DISTRICT",
    "Kirkwood Meadows Pub Util Dist": "KIRKWOOD_MEADOWS_PUBLIC_UTILITY_DISTRICT",
}

SF_WIRES = "City & County of San Francisco"

# CCA territories as of reporting year 2023. counties = whole-county coverage
# (munis are carved out automatically because their wires utility is not an
# IOU); cities/exclude refine partially-covered counties.
CCA_SPEC = [
    ("CLEANPOWERSF", "Green", {"counties": ["San Francisco"]}),
    ("MCE", "2023 MCE Light Green Power Mix",
     {"counties": ["Marin", "Napa"],
      "cities": ["Richmond", "San Pablo", "El Cerrito", "Pinole", "Hercules",
                 "Martinez", "Concord", "Pittsburg", "Oakley", "Moraga",
                 "Lafayette", "Danville", "Walnut Creek", "San Ramon",
                 "Benicia", "Vallejo"]}),
    ("AVA_COMMUNITY_ENERGY", "Bright Choice",
     {"counties": ["Alameda"], "cities": ["Tracy"]}),
    ("PENINSULA_CLEAN_ENERGY_AUTHORITY", "ECOplus",
     {"counties": ["San Mateo"], "cities": ["Los Banos"]}),
    ("SILICON_VALLEY_CLEAN_ENERGY", "GreenStart",
     {"counties": ["Santa Clara"], "exclude": ["San Jose"]}),
    ("SAN_JOS_CLEAN_ENERGY", "GreenSource", {"cities": ["San Jose"]}),
    ("SONOMA_CLEAN_POWER_AUTHORITY", "CleanStart",
     {"counties": ["Sonoma", "Mendocino"]}),
    ("REDWOOD_COAST_ENERGY_AUTHORITY", "REpower", {"counties": ["Humboldt"]}),
    ("VALLEY_CLEAN_ENERGY_ALLIANCE", "Standard Green",
     {"counties": ["Yolo"], "exclude": ["West Sacramento"]}),
    ("PIONEER_COMMUNITY_ENERGY", "Base Service",
     {"counties": ["Placer", "El Dorado"]}),
    ("CENTRAL_COAST_COMMUNITY_ENERGY", "3Cchoice",
     {"counties": ["Monterey", "San Benito", "Santa Cruz", "San Luis Obispo",
                   "Santa Barbara"],
      "exclude": ["Santa Barbara"]}),
    ("SANTA_BARBARA_CLEAN_ENERGY", "SBCE Green Start", {"cities": ["Santa Barbara"]}),
    ("CLEAN_POWER_ALLIANCE_OF_SOUTHERN_CALIFOR", "Clean Power",
     {"counties": ["Ventura"],
      "cities": ["Agoura Hills", "Alhambra", "Arcadia", "Beverly Hills",
                 "Calabasas", "Carson", "Claremont", "Culver City", "Downey",
                 "Hawaiian Gardens", "Hawthorne", "Malibu", "Manhattan Beach",
                 "Redondo Beach", "Rolling Hills Estates", "Santa Monica",
                 "Sierra Madre", "South Pasadena", "Temple City",
                 "West Hollywood", "Whittier", "Altadena", "Topanga"]}),
    ("SAN_DIEGO_COMMUNITY_POWER", "PowerOn",
     {"cities": ["San Diego", "Chula Vista", "Encinitas", "La Mesa",
                 "Imperial Beach", "National City"]}),
    ("CLEAN_ENERGY_ALLIANCE", "Clean Impact",
     {"cities": ["Carlsbad", "Del Mar", "Solana Beach", "Escondido",
                 "San Marcos", "Oceanside", "Vista"]}),
    ("ORANGE_COUNTY_POWER_AUTHORITY", "Smart Choice",
     {"cities": ["Irvine", "Huntington Beach", "Fullerton", "Buena Park"]}),
    ("LANCASTER_CHOICE_ENERGY", "Clear Choice", {"cities": ["Lancaster"]}),
    ("APPLE_VALLEY_CHOICE_ENERGY", "Core Choice", {"cities": ["Apple Valley"]}),
    ("POMONA_CHOICE_ENERGY", "Pomona Choice", {"cities": ["Pomona"]}),
    ("RANCHO_MIRAGE_ENERGY_AUTHORITY", "Base Choice", {"cities": ["Rancho Mirage"]}),
    ("DESERT_COMMUNITY_ENERGY", "DCE Carbon Free", {"cities": ["Palm Springs"]}),
    ("SAN_JACINTO_POWER", "PrimePower", {"cities": ["San Jacinto"]}),
    ("PICO_RIVERA_INNOVATIVE_MUNICIPAL_ENERGY", "PRIME Power",
     {"cities": ["Pico Rivera"]}),
    ("ENERGY_FOR_PALMDALES_INDEPENDENT_CHOICE", "EPIC Power",
     {"cities": ["Palmdale"]}),
    ("KING_CITY_COMMUNITY_POWER", "Standard", {"cities": ["King City"]}),
]

# Default (standard) product per non-CCA supplier; single-product labels are
# all "Standard" so only multi-product suppliers need entries.
DEFAULT_PRODUCT = {
    "PACIFIC_GAS_AND_ELECTRIC_COMPANY": "Base Plan",
    "SOUTHERN_CALIFORNIA_EDISON_COMPANY": "SCE Power Mix",
    "SAN_DIEGO_GAS_ELECTRIC": "Standard",
    "LOS_ANGELES_DEPARTMENT_OF_WATER_AND_POWE": "LADWP Power Mix",
    "SACRAMENTO_MUNICIPAL_UTILITY_DISTRICT": "SMUD General Mix",
    "ANAHEIM_PUBLIC_UTILITIES": "Anaheim",
    "BURBANK_WATER_AND_POWER": "Standard",
    "CITY_OF_PASADENA": "PWP Power Mix",
    "CITY_OF_RIVERSIDE_RIVERSIDE_PUBLIC_UTILI": "RPU General Power Mix",
    "CITY_OF_ROSEVILLE": "Roseville",
    "CITY_OF_SANTA_CLARA_DBA_SILICON_VALLEY_P": "Residential",
    "CITY_OF_PALO_ALTO_UTILITIES": "CPAU Standard Rate",
    "CITY_OF_HEALDSBURG": "Standard Rate",
    "TURLOCK_IRRIGATION_DISTRICT": "Retail Power Supply",
    "PACIFICORP": "Standard (Default) Electricity",
    "SFPUC_HETCH_HETCHY_POWER": "General Service",
}


def load_geonames():
    zips = {}
    for ln in (CACHE / "US.txt").read_text().splitlines():
        f = ln.split("\t")
        if len(f) > 6 and f[4] == "CA":
            zips[f[1]] = (f[2], f[5])   # zip -> (place, county)
    return zips


def ccas_for(place, county):
    out = []
    for slug, default, spec in CCA_SPEC:
        if place in spec.get("exclude", []):
            continue
        if county in spec.get("counties", []) or place in spec.get("cities", []):
            out.append((slug, default))
    return out


def build():
    geo = load_geonames()
    epa = {}
    for r in csv.DictReader(open(CACHE / "power_profiler_zip.csv", encoding="utf-8-sig")):
        if r["state"] == "CA":
            epa.setdefault(r["zip"].zfill(5), []).append(r)

    n = 0
    with open(OUT, "w", newline="") as f:
        f.write("# Generated by supplier_candidates.py — EPA wires crosswalk + GeoNames +\n")
        f.write("# hand-curated 2023 CCA territories. County-grain CCA coverage over-offers in\n")
        f.write("# partially-covered counties; the supplier picker in the UI is the correction.\n")
        f.write("# is_default: TRUE = supplier a customer has with no action (CCA in CCA land).\n")
        w = csv.writer(f)
        w.writerow(["zip", "state", "supplier_id", "default_product", "is_default",
                    "enrollment_share", "notes", "data_quality"])
        for z in sorted(epa):
            place, county = geo.get(z, ("", ""))
            rows = []      # (slug, product, default, share, note)
            seen = set()
            for r in sorted(epa[z], key=lambda r: r.get("Predominant Utility") != "1"):
                u = r["UtilName"]
                pred = r.get("Predominant Utility") == "1"
                if u == SF_WIRES:
                    rows += [("CLEANPOWERSF", "Green", True, 0.85,
                              "SF default CCA on PG&E wires"),
                             ("PACIFIC_GAS_AND_ELECTRIC_COMPANY", "Base Plan", False,
                              0.12, "opt-out bundled"),
                             ("SFPUC_HETCH_HETCHY_POWER", "General Service", False,
                              0.03, "municipal accounts")]
                elif u in IOU:
                    ccas = ccas_for(place, county)
                    share_iou = 0.12 if ccas else 1.0
                    for slug, default in ccas:
                        if slug not in seen:
                            rows.append((slug, default, pred, 0.88 / len(ccas),
                                         "CCA default territory"))
                            seen.add(slug)
                    if IOU[u] not in seen:
                        rows.append((IOU[u], DEFAULT_PRODUCT.get(IOU[u], "Standard"),
                                     pred and not ccas, share_iou,
                                     "bundled IOU" if ccas else ""))
                        seen.add(IOU[u])
                elif u in MUNI:
                    if MUNI[u] not in seen:
                        rows.append((MUNI[u], DEFAULT_PRODUCT.get(MUNI[u], "Standard"),
                                     pred, 1.0, "municipal/co-op"))
                        seen.add(MUNI[u])
                # unmapped small utilities: no candidate row; resolver falls back
            defaults = [r for r in rows if r[2]]
            if rows and not defaults:      # ensure exactly one default
                rows[0] = (*rows[0][:2], True, *rows[0][3:])
            for slug, product, default, share, note in rows:
                w.writerow([z, "CA", slug, product, "TRUE" if default else "FALSE",
                            round(share, 3), note, "CURATED_APPROXIMATE"])
                n += 1
    print(f"zip_supplier_candidates.csv: {n} rows across {len(epa)} CA ZIPs")


if __name__ == "__main__":
    build()
