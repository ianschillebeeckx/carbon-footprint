# Assigning NAICS codes to transactions — design notes

Reference for the merchant → NAICS → kg CO2e layer of a spend-based footprint tool
built on EPA Supply Chain GHG Emission Factors v1.3.0.

Lookup table: **`naics2022_with_2017_emission_factors.csv`** — 1,109 rows, 14 columns,
no nulls. Columns 1–10 are as published by EPA plus the 2022 crosswalk; 11–14 are
derived classifications added for this tool (see §1).

| column | notes |
|---|---|
| `2022 NAICS Code` | join key for modern code lookups; **not unique** — 79 codes repeat |
| `2022 NAICS Title` | |
| `2017 NAICS Code` | the factors' native key; unique, 1,016 values |
| `2017 NAICS Title` | |
| `GHG` | always `All GHGs` |
| `Unit` | always `kg CO2e/2022 USD, purchaser price` |
| `Supply Chain Emission Factors without Margins` | production only (SEF) |
| `Margins of Supply Chain Emission Factors` | distribution only (MEF) |
| `Supply Chain Emission Factors with Margins` | **the default multiplier** (SEF+MEF) |
| `Reference USEEIO Code` | underlying USEEIO sector; many 6-digit codes share one |
| `sector_name` | *derived* — 2-digit sector label |
| `supply_chain_role` | *derived* — position in the value chain, 9 values |
| `output_type` | *derived* — `goods` / `services` / `construction` / `utilities` |
| `likely_household_purchase` | *derived, heuristic* — could a personal transaction land here |

---

## 0. What the factor actually represents

`kg CO2e / 2022 USD, purchaser price` — an economy-wide average emissions intensity
for one dollar of output from a NAICS commodity, derived from USEEIO
(BEA input-output tables × GHG satellite data, run through a Leontief inverse).

The three factor columns are additive and share the purchaser-price denominator:

| column | meaning |
|---|---|
| `...without Margins` | cradle-to-gate production, recursive through all upstream suppliers |
| `Margins of...` | factory gate to shelf: transport + wholesale + retail |
| `...with Margins` | what you apply to a purchaser-price transaction |

```
without Margins + Margins of == with Margins
```

holds exactly across all rows (residual ≤ 0.001, rounding). There is no producer-price
conversion to perform. Ignore any source claiming the without-margins column is per
producer-price dollar — the additivity disproves it.

`Reference USEEIO Code` is worth surfacing in logs: it tells you the real resolution of
the model. Distinct 6-digit codes sharing a USEEIO code carry identical factors, so
agonizing between them is wasted effort.

---

## 1. How the codes are organized

NAICS nests left to right, one digit per level. Every prefix is a valid aggregation:

```
3      Manufacturing                             sector
32     Chemical, plastics, nonmetallic mineral   subsector
325    Chemical Manufacturing                    industry group
3256   Soap, Cleaning Compound & Toilet Prep     industry
32561  Soap and Cleaning Compound Mfg            industry (US)
325611 Soap and Other Detergent Mfg              national industry
```

This makes prefix matching the natural implementation for category rules: `str.startswith('311')`
captures all food manufacturing, `('44','45')` all retail. Write rules against prefixes,
not enumerated code lists.

### Sector ranges encode supply-chain position

This is the structurally useful part. The 2-digit sector is not an arbitrary taxonomy —
it tells you where in the value chain a row sits, which tells you what its factor includes.

| sectors | position | goods or services? | rows | median `...with Margins` | margins? |
|---|---|---|---|---|---|
| 11, 21 | Raw extraction | **goods** (support activities → services) | 92 | 0.488 | yes (86%) |
| 22 | Utilities | *neither* — own bucket | 4 | 0.578 | no |
| 23 | Construction | *neither* — capital, own bucket | 31 | 0.221 | no |
| 31–33 | **Manufacturing** | **goods** | 359 | 0.256 | **yes (100%)** |
| 42 | Wholesale — *is a margin* | services | 71 | 0.115 | no |
| 44–45 | Retail — *is a margin* | services | 66 | 0.111 | no |
| 48–49 | Transport/warehouse — *is a margin* | services | 57 | 0.566 | no |
| 51–81 | Services | services (511*/512* carry margins anyway) | 336 | 0.103 | no (5%) |

A goods/services split is **not** "31–33 vs 51–81." That misses agriculture and mining
on the goods side and the three margin sectors on the services side. Two refinements
the sector range alone can't express:

- **13 rows in 11/21 are really services** — farm labor contractors, soil preparation,
  cotton ginning, oilfield support. Performed on site, nothing shipped, so
  `Margins of Supply Chain Emission Factors == 0`. The margin column classifies these
  correctly where a prefix rule does not.
- **17 rows in 511*/512* are services that carry margins**, modelling boxed software
  and physical media. Hardcode the exception.

So the best available discriminator is `Margins of Supply Chain Emission Factors > 0`,
with `511*`/`512*` forced to services — not the sector range.

Sectors 42, 44–45, and 48–49 **are** the three components of the margin. That is why a
manufacturing row's `Margins of Supply Chain Emission Factors` already contains the
store, the wholesaler, and the truck — and why adding a retail row on top double-counts
(§2). Their own margin column is zero because retail service *is* the margin.

Practical read:
- **11–33 → the thing itself.** Full production chain plus distribution. Map purchases here.
- **42, 44–45, 48–49 → the markup only.** Almost never a household line item on its own.
- **51–81 → services.** Bought directly from the producer, zero margin, low intensity
  (median 0.103 vs 0.235 for everything else).

### The 17 rows that break the pattern

Publishing (511*) and motion picture / sound recording (512*) are service sectors
carrying nonzero margins, because the 2017 model still assumes physical media —
boxed software, printed books, CDs. Software Publishers 511210 is the extreme: margin
0.045 exceeds production 0.036.

For direct-to-consumer digital purchases these margins are an artifact. Use the
without-margins column (§6).

### The derived columns

Four columns encode the above so you don't re-derive it per query.

`supply_chain_role` — where the row sits in the value chain:

| value | rows | what the factor prices |
|---|---|---|
| `manufacturing` | 360 | the finished good, full upstream chain |
| `raw_extraction` | 80 | the raw commodity |
| `support_services` | 13 | on-site services in ag/mining (cotton ginning, well drilling) |
| `construction` | 31 | structures |
| `utilities` | 4 | electricity, gas, water — no factor published, handled by overrides |
| `wholesale_margin` | 71 | markup only |
| `retail_margin` | 147 | markup only |
| `transport_margin` | 57 | freight and delivery |
| `services` | 346 | the service itself |

`output_type` — collapses the above to `goods` (440), `services` (634),
`construction` (31), `utilities` (4).

Note this is **not** a plain sector-range rule. Sectors 11 and 21 are split by
`Margins of Supply Chain Emission Factors > 0`, which correctly routes 13 support
activities — farm labor contractors, soil preparation, oilfield services — to
`services` where a naive `startswith('11','21')` would call them goods. The
publishing/media rows are handled the other way, staying `services` despite carrying
margins.

`likely_household_purchase` — heuristic boolean, true for 450 of 1,109 rows. Useful
for narrowing a merchant-matching search space, and as a warning flag: a mapping that
resolves to `False` is worth reviewing.

`sector_name` — 2-digit label, for grouping and display.

### Don't use output_type as the split for reporting

For a household goods-vs-services breakdown, splitting rows by `output_type` is the
wrong axis. A single goods purchase contains both — the trade and transport services
that delivered it are the margin column, a median 11.1% of a manufactured good's total.
The factor columns already give you the decomposition:

```python
goods_kg    = spend * row['Supply Chain Emission Factors without Margins']
services_kg = spend * row['Margins of Supply Chain Emission Factors']
# for output_type == 'services', route the whole thing to services_kg
```

Also worth questioning whether goods-vs-services is the right axis at all. It's an
industry-side split inherited from the IO tables. A consumption-function split — food,
housing, transport, goods, services, à la PCE/COICOP — is more actionable, since a
person can decide to eat less beef but not to "consume fewer goods."

### Household relevance

Roughly 40% of the table can plausibly receive a personal transaction. The rest are
intermediate industries (soybean farming, steel mills, merchant wholesalers) that no
household transacts with directly.

That does **not** mean the intermediate codes are unusable. Groceries route to `311xxx`
food manufacturing and `111xxx` agriculture precisely because the purchase is the food,
not the store — the `likely_household_purchase == False` codes are where most household
emissions actually live. The flag narrows merchant *matching*; it does not constrain
which factors you apply.

---

## 2. The central rule

**Map what was bought, not where it was bought.**

A grocery run is food commodities (311xxx), not `445110 Supermarkets`.
Detergent is `325611 Soap and Other Detergent Manufacturing`, not the store.

Retail rows (44*/45*) price the *retail markup only* — the store's HVAC, staff, and
parking lot. They know nothing about the goods on the shelf. Mapping a purchase to a
retail code undercounts by roughly 2x (detergent) to 5x (groceries).

Verification that the margin already includes the store: fluid milk's
`Margins of Supply Chain Emission Factors` = 0.037, grocery retail's
`...with Margins` = 0.186, ratio ≈ 20% — exactly the expected retail margin share of
grocery purchaser price. Same arithmetic across commodities:

| commodity (`2017 NAICS Code`) | `Margins of...` | implied margin share |
|---|---|---|
| Fluid milk 311511 | 0.037 | ~20% |
| Detergent 325611 | 0.040 | ~22% |
| Small appliances 335210 | 0.051 | ~27% |
| Men's apparel 315220 | 0.060 | ~32% |

`Margins of Supply Chain Emission Factors > 0` is also a useful programmatic test for
"this row is a physical good." 454 rows are nonzero and 96% of those sit in sectors
11–33; the 562 zero-margin rows are services and the margin sectors themselves. If a
mapping for a physical good lands on a zero-margin row, you've mapped a merchant.

**Corollary: never stack a retail row on top of a commodity row.** That double-counts
distribution.

### When a retail code IS correct

Only when retail service is the entire purchase and no goods change hands:
- Costco / Sam's Club membership fee → `452311`
- Instacart or delivery service fees → `454110` / `492210`
- Residual fallback for an undecomposable merchant — label it a known undercount,
  not an estimate.

---

## 3. Merchant name is a weak signal; the category is stronger

Merchant → NAICS is many-to-one at best and frequently one-to-many. A single Costco
charge is groceries + fuel + tires + prescriptions. No amount of care in code
selection recovers the basket from the merchant string.

**Prefer the accounting-software category as the primary key.** Monarch's category is
a statement about *what was bought*; the merchant name is a statement about *where*.
The former is what the factor needs.

Use merchant name for overrides on top of the category, not as the base signal —
`AMZN MKTP` under "Shopping" is genuinely ambiguous, but `PG&E`, `SHELL`, and
`NETFLIX` are unambiguous and should short-circuit to a specific rule.

### Composite factors per category

Build a weighted blend per category rather than picking a single code:

```
Groceries = 0.30×311xxx dairy/meat + 0.25×111xxx produce
          + 0.30×311xxx packaged + 0.15×312xxx beverages   → ~0.5–0.7
```

Anything else attributes a $200 grocery bill at 0.186 (37 kg) when the honest
number is 100–140 kg. Store the weights in a versioned config, not in code, and
write down where each weight came from — they are the least defensible part of the
model and the part most likely to be revisited.

---

## 4. Resolution precedence

Resolve each transaction through this ladder, first match wins:

1. **Physical-unit override** — kWh, therms, gallons, miles. Always beats spend.
   Inflation-proof and far more accurate. Extend this wherever quantities exist.
2. **Merchant-specific rule** — hand-curated, for high-volume or unambiguous merchants.
3. **Category composite** — the weighted blend above.
4. **Category fallback** — single best code for the category.
5. **Unmapped** — surface it. Do not silently coerce to zero.

Persist which rung fired for every transaction. It's the only way to know whether a
year-over-year change is real or a mapping change.

---

## 5. Exclude, don't zero

These are not consumption and must be dropped from the calculation entirely —
including from any per-dollar denominator:

- Internal transfers between own accounts
- Credit card payments (the underlying charges are already counted — counting both
  is a straight double count)
- Mortgage principal, loan principal, investment contributions, savings
- Refunds and chargebacks → let them flow through as negative, don't drop them

**Taxes** are a scope boundary rather than an accounting necessity. They fund
government output, which USEEIO excludes entirely (all `92xxxx` codes have no factor).
Personal-footprint conventions generally treat government emissions as collective, so
excluding them is defensible — but document it as a choice.

### Insurance: include it, but not at face value

Insurance is a purchased service with real codes and real factors — do **not** exclude it.

| `2017 NAICS Code` | title | `...with Margins` |
|---|---|---|
| 524126 | Direct Property and Casualty Insurance Carriers | 0.033 |
| 524113 | Direct Life Insurance Carriers | 0.051 |
| 524114 | Direct Health and Medical Insurance Carriers | 0.033 |
| 524210 | Insurance Agencies and Brokerages | 0.029 |

Caveat on the denominator: BEA measures insurance output as premiums earned plus
premium supplements *minus normal losses incurred and policyholder dividends*. The
factor is therefore per dollar of **insurance service charge**, not per dollar of
premium. At typical P&C loss ratios (60–75%), the service charge is ~25–40% of the
premium, so applying the factor to the full premium overstates roughly 3x.

Not worth correcting for P&C and life — 0.033 is small enough that a $2,400 annual
premium moves between 25 and 79 kg either way, noise against a 10–20 t household.
Apply to the full premium and note the bias.

**Health insurance is the exception that matters.** The premium funds healthcare
delivery, which is far more emissions-intensive than the carrier's back office:

| `2017 NAICS Code` | title | `...with Margins` |
|---|---|---|
| 622110 | General Medical and Surgical Hospitals | 0.145 |
| 623110 | Nursing Care Facilities | 0.159 |
| 621111 | Offices of Physicians | 0.083 |
| 621210 | Offices of Dentists | 0.056 |

Mapping health premiums to `524114` (0.033) prices the insurer, not the care. Route
them to a delivery blend weighted toward 622110 and 621111 — lands around 0.10–0.13,
roughly 3x higher, in a category that's often several thousand dollars a year.

Separately, 41 codes have no EPA factor and were dropped when the lookup table was
built — electricity (221xxx), government (92xxxx), private households (814110), and
331314. They are **absent as rows**, so a mapping to one of them produces a failed
join rather than a null. Assert on join success; don't let a left join emit NaN that
later coerces to zero.

Electricity is already handled by the physical-unit override. **814110 is the one that
bites** — it's the natural home for a housekeeper or nanny and it will silently miss
the join. Use `561720 Janitorial Services` (0.214) as the standard proxy.

---

## 6. Units, vintages, deflation

- **Key your mapping table on `2017 NAICS Code`.** It's unique (1,016 values) and it's
  what the factors are published against. `2022 NAICS Code` is present for lookup
  ergonomics — searching modern titles — but it is **not unique**: 79 codes appear on
  multiple rows because several 2017 codes merged into them. Joining on it fans out
  and will double-count.

  Streaming is the case in point. `2022 NAICS Code` 516210 spans five rows
  (radio networks, TV broadcasting, cable/subscription programming, news syndicates,
  internet publishing) with `...with Margins` from 0.064 to 0.094. Pick the
  `2017 NAICS Code` 515210 row (0.094) rather than averaging — the older, finer code
  carries more information than the modern merged one.
- **Deflate spend to 2022 USD before multiplying.** CPI-U all-items:

  | spend year | multiplier |
  |---|---|
  | 2022 | 1.000 |
  | 2023 | 0.960 |
  | 2024 | ~0.933 |
  | 2025 | ~0.909 |
  | 2026 | ~0.882 |

  2022–23 are exact from BLS monthlies; later years are chained estimates — pull
  `CPIAUCSL` from FRED for production. Note Oct 2025 is missing from the CPI series
  (2025 appropriations lapse), so interpolate if computing that annual average.
  Skipping deflation inflates recent years and manufactures ~12% of fake growth
  in a 2022→2026 trend.
- **Use `Supply Chain Emission Factors with Margins` by default.** Exception:
  direct-to-consumer digital purchases. The 17 rows in 511*/512* carry margins that
  model boxed software and physical media — for a SaaS subscription or digital
  download, use `Supply Chain Emission Factors without Margins` instead
  (Software Publishers 511210: 0.036, not 0.080).

---

## 7. Known limitations to encode in the output, not hide

**Price is a proxy for physical quantity.** A $24 bottle of eco-detergent gets exactly
twice the emissions of a $12 bottle of Tide. Spend-based accounting reads premium
pricing, brand markup, and inflation as physical throughput. It is directionally
useful across large categories and actively misleading on individual purchases.

Consequences worth surfacing in the UI:
- Buying secondhand is attributed as if new
- Sales and discounts appear as emissions reductions
- Switching to a cheaper supplier of the same good "reduces" your footprint
- One-off capital purchases (car, solar, appliances) land entirely in one month —
  consider amortizing over expected life rather than reporting the spike

**Channel modeling isn't worth it.** You can substitute your own margin blend
(`454110` online at 0.094 vs `445110` store at 0.186, plus couriers at 0.303 and
warehousing at 0.244), but margins are only ~11% of a manufactured good's total, and
online doesn't come out obviously cleaner once last-mile is included. Neither channel
captures your own drive to the store — that's household transport, sitting outside the
commodity boundary and often larger than the entire retail margin difference.

**Report ranges, not point estimates.** EEIO factors are industry averages with wide
dispersion. Three significant figures imply precision the method doesn't have.

---

## 8. Reproducibility

Store, per transaction: the resolved `2017 NAICS Code`, its `Reference USEEIO Code`,
which factor column was applied and its value, the factor dataset version (`v1.3.0`),
the deflator used, and the precedence rung that fired.

Factors get revised between releases and the mapping table will change as it's tuned.
Without this, recomputed history silently disagrees with what was previously shown,
and there's no way to attribute the difference.
