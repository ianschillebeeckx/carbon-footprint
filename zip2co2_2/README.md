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
