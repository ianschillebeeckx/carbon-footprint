# gridcarbon

`ZIP + annual kWh -> kg CO2e`, using hourly grid intensity weighted by a residential load shape.

```python
from gridcarbon import GridCarbon
gc = GridCarbon()

gc.estimate("94110", annual_kwh=4800)
# <1,305 kg CO2e/yr | 0.2719 kg/kWh | CISO | +4.3% vs flat>

gc.estimate("94110", annual_kwh=4800, monthly_kwh={1: 480, 2: 430, ...})   # better
gc.profile("94110").to_dict()                                              # for the UI
gc.compare("94110", 4800)                                                  # the explainer
```

## The math

```
1. RECONSTRUCT   Ihat_h = sum_f(G_fh * e_f) / sum_f(G_fh)     shape right, level wrong
2. CALIBRATE     alpha  = I_egrid_annual / genwmean(Ihat)     one scalar per BA
                 I_h    = alpha * Ihat_h
3. WEIGHT        Ibar   = sum_h(w_h * I_h)                    w_h unitless, sums to 1
                 E      = annual_kwh * Ibar
```

`w_h` contributes no kg and no kWh — it only decides which hours of `I_h` count more. The user
supplies scale; the library supplies shape.

`alpha` exists because fuel factors are national fleet averages. CISO's gas fleet is newer and
more CCGT-heavy than average, so the reconstruction is off by ~10% before calibration. eGRID
measured that BA's actual plants, so alpha absorbs the fuel-factor error, the `OTH` bucket
approximation, and the import assumption in one number — and guarantees annual totals reconcile
to what EPA publishes. **alpha outside 0.80–1.25 means a structural join error, not imprecision.**

## Data

| | source | resolution |
|---|---|---|
| `I_h` shape | EIA Hourly Electric Grid Monitor v2 | hourly, balancing authority |
| `I_h` level | eGRID BA sheet | annual, BA |
| `w_h` | OpenEI TMY3 residential load profiles | hourly |
| `e_f` | `fuel_factors.csv` (IPCC AR5 + EPA) | per fuel |

Location conditioning is **balancing authority**, reached via `zip -> utility -> BA`. BA is finer
than eGRID subregion — CAMX contains CISO, LDWP, BANC and IID, and LDWP is materially dirtier
than the CAMX average.

## Run

```bash
python make_synthetic_cache.py                   # fabricated data, structurally correct
python -m gridcarbon.sources --refresh --eia-key=...   # real data (free key from EIA)
python -m gridcarbon.build --year 2024
python demo.py
```

**Everything in `data/cache/` is synthetic.** Real values need the refresh.

> **This repo runs on real data.** The synthetic path above is kept for structure tests.
> The production build is: `make_real_cache.py` (EIA-930 2024 bulk files + eGRID2023 BA23
> sheet + TMY3 shapes) -> `make_zip_ba.py` (EPA Power Profiler x EIA-861 -> 39k ZIPs) ->
> `python -m gridcarbon.build --year 2024` -> `../scripts/build_gridcarbon_web.py`.

## Deviations from the library as originally specified

Documented here because each one changes shipped numbers; the *why* lives in code comments
at the referenced sites.

1. **Imports dropped — production-based intensity** (`gridcarbon/build.py::build_ba`,
   `use_imports=False`). The original reconstruction valued imports at CARB's 428 g CA-market
   default and calibrated against eGRID's production-based BA rate. On real 2024 data that
   mismatch pushed alpha out of its own sanity band for a third of BAs (CISO 0.65, PACW 0.39,
   BPAT 2.18) — and 428 g is indefensible outside CA (it would price Seattle's BPA-hydro
   imports as gas). Shipped intensity is in-BA generation only, the same basis as every
   standard use of eGRID location-based rates. Cost: import-heavy BAs are understated at the
   level (CISO ~10-15% vs consumption accounting); the import contribution to evening shape
   is lost (second-order — the in-BA gas ramp carries it).

2. **Mid-2024 EIA-930 schema change handled** (`make_real_cache.py::FUEL_COLS`). Jul-Dec 2024
   splits Solar/Wind by integrated-battery status (including EIA's typo'd
   `"Solar witho Integrated Battery Storage (Adjusted)"` column, which is real and carries
   data), breaks out Geothermal and Pumped Storage, and adds Battery/Energy Storage columns.
   Missing this zero-filled H2 solar — July noons parsed as 85% gas and flattened the annual
   diurnal curve. Both schemas map to canonical fuels and are summed.

3. **Storage split out of "Other"** (`make_real_cache.py`, the daily-min split). Some 930
   respondents — CAISO foremost — never populate the dedicated battery column; their batteries
   land in "Other Fuel Sources", cycling −5.5 GW at noon (charging) to +4.8 GW winter evenings
   (discharging). Priced at the gas-like Other factor, evening discharge of stored midday solar
   counted as gas. Per local day, the excess of Other above its daily minimum is reclassified
   to a `storage` fuel (0 combustion, 55 g lifecycle in `fuel_factors.csv`); flat fossil Other
   (waste-coal BAs) has no daily cycle and is untouched. Validation: CISO alpha 0.73 -> 0.97
   and the diurnal swing widened from 1.15x to 1.46x, with post-battery evenings (~235 g)
   now visibly cleaner than deep night (~279 g).

4. **TMY3 local-time alignment** (`make_real_cache.py::BA_UTC_OFFSET`). TMY3 hours are local;
   the cache index is UTC. Unrolled, the residential evening peak rendered (and weighted!) at
   midday. Load arrays are rolled by the BA's standard-time UTC offset before writing. DST
   (±1h) is deliberately ignored.

5. **Widened alpha shipping band + degradation policy**
   (`../scripts/build_gridcarbon_web.py`). The library's (0.80, 1.25) "structural error" band
   is kept as a warning, but shipping uses [0.70, 1.45] — values in the outer ring are fleet
   signal (Mountain-West 1970s coal genuinely emits above the 950 g national factor; that is
   what alpha is *for*), not join errors. A BA ships its hourly shape only if alpha is in
   band, |uplift| <= 12%, and >= 90% of hours reconstructed; otherwise it degrades to the flat
   eGRID+upstream level (EPA's measured number — level right, no diurnal claim). A 4.2% US
   grid-gross-loss factor converts busbar to delivered kWh.

## Alpha validation results (2024 data, 2023 eGRID)

Alpha doubles as a per-BA integrity check: |alpha − 1| measures how far EPA's measured rate
sits from our bottom-up reconstruction. **37/61 BAs within ±10%, 46/61 within ±20%.** All
major load centers tight: NYIS 1.00, PJM 1.02, CISO 0.97, ISNE 0.98, FPL 0.96, MISO 1.09,
ERCO 1.13, SWPP 1.13, LDWP 1.18.

Outliers, by class (all outside the gate degrade to flat eGRID level):

| class | BAs | diagnosis |
|---|---|---|
| 930-vs-eGRID plant assignment | PACW 6.18, BPAT 2.97, TEPC 1.62 | The 930 respondent's fuel mix and eGRID's plant-to-BA assignment disagree about which plants belong to the BA (BPA reports ~pure hydro; eGRID's BPAT set includes thermal). No calibration should paper over this. |
| Biomass accounting | GVL 1.87 | Deerhaven biomass: we count biomass combustion as 0 (biogenic-neutral, per `fuel_factors.csv`); eGRID measures the muni at 0.72 kg/kWh. |
| Dirty-fleet signal (ships fine) | WACM 1.49, NWMT 1.42, AZPS 1.34, SRP 1.28, EPE 1.28, PACE 1.26 | Mountain-West coal above the national 950 g average — genuine fleet deviation, exactly what alpha absorbs. WACM alone falls outside the gate. |
| Remaining OTH misjoin | IID 0.41 | Salton Sea geothermal is IID's flat "Other" baseload, priced gas-like; no daily cycle so the storage split (correctly) leaves it alone. A one-line IID other->geothermal remap would fix it; at ~180k residents it ships the flat eGRID level instead. |
| eGRID degenerate rates | CPLW 0.00, HST −0.03 | eGRID publishes 0.000 (hydro-only) and a negative rate (Homestead accounting oddity); alpha is meaningless and the rate is clipped >= 0 for the flat path. |

Re-run the ranking any time: sort `dist/summary.csv` by `abs(alpha - 1)`.

## Expectations

The uplift over flat-rate accounting is **a few percent**, not tens of percent. The identity

```
Ibar_load / Ibar_flat = 1 + rho * CV_L * CV_I
```

is printed alongside the computed result at build time as a consistency check; they won't match
exactly (one uses simple means, the other generation-weighted) but a large divergence means
something is wrong.

Two things this is *not* for:
- **Marginal decisions.** "Should I charge the EV at 2pm" needs marginal rates (WattTime MOER,
  Cambium LRMER), not this average. Flat for the stock, marginal for the flow.
- **Solar households.** Weight by net import `max(0, C_h - S_h)`, not consumption. That shape
  zeroes out the clean midday hours entirely and the uplift jumps to 30–50%.

TMY3 load shapes cannot capture synoptic covariance (heat wave = high load *and* high intensity
on the same day) because the synthetic year's hot days don't align with the real intensity
year's. You get diurnal and seasonal only. ResStock AMY runs fix this.

**The seasonal channel can dominate the diurnal one — and it is only as good as the assumed
seasonality.** On real 2024 data the single largest deviation from flat-rate accounting is
*negative* and almost entirely seasonal: PGE (Portland) comes out **−6.0%** with a nearly flat
diurnal curve (1.05x), because the hydro-backed grid is dirtiest in late summer (~500 g/kWh,
reservoirs low, gas filling in) and cleanest during spring runoff (~216 g), while Portland
homes peak in winter heating season and barely use power in the dirty months. The mirror case
is PNM (+10.7%), where Albuquerque AC load peaks exactly in the dirty summer months (seasonal
corr +0.72) *on top of* a 1.83x solar-day/fossil-night diurnal swing. The caveat: that
seasonal load pattern is TMY3's *typical* home, not the user's — an electrically-heated
Portland house peaks even harder in winter (bigger discount), a gas-heated one less. Passing
`monthly_kwh` from actual bills replaces the assumed seasonality with the real one; the
per-month factors are already built (`monthly_kg_per_kwh` in `dist/summary.csv`, `mo` in the
web dataset).
