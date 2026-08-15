"""
Populates data/ with real values. The CSVs shipped alongside this are SEED files with
illustrative numbers - they let the module run end-to-end but must not be used for output.

I could not fetch these for you: the sandbox this was written in only reaches package
registries, not epa.gov or energy.ca.gov. Run this locally.

None of these publishers offer a clean API, so each loader below is a manual step with the
exact file and sheet identified. Budget an hour, mostly on the CCA territory table.
"""

SOURCES = {
    "zip_subregion.csv": {
        "url": "https://www.epa.gov/egrid/power-profiler",
        "file": "Power Profiler Zipcode Tool (Excel), linked from the eGRID page",
        "notes": [
            "Sheet has ZIP + up to 3 subregion columns. ~33,000 rows.",
            "Read ZIP as string - leading zeros in New England will be eaten by pandas.",
            "Confirm the release year matches the eGRID release you use for factors.",
        ],
    },
    "egrid_subregion_factors.csv": {
        "url": "https://www.epa.gov/egrid/download-data",
        "file": "eGRID2023 Summary Tables (xlsx) - easier than the full data workbook",
        "notes": [
            "Table 'Subregion Output Emission Rates - Greenhouse Gases': take the ANNUAL "
            "TOTAL OUTPUT CO2e column, not non-baseload.",
            "CO2 is lb/MWh but CH4 and N2O are lb/GWh. Off-by-1000 is the classic bug here; "
            "use EPA's own CO2e column and avoid recomputing.",
            "Table 'Grid Gross Loss (%)' for grid_gross_loss_pct.",
            "Table 'Subregion Resource Mix' for the pct_* columns.",
            "Record the GWP set from the Technical Guide into the gwp_set column.",
        ],
    },
    "greene_residual_mix.csv": {
        "url": "https://resource-solutions.org/learn/residual-mix/",
        "file": "Annual U.S. Residual Mix Emissions Rates (PDF/xlsx)",
        "notes": [
            "Check whether the release reports CO2 or CO2e and set includes_ch4_n2o "
            "accordingly - the module rescales when FALSE.",
            "Lags eGRID by ~1 year. Set source_egrid_vintage so mismatches are visible.",
        ],
    },
    "cec_psd_supplier_factors.csv": {
        "url": "https://www.energy.ca.gov/programs-and-topics/programs/power-source-disclosure",
        "file": "Power Content Label annual report data tables",
        "notes": [
            "One row per retail supplier PER PRODUCT. Opt-up products are separate rows and "
            "have wildly different factors than the default product.",
            "VERIFY basis_denominator against the methodology doc. Everything about whether "
            "the loss gross-up double-counts hinges on this.",
            "Published ~Oct for the prior calendar year.",
            "Other disclosure states with comparable data: MA (DPU), IL (ICC), NY (PSC), "
            "CT (PURA), NJ (BPU). Quality and format vary a lot; CA is the best.",
        ],
    },
    "zip_supplier_candidates.csv": {
        "url": "https://data.openei.org/submissions/8563",
        "file": "NREL 'U.S. Electric Utility Companies and Rates: Look-up by Zip Code (2024)'",
        "notes": [
            "This gives you the WIRES utility only. It will return PG&E for every SF ZIP.",
            "The CCA layer has no national source. Build it from CalCCA's member list plus "
            "each CCA's published service territory. This is the hand-curation step.",
            "is_default should be TRUE for the CCA in CCA territory - customers are "
            "auto-enrolled and must opt out. Getting this backwards inverts your fallback.",
            "enrollment_share: CCA opt-out rates run roughly 5-15%. Ask the user instead of "
            "guessing wherever the UI allows it.",
        ],
    },
    "upstream_fuel_cycle_factors.csv": {
        "url": "https://www.ipcc.ch/site/assets/uploads/2018/02/ipcc_wg3_ar5_annex-iii.pdf",
        "file": "IPCC AR5 WG3 Annex III, Table A.III.2",
        "notes": [
            "Alternative: USEEIO's electricity satellite table, which would give you strict "
            "boundary consistency with your spend-based NAICS categories at the cost of "
            "much coarser resolution.",
            "Alternative: Argonne GREET, better for the gas supply chain specifically.",
            "Do not mix sources across fuels - the harmonization assumptions differ.",
        ],
    },
}

if __name__ == "__main__":
    for fname, s in SOURCES.items():
        print(f"\n=== {fname} ===")
        print(f"  {s['url']}")
        print(f"  file: {s['file']}")
        for n in s["notes"]:
            print(f"   - {n}")
