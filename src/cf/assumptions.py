"""The assumptions registry — every modeling choice, declared once.

Each entry is a named assumption: the value the code actually uses, the
rationale, the external sources, and the code that consumes it. Consumers
import values from here (Python) or read the injected ``ASSUME`` map (the
web app), and the methodology page (web/methodology.html) is generated from
the same entries — so a number can't drift between the code and the writeup.

Rules:
  - A scalar or small table that the app computes with lives HERE, and code
    references it by id. Hand-typed copies elsewhere are bugs.
  - Big factor tables (the 972-code EPA table, per-fuel factors, hourly grid
    data) stay in their data files; their entry documents provenance and
    links the file instead of duplicating it.
  - ``rationale`` is the writeup. Markdown links [text](url) and `code`
    are allowed; keep it honest about direction of bias.

Build: scripts/build_methodology.py renders the page; build_web.py and
src/cf/server.py inject ``js_values()`` into the app template.
"""

from dataclasses import dataclass

REPO = "https://github.com/ianschillebeeckx/carbon-footprint/blob/main/"


@dataclass(frozen=True)
class Assumption:
    id: str            # dotted, stable — anchors and ASSUME keys
    section: str       # method | gs | travel | electricity | home | food | offsets | impact | data
    label: str         # short title
    rationale: str     # the writeup prose (inline markdown links allowed)
    value: object = None   # the value code computes with (None = policy, no scalar)
    display: str = ""      # human formatting of value for the page ("" = str(value))
    bias: str = "neutral"  # over | under | neutral | varies — effect on YOUR footprint
    sources: tuple = ()    # ((title, url), ...)
    code: tuple = ()       # repo-relative paths that consume/implement it


A = Assumption

ASSUMPTIONS = [

    # ------------------------------------------------------------------
    # Cross-cutting method
    # ------------------------------------------------------------------
    A("method.gwp", "method", "All gases as CO₂e (GWP-100)",
      "Every factor in the app is expressed as CO₂-equivalent over a 100-year "
      "horizon, as published by its source (EPA supply-chain factors, eGRID, "
      "CoolClimate). Methane-heavy activities (gas leakage, landfill, beef) would "
      "look substantially worse on a 20-year horizon; GWP-100 is the reporting "
      "standard and every upstream source uses it, so the app does too.",
      bias="under",
      sources=(("IPCC AR5 WG1 Ch.8 (GWP values)", "https://www.ipcc.ch/report/ar5/wg1/"),)),

    A("method.annualize", "method", "Spending window → annual rate",
      "Uploaded transactions cover whatever window the export spans. The app "
      "measures the window from the date range (days ÷ 30.44 → months, minimum 1) "
      "and scales totals to a 12-month rate. A short window amplifies whatever was "
      "unusual about it — upload ~12 months so seasonality (holidays, travel, "
      "heating) averages out.",
      bias="varies",
      code=("site/v2-template.html", "src/cf/classify.py")),

    A("method.cpi_deflator", "method", "Spending deflated to 2022 dollars",
      "The EPA factors are kg CO₂e per **2022** dollar, so later spending is "
      "deflated by the CPI-U annual average before multiplying (a 2025 dollar buys "
      "~9% less stuff than a 2022 dollar, so it carries ~9% less production). "
      "Values: 2022 = 1.000, 2023 = 0.960, 2024 = 0.933, 2025 = 0.909, "
      "2026 = 0.882 (extrapolated at ~3%). Without this, inflation would read as "
      "emissions growth.",
      value={2022: 1.000, 2023: 0.960, 2024: 0.933, 2025: 0.909, 2026: 0.882},
      display="1.000 → 0.882 (2022→2026)",
      sources=(("BLS CPI-U", "https://www.bls.gov/cpi/"),),
      code=("src/cf/classify.py",)),

    A("method.top80_review", "method", "Review guidance: top 80% of spend",
      "Classification errors on small merchants barely move the total, so the "
      "merchant list marks the merchants that make up the top 80% of spend in "
      "each category (green dot). Reviewing just those bounds the damage any "
      "misclassification can do to roughly the last fifth of each category.",
      value=0.8, display="80% of spend per category",
      code=("site/v2-template.html",)),

    A("method.lowconf", "method", "Low-confidence flag threshold",
      "Merchant classifications come from an LLM with a self-reported confidence. "
      "Below 0.7 the row is flagged for review (amber), as are pure vector-search "
      "matches and goods codes missing retail margins. The threshold is a triage "
      "knob, not a statement about accuracy — spot checks show most sub-0.7 "
      "assignments are still reasonable.",
      value=0.7, display="confidence < 0.7",
      code=("site/v2-template.html", "worker/src/index.js")),

    # ------------------------------------------------------------------
    # Goods & Services
    # ------------------------------------------------------------------
    A("gs.epa_factors", "gs", "EPA supply-chain factors (USEEIO)",
      "Every dollar of classified spending is multiplied by EPA's Supply Chain "
      "GHG Emission Factor for its NAICS commodity — v1.3.0, kg CO₂e per 2022 "
      "dollar at **purchaser price, with margins**: the cradle-to-shelf average "
      "for that commodity across the whole US economy, including transport, "
      "wholesale, and retail. These are economy-wide averages from EPA's USEEIO "
      "input-output model — they can't see brands, so a dollar of artisanal "
      "furniture and a dollar of IKEA carry the same factor. All 972 usable "
      "codes are browsable on the [industry table](naics.html).",
      display="972 NAICS codes, kg CO₂e / 2022 USD",
      sources=(("EPA Supply Chain GHG Emission Factors v1.3.0",
                "https://catalog.data.gov/dataset/supply-chain-greenhouse-gas-emission-factors-v1-3-by-naics-6"),
               ("USEEIO model", "https://www.epa.gov/land-research/us-environmentally-extended-input-output-useeio-models")),
      code=("data/naics2022_with_2017_emission_factors_1.csv", "src/cf/naics_prep.py")),

    A("gs.commodity_rule", "gs", "Code the commodity, not the store",
      "A supermarket purchase is coded to food manufacturing, not to "
      "\"supermarkets\" — retail NAICS codes carry only the retail *margin* "
      "(the store's own lights and trucks), a factor 5–10× lower than the "
      "goods on the shelf. Using store codes is the single biggest way to "
      "undercount a spend-based footprint. Retail codes are used only when "
      "the store itself is the product (repair shops, dry cleaning).",
      sources=(("Design notes §2", REPO + "naics_mapping_design_notes.md"),),
      code=("src/cf/naics_prep.py",)),

    A("gs.classification", "gs", "Merchant → industry classification",
      "Each merchant/category pair resolves in order: your own corrections → "
      "deterministic rules (category hints like \"Restaurants\"; transfer/income "
      "detection) → a shared classification cache → vector search over the 972 "
      "codes → an LLM (Claude Haiku) choosing among the top candidates. The "
      "bank's own category column is weighted more heavily than the merchant "
      "string — \"Delta\" the airline and \"Delta\" the faucet brand separate on "
      "the hint. LLM picks carry a confidence and can be corrected; corrections "
      "are remembered.",
      bias="varies",
      code=("worker/src/index.js", "src/cf/classify.py")),

    A("gs.baskets", "gs", "Multi-category retailers get baskets",
      "Amazon, Costco, Walmart, Target and similar sell everything, so a single "
      "code would be wrong for most of the receipt. These merchants get a fixed "
      "weighted basket of commodity codes (e.g. general merchandise ≈ groceries "
      "+ apparel + electronics + household goods at typical shares), marked 🧺 "
      "in the list. The weights are national retail-mix estimates, editable "
      "per merchant — your actual cart will differ.",
      bias="varies",
      code=("src/cf/naics_prep.py", "site/v2-template.html")),

    A("gs.nonpurchase", "gs", "Transfers, payments, income excluded",
      "Credit-card payments, account transfers, Venmo, paychecks, and refunds "
      "are money movement, not consumption — they're detected (by category hint "
      "and merchant pattern) and carry no emissions, and they're excluded from "
      "spending totals rather than zeroed into them. Getting this wrong "
      "double-counts every card payment.",
      code=("src/cf/classify.py",)),

    A("gs.taxes_ignored", "gs", "Taxes and government fees ignored",
      "Tax payments, DMV registration, and similar government fees carry no "
      "factor: the EPA table has no public-administration rows (NAICS 92), and "
      "taxes buy no marginal production in an input-output sense. Government "
      "services you consume are real emissions but unattributable from a bank "
      "statement; they're outside the boundary, not zero. Tolls, by contrast, "
      "pay for road operations and are counted (NAICS 488490).",
      bias="under",
      code=("src/cf/naics_prep.py",)),

    A("gs.health_insurance", "gs", "Insurance priced at utilization, not premium",
      "A health-insurance premium is mostly a transfer into a pool that buys "
      "hospital care; the EPA \"insurance carriers\" factor covers only the "
      "carrier's own offices. Premiums are therefore expanded to a utilization "
      "mix (60% hospitals, 25% physician offices, 15% carrier overhead) so the "
      "care your premium funds is counted. The same logic applies to other "
      "insurance lines at their carrier factor.",
      display="60/25/15 hospital/physician/carrier",
      code=("src/cf/classify.py",)),

    A("gs.counted_elsewhere", "gs", "Fuel, flights, utilities, food leave G&S",
      "Transactions whose emissions are modeled physically in another tab are "
      "excluded from Goods & Services to prevent double counting: gas stations "
      "(NAICS 457) and airlines (481) → Travel; electric, gas, and water "
      "utilities (2211/221210/2213) → Home; groceries (445/311/312) and "
      "restaurants (722) → Food's diet model. Each tab shows the excluded "
      "spend as a cross-check against your physical inputs. Cost of the "
      "choice: the restaurant *service* overhead (the building, not the food) "
      "is dropped, so heavy dining-out is slightly undercounted.",
      bias="under",
      code=("src/cf/naics_prep.py", "site/v2-template.html")),

    # ------------------------------------------------------------------
    # Travel
    # ------------------------------------------------------------------
    A("travel.vehicle_fuel", "travel", "Vehicle fuel factors",
      "Gasoline 8.87 kg CO₂e/gallon tailpipe + 2.31 upstream (extraction, "
      "refining, transport); diesel 11.30 + 2.36. Fuel burn is miles ÷ MPG — "
      "your real MPG, not the sticker, if you know it. Factors measured from "
      "the CoolClimate household calculator API by finite differences (see "
      "the measured-factors file in the repo).",
      value={1: {"label": "Gasoline", "d": 8.874, "u": 2.307},
             2: {"label": "Diesel", "d": 11.304, "u": 2.360},
             3: {"label": "Electric", "d": 0, "u": 0}},
      display="gas 8.87+2.31, diesel 11.30+2.36 kg/gal",
      sources=(("CoolClimate Network calculator", "https://coolclimate.berkeley.edu/calculator"),),
      code=("data/coolclimate_travel_factors.json", "site/v2-template.html")),

    A("travel.ev_grid_missing", "travel", "EVs count zero fuel emissions",
      "CoolClimate's vehicle model prices electric vehicles' fuel at zero and "
      "does not add their charging load anywhere; the model is duplicated "
      "as-is. An EV charging on a real grid causes roughly 1–2 t CO₂e per "
      "10k miles depending on the region — if you drive an EV, add its kWh "
      "to your Home electricity to count it honestly.",
      bias="under",
      sources=(("CoolClimate Network calculator", "https://coolclimate.berkeley.edu/calculator"),),
      code=("site/v2-template.html",)),

    A("travel.vehicle_manufacture", "travel", "Vehicle manufacturing per mile",
      "Building and maintaining the vehicle is amortized at 0.056 kg CO₂e per "
      "mile driven, regardless of fuel (CoolClimate's approach). Driving less "
      "reduces it; a lightly-driven second car still carries most of its "
      "embodied emissions per mile.",
      value=0.056, display="0.056 kg/mile",
      sources=(("CoolClimate Network calculator", "https://coolclimate.berkeley.edu/calculator"),),
      code=("data/coolclimate_travel_factors.json",)),

    A("travel.ground_transit", "travel", "Transit factors (and CoolClimate's bus bug)",
      "Per passenger-mile, measured from CoolClimate: simple \"public transit\" "
      "225 g; detailed mode — transit rail 205 g, commuter rail 205 g, intercity "
      "rail 233 g, bus **1.3 g**. That bus number is CoolClimate's own model "
      "output and is ~100× below their published 107 g/mile constant — almost "
      "certainly their bug, duplicated faithfully rather than silently patched. "
      "Use the simple mode if you ride buses.",
      value={"publictrans": 0.2253, "bus": 0.0013, "transit": 0.2054,
             "commuter": 0.2054, "intercity": 0.2331},
      display="205–233 g/pax-mile (bus 1.3 g — their bug)",
      bias="under",
      sources=(("CoolClimate Network calculator", "https://coolclimate.berkeley.edu/calculator"),),
      code=("data/coolclimate_travel_factors.json", "site/v2-template.html")),

    A("travel.air", "travel", "Flight factors, no contrail multiplier",
      "Per passenger-mile, well-to-wake: short-haul 250 g, medium 156 g, long "
      "197 g — EPA GHG Factors Hub 2025 combustion factors plus ~21% upstream "
      "fuel production (ICCT: CORSIA life-cycle 89 vs combustion 73.2 "
      "gCO₂e/MJ). **Deliberately excludes contrail and other non-CO₂ effects**, "
      "which best estimates put at another ~1.7–2× on flying — they're "
      "short-lived and scientifically uncertain, but excluding them means air "
      "travel here is a floor, not a midpoint.",
      value={"short": 0.250, "medium": 0.156, "long": 0.197},
      display="250/156/197 g per pax-mile (short/med/long)",
      bias="under",
      sources=(("EPA GHG Emission Factors Hub", "https://www.epa.gov/climateleadership/ghg-emission-factors-hub"),
               ("ICCT well-to-wake analysis", "https://theicct.org/wp-content/uploads/2021/08/update-well-to-wake-co2-aug21-1.pdf")),
      code=("site/v2-template.html",)),

    A("travel.air_trip_miles", "travel", "Representative round-trip lengths",
      "The flight-count mode converts \"2 long round trips\" to miles at "
      "representative round-trip distances: short 400 mi, medium 2,000 mi, "
      "long 6,000 mi (2× typical one-way segments of 200/1,000/3,000). Real "
      "trips vary ±50%; switch to miles mode for actual distances.",
      value={"short": 400, "medium": 2000, "long": 6000},
      display="400 / 2,000 / 6,000 mi round trip",
      code=("site/v2-template.html",)),

    A("travel.crosscheck_rates", "travel", "Travel cross-check price assumptions",
      "The cross-check panel converts your uploaded gas-station and airline "
      "spend into implied physical activity at assumed prices: gasoline "
      "$4.50/gallon, economy air fare ~15¢/mile, transit ~25¢/mile, and "
      "rideshare ~$2/mile. Rough yardsticks to catch a missing vehicle or "
      "forgotten trip — not accounting inputs.",
      value={"gas_per_gal": 4.50, "air_per_mile": 0.15,
             "transit_per_mile": 0.25, "taxi_per_mile": 2},
      display="$4.50/gal · 15¢/air-mile · 25¢/transit-mile",
      code=("site/v2-template.html",)),

    # ------------------------------------------------------------------
    # Electricity
    # ------------------------------------------------------------------
    A("elec.physical_grid", "electricity", "Everyone gets the physical grid average",
      "Your electricity factor is the physical average of your local grid at "
      "the hours households use power — regardless of any green-power plan. "
      "A \"100% renewable\" contract is real procurement your money funds, but "
      "at 7pm the wires deliver the same gas-heavy mix to every meter on the "
      "circuit; market-based accounting would let the same clean midday solar "
      "be claimed by contract holders while everyone else's evening power "
      "pretends it away. This app answers \"what do my outlets cause\", not "
      "\"what do my contracts claim\".",
      sources=(("gridcarbon README (full argument)", REPO + "zip2co2_2/README.md"),),
      code=("zip2co2_2/gridcarbon/", "site/v2-template.html")),

    A("elec.ba_resolution", "electricity", "ZIP → utility → balancing authority",
      "Location is resolved to the balancing authority (the grid operator that "
      "physically dispatches your power) via EPA Power Profiler and EIA-861 "
      "service territories — ~39,000 ZIPs mapped. BA is finer than eGRID's "
      "subregions: CAMX averages together CISO, LADWP, BANC and IID, and LADWP "
      "is materially dirtier than the CAMX average. ZIPs without a BA mapping "
      "fall back to their eGRID subregion's flat rate.",
      display="61 BAs · ~39k ZIPs",
      sources=(("EPA Power Profiler", "https://www.epa.gov/egrid/power-profiler"),
               ("EIA-861", "https://www.eia.gov/electricity/data/eia861/")),
      code=("zip2co2_2/make_zip_ba.py", "scripts/build_gridcarbon_web.py")),

    A("elec.hourly_intensity", "electricity", "Hourly intensity from EIA-930",
      "For every hour of 2024, your BA's generation by fuel (EIA-930 Hourly "
      "Grid Monitor) is combined with per-fuel emission factors to reconstruct "
      "that hour's kg CO₂e/kWh — so noon solar and the 7pm gas ramp get their "
      "own numbers instead of one annual average. Handles EIA's mid-2024 "
      "schema change (solar/wind split by battery integration — including "
      "EIA's typo'd \"Solar witho Integrated Battery Storage\" column, which "
      "is real and carries data); missing it would have read July noons as "
      "85% gas.",
      display="8,784 hours × fuel mix, per BA",
      sources=(("EIA-930 Hourly Electric Grid Monitor", "https://www.eia.gov/electricity/gridmonitor/"),),
      code=("zip2co2_2/make_real_cache.py", "zip2co2_2/gridcarbon/core.py")),

    A("elec.alpha_calibration", "electricity", "Calibrated to eGRID (α), which doubles as validation",
      "The reconstructed hourly curve has the right *shape* but an uncertain "
      "*level* (fuel factors are national fleet averages; CISO's gas fleet is "
      "newer than average). One scalar α per BA rescales the curve so its "
      "annual average exactly matches EPA's measured eGRID rate — level from "
      "EPA, shape from EIA-930. α also doubles as an integrity check: "
      "|α−1| measures reconstruction error. 37/61 BAs land within ±10%, all "
      "major load centers within ±5% (NYIS 1.00, PJM 1.02, CISO 0.97). BAs "
      "with α outside [0.70, 1.45] indicate a structural data problem and "
      "don't ship an hourly shape at all.",
      value={"band_lo": 0.70, "band_hi": 1.45},
      display="α ∈ [0.70, 1.45] to ship hourly",
      sources=(("EPA eGRID", "https://www.epa.gov/egrid"),
               ("Alpha validation table", REPO + "zip2co2_2/README.md")),
      code=("zip2co2_2/gridcarbon/core.py", "scripts/build_gridcarbon_web.py")),

    A("elec.production_based", "electricity", "Production-based: imports not counted",
      "Intensity covers generation *inside* your BA only. Consumption-based "
      "accounting would price imports too, but the only defensible default "
      "(CARB's 428 g \"unspecified\" rate) is a California construct — it "
      "would price Seattle's BPA-hydro imports as gas — and on real data it "
      "pushed a third of BAs outside α's sanity band. Cost: import-heavy BAs "
      "are understated (CISO ~10–15% low vs consumption accounting); the "
      "evening-import share of the daily shape is lost. This matches how "
      "eGRID location-based rates are normally used.",
      bias="under",
      sources=(("Deviation #1, gridcarbon README", REPO + "zip2co2_2/README.md"),),
      code=("zip2co2_2/gridcarbon/build.py",)),

    A("elec.storage_split", "electricity", "Batteries split out of \"Other\"",
      "CAISO and others report grid batteries inside \"Other Fuel Sources\", "
      "which otherwise gets priced like gas — so evening discharge of stored "
      "midday solar counted as fossil. Per local day, the excess of Other "
      "above its daily minimum is reclassified as storage (0 combustion, "
      "55 g/kWh lifecycle); flat fossil \"Other\" (waste-coal BAs) has no "
      "daily cycle and is untouched. Validation: CISO's α moved 0.73 → 0.97 "
      "and post-battery evenings (~235 g) now correctly read cleaner than "
      "deep night (~279 g).",
      display="daily-min split; storage at 55 g/kWh",
      sources=(("Deviation #3, gridcarbon README", REPO + "zip2co2_2/README.md"),),
      code=("zip2co2_2/make_real_cache.py", "zip2co2_2/gridcarbon/data/fuel_factors.csv")),

    A("elec.load_shape", "electricity", "Weighted by when households use power",
      "The hourly intensity is averaged using a typical home's hourly usage "
      "(OpenEI TMY3 residential load profiles, aligned to local time) — "
      "households use most power on winter evenings, when solar is gone and "
      "gas is ramping, so the factor usually runs a few percent above the "
      "flat annual average (CISO +1.4%, PNM +10.7%). The weights only decide "
      "which hours count more; your kWh input supplies all the scale.",
      display="TMY3 residential, per-BA local time",
      sources=(("OpenEI TMY3 load profiles", "https://data.openei.org/submissions/153"),),
      code=("zip2co2_2/make_real_cache.py", "zip2co2_2/gridcarbon/core.py")),

    A("elec.seasonal_limit", "electricity", "Seasonality is TMY3's typical home, not yours",
      "The load weighting carries a season, and it can dominate: Portland "
      "(PGE) comes out **−6%** vs flat because the hydro-backed grid is "
      "dirtiest in late summer (reservoirs low, ~500 g) and cleanest in "
      "spring runoff (~216 g), while a typical home peaks in winter. But "
      "that's TMY3's typical home — electric heating peaks harder in winter "
      "(bigger discount), gas heating less. If your utility offers interval "
      "data (Green Button), the advanced upload replaces the assumed "
      "seasonality with your actual meter.",
      bias="varies",
      sources=(("Seasonal covariance, gridcarbon README", REPO + "zip2co2_2/README.md"),),
      code=("zip2co2_2/gridcarbon/core.py", "site/v2-template.html")),

    A("elec.upstream", "electricity", "Upstream fuel-cycle emissions added",
      "Combustion isn't the whole story: extracting, processing, and "
      "transporting fuels adds emissions (methane leakage dominates for gas). "
      "Mix-weighted upstream factors (IPCC AR5 lifecycle values per fuel) are "
      "added after calibration, typically +8–10% on a gas-heavy grid.",
      display="IPCC AR5 lifecycle, mix-weighted",
      sources=(("IPCC AR5 Annex III", "https://www.ipcc.ch/site/assets/uploads/2018/02/ipcc_wg3_ar5_annex-iii.pdf"),),
      code=("zip2co2_2/gridcarbon/data/fuel_factors.csv", "scripts/build_gridcarbon_web.py")),

    A("elec.delivery_loss", "electricity", "Delivery losses",
      "Intensities are computed at the busbar; ~4.2% of US generation is lost "
      "in transmission and distribution before it reaches your meter, so "
      "factors are grossed up by 1/(1−0.042). Your kWh input is what your "
      "meter (and bill) shows.",
      value=0.042, display="+4.4% (grid gross loss 4.2%)",
      sources=(("EPA eGRID summary data (grid gross loss)", "https://www.epa.gov/egrid/summary-data"),),
      code=("scripts/build_gridcarbon_web.py",)),

    A("elec.ship_gate", "electricity", "When hourly data isn't trusted: flat fallback",
      "A BA ships its hourly shape only if α is in band, the load-weighting "
      "adjustment is |uplift| ≤ 12%, and ≥ 90% of hours reconstructed — 46 of "
      "61 BAs pass. The rest (plus ZIPs with no BA mapping) degrade to EPA's "
      "flat annual eGRID rate plus upstream: level right, no claim about "
      "hours. Better no shape than a wrong shape.",
      value={"uplift_max_pct": 12.0, "coverage_min": 0.90},
      display="|uplift| ≤ 12% · coverage ≥ 90% · 46/61 BAs ship",
      code=("scripts/build_gridcarbon_web.py",)),

    A("elec.hourly_upload", "electricity", "Advanced: your meter replaces the assumed profile",
      "Uploading a year of utility interval data (Green Button CSV) replaces "
      "the TMY3 assumption entirely: emissions become the exact sum of your "
      "kWh in each hour × that hour's grid intensity, joined by calendar "
      "position onto the 2024 intensity year. Parsing is robust to arbitrary "
      "column layouts (deterministic detection, then an LLM column-mapper as "
      "fallback, then unit/date-order probes against the data). Quality "
      "gates: all 12 months present with ≥ 20 days each, and an annual total "
      "between 50 and 200,000 kWh — otherwise the upload is rejected rather "
      "than silently wrong.",
      value={"min_days_per_month": 20, "min_kwh": 50, "max_kwh": 200000},
      display="Σ kWh_h × I_h · 12 months × ≥20 days",
      code=("site/v2-template.html", "worker/src/index.js", "src/cf/hourly_mapper.py")),

    # ------------------------------------------------------------------
    # Home (non-electric)
    # ------------------------------------------------------------------
    A("home.gas", "home", "Natural gas per therm",
      "5.47 kg CO₂e/therm combustion + 0.77 upstream (production and "
      "leakage) — CoolClimate's factors. Upstream leakage estimates vary "
      "widely in the literature (this is on the lower half); on GWP-20 gas "
      "would look substantially worse.",
      value={"d": 5.470, "u": 0.766},
      display="5.47 + 0.77 kg/therm",
      bias="under",
      sources=(("CoolClimate Network calculator", "https://coolclimate.berkeley.edu/calculator"),),
      code=("site/v2-template.html",)),

    A("home.oil", "home", "Heating oil per gallon",
      "11.79 kg CO₂e/gallon combustion + 3.07 upstream — CoolClimate's "
      "factors, measured from their API.",
      value={"d": 11.793, "u": 3.066},
      display="11.79 + 3.07 kg/gal",
      sources=(("CoolClimate Network calculator", "https://coolclimate.berkeley.edu/calculator"),),
      code=("site/v2-template.html",)),

    A("home.construction", "home", "Home construction amortized",
      "Building and maintaining the structure is amortized at 0.93 kg CO₂e "
      "per square foot per year (CoolClimate). A 1,500 sqft home carries "
      "~1.4 t/yr regardless of energy use — one reason square footage is a "
      "bigger lever than insulation marketing suggests.",
      value=0.93, display="0.93 kg/sqft/yr",
      sources=(("CoolClimate Network calculator", "https://coolclimate.berkeley.edu/calculator"),),
      code=("site/v2-template.html",)),

    A("home.water_omitted", "home", "Water & sewer deliberately omitted",
      "Physically, municipal water is ~4.25 kWh per 1,000 gallons (EPA/EPRI) "
      "≈ 25–110 kg CO₂e/yr for typical households — a rounding error, so "
      "it's left out. CoolClimate's much larger water figure is "
      "expenditure-based and double-counts water heating (which your "
      "gas/electric inputs already cover). Your water-utility spend shows in "
      "the cross-check as excluded.",
      bias="under",
      sources=(("EPA WaterSense (energy-water nexus)", "https://www.epa.gov/watersense"),),
      code=("site/v2-template.html",)),

    A("home.crosscheck_rates", "home", "Home cross-check price assumptions",
      "Utility spend from your upload is converted to implied usage at "
      "CoolClimate's rate assumptions — $0.223/kWh and $2.02/therm — to "
      "compare against what you entered. Local rates vary ±2×; it's a "
      "consistency check, not a bill audit.",
      value={"kwh": 0.2233, "therm": 2.015, "oil_gal": 3.0},
      display="$0.223/kWh · $2.02/therm",
      code=("site/v2-template.html",)),

    # ------------------------------------------------------------------
    # Food
    # ------------------------------------------------------------------
    A("food.model", "food", "Diet model: servings → calories → CO₂e",
      "CoolClimate's food model at full meat detail. Weekly servings convert "
      "to calories/day with their serving sizes (beef/pork 213 cal, poultry/"
      "fish 204, dairy 120, fruits/vegetables 70, grains 150), then calories "
      "carry measured factors — per (calorie/day) over a year: beef & pork "
      "2.22 kg, fish 2.08, poultry 1.56, dairy 1.46, fruits/vegetables 1.22 "
      "(modeled jointly), snacks/other 0.82, grains 0.53. US-average servings "
      "prefilled; the calories-per-day readout should land near ~2,300 if "
      "your servings are realistic. Beef→poultry or beef→legume swaps are "
      "the biggest levers.",
      value={"beefpork": {"label": "Beef, pork, lamb, veal", "f": 2.2228, "cps": 213, "def": 7.4},
             "poultry": {"label": "Poultry & eggs", "f": 1.5585, "cps": 204, "def": 5.2},
             "fish": {"label": "Fish & seafood", "f": 2.0841, "cps": 204, "def": 2.3},
             "meatother": {"label": "Alt meat (legumes, tofu, nuts)", "f": 0.8176, "cps": 204, "def": 1.8},
             "dairy": {"label": "Dairy", "f": 1.4600, "cps": 120, "def": 15.3},
             "fruits": {"label": "Fruits", "f": 1.2228, "cps": 70, "def": 12.4},
             "veggies": {"label": "Vegetables", "f": 1.2228, "cps": 70, "def": 12.4},
             "cereals": {"label": "Grains & baked goods", "f": 0.5292, "cps": 150, "def": 28.6},
             "otherfood": {"label": "Snacks, drinks, etc.", "f": 0.8176, "cps": 200, "def": 23.6}},
      display="beef 2.22 → grains 0.53 kg per (cal/day)·yr",
      sources=(("CoolClimate Network calculator", "https://coolclimate.berkeley.edu/calculator"),),
      code=("data/coolclimate_factors.json", "site/v2-template.html")),

    A("food.legume_conservative", "food", "Legumes charged conservatively",
      "Beans, lentils, tofu and nuts share CoolClimate's blended \"alt meat\" "
      "factor. The literature puts pure legumes ~2–4× lower than that blend, "
      "so a beef→beans swap is at least as good as displayed — the app "
      "overstates plant-protein emissions rather than overselling the swap.",
      bias="over",
      sources=(("Poore & Nemecek 2018", "https://www.science.org/doi/10.1126/science.aaq0216"),),
      code=("site/v2-template.html",)),

    A("food.crosscheck_grocery", "food", "Grocery cross-check composite",
      "The cross-check converts uploaded grocery spend (2022 USD) at a "
      "~0.60 kg CO₂e/$ commodity composite — a diet-mix assumption, since "
      "spend can't see what's in the cart; restaurants use their own USEEIO "
      "factor (~0.23–0.36 kg/$, mostly the service, diet-blind). A strong "
      "disagreement with the diet estimate usually means heavy dining out "
      "(undercounted there) or premium groceries (overcounted).",
      value=0.60, display="0.60 kg CO₂e / 2022 $",
      sources=(("Design notes §3", REPO + "naics_mapping_design_notes.md"),),
      code=("site/v2-template.html",)),

    # ------------------------------------------------------------------
    # Offsets & credits
    # ------------------------------------------------------------------
    A("offsets.user_entered", "offsets", "Offsets at face value, price as quality proxy",
      "You enter dollars/year and the provider's $/tonne; tonnes = dollars ÷ "
      "price, subtracted from the section you assign. The app takes your "
      "provider's claim at face value — but price is a decent permanence "
      "proxy: durable removal (bio-oil ~$600/t, DAC ~$1,000/t) permanently "
      "stores carbon, while $5–20/t avoidance credits often don't hold up. "
      "Cheap credits will happily zero your footprint on paper.",
      bias="varies",
      code=("site/v2-template.html",)),

    A("offsets.removal_price", "offsets", "Reference removal price",
      "$600/tonne — Charm Industrial's bio-oil sequestration, used as the "
      "default offset row and as the \"cost to remove everything you emit\" "
      "impact card. Chosen as a real, purchasable, durable (geologic) removal "
      "price rather than a cheap-credit average; DAC runs higher (~$1,000), "
      "avoidance credits far lower.",
      value=600, display="$600 / tonne",
      sources=(("Charm Industrial", "https://charmindustrial.com"),),
      code=("site/v2-template.html",)),

    A("offsets.recycle", "offsets", "Recycling credit",
      "~2.9 t CO₂e avoided per short ton of mixed recyclables vs landfilling "
      "(EPA equivalencies; recycled material displaces virgin production, so "
      "it's credited against Goods by default). Estimating: US average is "
      "~8 lbs/person/week; a full 13-gal bag ≈ 8–10 lbs. This is an "
      "*avoidance* estimate — softer than paid removal, and your curbside "
      "program's actual material fate varies.",
      value=2.9, display="2.9 t CO₂e / ton recycled",
      bias="over",
      sources=(("EPA GHG equivalencies", "https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator"),),
      code=("site/v2-template.html",)),

    A("offsets.compost", "offsets", "Compost credit",
      "~0.6 t CO₂e per ton of food scraps diverted from landfill (EPA WARM — "
      "avoided landfill methane), credited against Food. A full countertop "
      "pail ≈ 3–5 lbs; composting all scraps ≈ 4–7 lbs/person/week. Same "
      "avoidance caveat as recycling.",
      value=0.6, display="0.6 t CO₂e / ton composted",
      sources=(("EPA WARM", "https://www.epa.gov/warm"),),
      code=("site/v2-template.html",)),

    # ------------------------------------------------------------------
    # Impact numbers
    # ------------------------------------------------------------------
    A("impact.scc", "impact", "Social cost of carbon",
      "$240 per tonne: EPA's 2023 central estimate (2% near-term Ramsey "
      "discounting) is $190/t for 2020 emissions in 2020 dollars; the SCC "
      "grows with emission year and this is adjusted to a mid-2020s emission "
      "year in current dollars ≈ $240. It's the discounted present value of "
      "future damages — storms, floods, crop loss, heat mortality — mostly "
      "borne by people other than the emitter. Lower discount rates or "
      "harder-to-quantify damages push it higher; the pre-2021 federal $51 "
      "figure is well below the current literature.",
      value=240, display="$240 / t CO₂e",
      sources=(("EPA SC-GHG report (2023)", "https://www.epa.gov/environmental-economics/scghg"),),
      code=("site/v2-template.html",)),

    A("impact.mortality", "impact", "Excess deaths (Bressler mortality cost)",
      "Bressler (2021): under RCP6.1-like warming, sustained emissions cause "
      "~2.26×10⁻⁴ excess deaths through 2100 per tonne of CO₂ — about one "
      "death per 4,400 tonnes. The app shows deaths if your current rate is "
      "sustained 50 years, each year's tonnes counting deaths through 2100 "
      "via a closed-form fit (per-year mortality 226 micromorts/t declining "
      "quadratically at 0.04028·k² as the horizon shrinks). Temperature-"
      "mortality only — excludes famine, conflict, and other channels, so "
      "it's a conservative floor.",
      value={"umort_per_t": 226.0, "decay": 0.04028, "horizon_years": 50},
      display="≈ 1 death / 4,400 t (50-yr horizon)",
      bias="under",
      sources=(("Bressler 2021, Nature Communications", "https://www.nature.com/articles/s41467-021-24487-w"),),
      code=("site/v2-template.html",)),

    A("impact.us_benchmark", "impact", "US household benchmark",
      "The amber comparison tick is the average US household from "
      "CoolClimate's national defaults: 49.9 t CO₂e/yr — travel 15.7, home "
      "12.2, food 7.0, goods 7.9, services 7.0. A *household* average (not "
      "per person), computed under this app's own section boundaries so the "
      "comparison is apples-to-apples.",
      value={"travel": 15718, "home": 12219, "food": 7002,
             "goods": 7920, "services": 7032},  # kg/yr; totals 49.9 t
      display="49.9 t CO₂e / household / yr",
      sources=(("CoolClimate Network calculator", "https://coolclimate.berkeley.edu/calculator"),),
      code=("site/v2-template.html",)),

    # ------------------------------------------------------------------
    # Data handling
    # ------------------------------------------------------------------
    A("data.local_first", "data", "Your data stays in your browser",
      "Transactions, amounts, dates, and all tab inputs live in this "
      "browser's localStorage — the server never stores them. What leaves the "
      "browser: merchant names + bank category hints (to a shared "
      "classification cache and, for unknowns, an LLM), and hourly-CSV "
      "*header samples* for column mapping. Corrections you make improve the "
      "shared merchant cache for everyone; your amounts never do.",
      code=("scripts/build_web.py", "worker/src/index.js")),
]

REGISTRY = {a.id: a for a in ASSUMPTIONS}
assert len(REGISTRY) == len(ASSUMPTIONS), "duplicate assumption id"

SECTIONS = {
    "method": "Cross-cutting method",
    "gs": "Goods & Services",
    "travel": "Travel",
    "electricity": "Home — electricity",
    "home": "Home — gas, oil, structure",
    "food": "Food",
    "offsets": "Offsets & credits",
    "impact": "Damages, deaths & benchmark",
    "data": "Data handling",
}


def js_values() -> dict:
    """The {id: value} map injected into the app as ASSUME."""
    return {a.id: a.value for a in ASSUMPTIONS if a.value is not None}


# ---- Python-side constants (import these, don't retype) ----
CPI_DEFLATOR = REGISTRY["method.cpi_deflator"].value
ALPHA_BAND = (REGISTRY["elec.alpha_calibration"].value["band_lo"],
              REGISTRY["elec.alpha_calibration"].value["band_hi"])
UPLIFT_MAX = REGISTRY["elec.ship_gate"].value["uplift_max_pct"]
COVERAGE_MIN = REGISTRY["elec.ship_gate"].value["coverage_min"]
GRID_LOSS = REGISTRY["elec.delivery_loss"].value
