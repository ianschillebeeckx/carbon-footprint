# Tier 2 electricity emission factors

`ZIP (+ optional supplier) -> kg CO2e / kWh delivered`, market-based where disclosure data
exists, with a documented degradation path to location-based where it doesn't.

## Files

| File | What it is |
|---|---|
| `tier2_factors.py` | The resolver. All six decisions documented in the module docstring. |
| `fetch_data.py` | Source URLs, sheet names, and gotchas — the documentation the builders below execute. |
| `build_data.py` | **Builder**: EPA Power Profiler crosswalk + eGRID2023 Summary Tables + CRS residual mix -> `data/zip_subregion.csv`, `data/zip_utility.csv`, `data/egrid_subregion_factors.csv`, `data/greene_residual_mix.csv`. GWP set fit empirically (AR5). |
| `cec_labels.py` | **Builder**: downloads + parses all 90 CEC 2023 Power Content Label PDFs -> `data/cec_psd_supplier_factors.csv` (155 supplier-product rows; 373 lb CA-average checksum on every parse). |
| `supplier_candidates.py` | **Builder**: EPA wires crosswalk + GeoNames + hand-curated 2023 CCA territories -> `data/zip_supplier_candidates.csv`. |
| `data/*.csv` | The populated data (2023 reporting year, `data_quality=PUBLISHED` / `CURATED_APPROXIMATE`). |
| `data/fuel_factors.csv` | Per fuel: upstream lifecycle, stack combustion, and three carbon-free definitions. IPCC AR5. |
| `cache/` | Raw downloads (gitignored; re-fetched by the builders). |

The original SEED files are gone: every row now carries `data_quality=PUBLISHED` (parsed
from the publication of record) or `CURATED_APPROXIMATE` (the hand-curated CCA territory
layer). `scripts/build_zip2co2_web.py` — which precomputes the webapp dataset
(`web/data/zip2co2.json`) by running this resolver — refuses to build if any
`SEED_APPROXIMATE` row reappears.

## Usage

```python
from tier2_factors import Tier2FactorResolver

r = Tier2FactorResolver(reporting_year=2023)
res = r.resolve("94110", supplier="CLEANPOWERSF", product="SuperGreen")

res.kg_co2e_per_kwh      # 0.0306
res.tier                 # "2-supplier"
res.combustion_kg_per_kwh, res.upstream_kg_per_kwh
res.confidence           # "high" / "medium" / "low"
res.warnings             # surface these; several are load-bearing

# carbon-free, reported alongside
res.pct_carbon_free                     # 100.0 (point, chosen definition)
res.pct_carbon_free_low, res.pct_carbon_free_high   # band from unspecified-power treatment
res.pct_by_definition   # {'strict': 100.0, 'conventional': 100.0, 'rps_ca': 100.0}
res.physical_pct_carbon_free            # 54.5 - location-based, what actually flowed
res.carbon_free_basis                   # "market" vs "location"
res.implied_kg_co2e_per_kwh             # cross-check: intensity recomputed from the mix
```

`carbon_free_definition` is `strict` | `conventional` | `rps_ca`. The last is the number on a
California bill and excludes large hydro *and* nuclear - PG&E scores ~95% conventional and
~38% rps_ca on the same portfolio. `unspecified_treatment` is `zero` | `residual` | `exclude`.

Instantiate once and reuse - the constructor reads six CSVs.

## Things to check before trusting output

1. `basis_denominator` in the CEC file. The loss gross-up decision hangs entirely on it.
2. GWP set consistency across eGRID / CEC / Green-e. AR4 vs AR5 moves CH4 25 -> 28.
3. `gas_leakage_sensitivity` sweep. Try 0.6 and 1.8 and see how much of your household total
   moves. For a gas-heavy grid it is not a rounding error.
4. Residual mix is only correct if you apply it to *every* non-claimant. Half-applying it is
   worse than not applying it.
5. `implied_kg_co2e_per_kwh` vs `combustion_kg_per_kwh`. A gap over 30% fires a warning and
   usually means a bad join or a mix/intensity vintage mismatch. Log it and watch the rate.

## Status: superseded for the webapp's electricity factor

The app now uses `zip2co2_2/` (gridcarbon): hourly EIA-930 grid intensity
weighted by a TMY3 residential load shape, physical grid average, no supplier
dimension. This module's data remains in use as inputs: `data/zip_utility.csv`
feeds the zip->BA crosswalk, `cache/eia861/` feeds the utility->BA join, and
the subregion factors serve as the flat fallback for ZIPs without hourly data
(via `web/data/zip2co2.json`, still built by `scripts/build_zip2co2_web.py`).
The CEC Power Content Label parser (`cec_labels.py`) is kept for reference —
market-based supplier factors were deliberately dropped from the footprint (the
wires deliver the shared physical mix regardless of plan).
