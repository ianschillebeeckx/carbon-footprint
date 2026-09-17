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

CHANGING A VALUE: edit it here (never as a literal elsewhere), update this
entry's rationale/sources/bias in the same edit — the page describes current
factors only, no change history — then run scripts/build_web.py and commit
the regenerated web/ pages so the deployed methodology never lags the code.

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

# Shared citations. CoolClimate's model is peer-reviewed (Jones & Kammen 2011);
# the exact constants in this app were measured from the deployed calculator's
# API in 2026, which postdates the paper — cite both, never the website alone.
JK2011 = ("Jones & Kammen 2011, Environ. Sci. Technol. 45(9): 4088–4095",
          "https://pubs.acs.org/doi/10.1021/es102221h")
CC_API = ("CoolClimate calculator (constants measured from its API, 2026)",
          "https://coolclimate.berkeley.edu/calculator")
EPA_HUB = ("EPA GHG Emission Factors Hub (Jan 2025, EPA's last edition)",
           "https://www.epa.gov/climateleadership/ghg-emission-factors-hub")
ICCT_WTW = ("ICCT well-to-wake analysis (EPA RFS2 life-cycle basis)",
            "https://theicct.org/wp-content/uploads/2021/08/update-well-to-wake-co2-aug21-1.pdf")
NTD = ("APTA Public Transportation Fact Book 2025 (FTA National Transit Database RY2023)",
       "https://www.apta.com/research-technical-resources/transit-statistics/public-transportation-fact-book/")

ASSUMPTIONS = [

    # ------------------------------------------------------------------
    # Cross-cutting method
    # ------------------------------------------------------------------
    A("method.gwp", "method", "All gases as CO₂e (GWP-100)",
      "Every factor is expressed as CO₂-equivalent over a 100-year horizon, "
      "as published by its source. GWP-100 is what the reporting standards "
      "require: the UNFCCC transparency framework mandates AR5 values for "
      "national inventories, and EPA's own inventory and eGRID use them.\n\n"
      "The assessment vintage is not uniform across our sources, and it is "
      "honest to say so. eGRID is AR5. The supply-chain factors moved to AR6 "
      "with v1.4.0 (methane 29.8 rather than 28, nitrous oxide 273 rather "
      "than 265). CoolClimate documents no vintage at all and is probably "
      "AR4-era. Re-expressing everything on one set would move a total by "
      "under 1%, because the difference between AR5 and AR6 is a few percent "
      "on two gases that are a small share of the mix — so this is a "
      "documentation issue rather than a numerical one.\n\n"
      "The horizon matters far more than the vintage. On a 20-year basis "
      "methane's weight nearly triples (82.5 against 29.8), which would raise "
      "beef by roughly 1.6–2×, natural gas upstream by about the same, and a "
      "US-average emissions mix by around 21%. Methane-heavy activities are "
      "therefore understated here relative to their near-term warming effect.",
      bias="under",
      sources=(("IPCC AR5 WG1 Ch.8, Table 8.7", "https://www.ipcc.ch/report/ar5/wg1/"),
               ("IPCC AR6 WG1 Ch.7, Table 7.15", "https://www.ipcc.ch/report/ar6/wg1/chapter/chapter-7/"),
               ("UNFCCC Decision 18/CMA.1 (common metrics)",
                "https://unfccc.int/process-and-meetings/transparency-and-reporting/reporting-and-review/methods-for-climate-change-transparency/common-metrics"))),

    A("method.annualize", "method", "Spending window → annual rate",
      "Uploaded transactions cover whatever window the export spans. The app "
      "measures the window from the date range (days ÷ 30.44 → months, minimum 1) "
      "and scales totals to a 12-month rate. A short window amplifies whatever was "
      "unusual about it — upload ~12 months so seasonality (holidays, travel, "
      "heating) averages out.",
      bias="varies",
      code=("site/v2-template.html", "src/cf/classify.py")),

    A("method.cpi_deflator", "method", "Spending rebased to 2024 dollars",
      "The factor set is kg CO₂e per **2024** dollar, so spending from other "
      "years is rebased by the CPI-U annual average before multiplying. A "
      "2022 dollar bought about 7% more than a 2024 one, so it is scaled "
      "*up*; a 2026 dollar buys less, so it is scaled down. Without this, "
      "inflation would read as emissions growth.\n\n"
      "Computed from BLS series CUUR0000SA0 annual averages — 2022 = 292.655, "
      "2023 = 304.702, 2024 = 313.689, 2025 = 321.943, 2026 = 331.655 "
      "(January–August) — as 313.689 ÷ the year's index. The 2026 figure "
      "refreshes when BLS publishes that annual average in January. Note "
      "October 2025 was never published — the federal shutdown made the "
      "survey unrecoverable — so the 2025 average rests on eleven months.\n\n"
      "One honest limitation: CPI-U is a *household basket* standing in for "
      "what should be commodity-specific output prices. Close enough across "
      "goods and services in aggregate, but wrong in places — gasoline's 2022 "
      "price spike means fuel dollars from that year need a very different "
      "adjustment than the all-items index gives.",
      value={2022: 1.0719, 2023: 1.0295, 2024: 1.000, 2025: 0.9744, 2026: 0.9458},
      display="1.072 → 0.946 (2022→2026), 2024 base",
      sources=(("BLS CPI-U, series CUUR0000SA0", "https://www.bls.gov/cpi/"),),
      code=("src/cf/classify.py",)),

    A("method.top80_review", "method", "Review guidance: top 80% of spend",
      "Classification errors on small merchants barely move the total, so the "
      "merchant list marks the merchants making up the top 80% of **emissions** "
      "in each category (green dot). Reviewing just those bounds what a "
      "misclassification can cost to roughly the last fifth of the category.\n\n"
      "Ranked by kg rather than dollars, which are not interchangeable here: "
      "factors span about 8× across the index's 10th-to-90th percentile, so a "
      "merchant sitting just below a dollar cutoff can outweigh several above "
      "it. An earlier version ranked by spend while claiming to bound "
      "emissions. The 80% convention itself is ordinary ABC/Pareto triage — "
      "verify the vital few, sample the rest.",
      value=0.8, display="80% of emissions per category",
      code=("site/v2-template.html",)),

    A("method.coolclimate", "method", "CoolClimate constants: paper vs deployed calculator",
      "Travel, home-fuel, construction, food, and the US-benchmark factors follow "
      "UC Berkeley's CoolClimate household model, whose methodology is "
      "peer-reviewed ([Jones & Kammen 2011](https://pubs.acs.org/doi/10.1021/es102221h), "
      "with the food/goods LCA groundwork in Jones, Kammen & McGrath 2008). The "
      "*specific constants* in this app, however, were measured from the deployed "
      "calculator's API by finite differences in 2026 — the calculator has been "
      "updated since the paper, and the paper does not tabulate these exact "
      "values (see [the measured-factors file](" + REPO + "data/coolclimate_travel_factors.json)). "
      "So CoolClimate entries cite both: the paper for the model, the API "
      "measurement for the value and its vintage. Where an entry deviates from "
      "the calculator (the bus factor), the substitute source is named on that "
      "entry.",
      sources=(JK2011, CC_API),
      code=("data/coolclimate_travel_factors.json", "data/coolclimate_factors.json")),

    A("method.lowconf", "method", "Low-confidence flag threshold",
      "Merchant classifications come from an LLM with a self-reported "
      "confidence. Below 0.7 the row is flagged for review (amber), as are "
      "pure vector-search matches and goods codes missing retail margins.\n\n"
      "A self-reported confidence is a **ranking signal, not a probability**. "
      "The literature is consistent that language models are systematically "
      "overconfident and that verbalised scores cluster on round numbers, so "
      "0.7 does not mean \"70% likely right\" — it is simply a cutoff that "
      "surfaces roughly the right rows to look at. Flagging vector-only "
      "matches and margin-less goods codes as well is deliberate "
      "defence-in-depth, since those failures don't depend on the confidence "
      "signal being meaningful.",
      value=0.7, display="confidence < 0.7",
      sources=(("Xiong et al. 2024 (ICLR), confidence elicitation in LLMs",
                "https://arxiv.org/abs/2306.13063"),
               ("Tian et al. 2023 (EMNLP), verbalized confidence calibration",
                "https://aclanthology.org/2023.emnlp-main.330/")),
      code=("site/v2-template.html", "worker/src/index.js")),

    # ------------------------------------------------------------------
    # Goods & Services
    # ------------------------------------------------------------------
    A("gs.epa_factors", "gs", "EPA supply-chain factors (USEEIO)",
      "Every dollar of classified spending is multiplied by the Supply Chain "
      "GHG Emission Factor for its NAICS commodity — **v1.4.0, kg CO₂e per "
      "2024 dollar at purchaser price, with margins**: the cradle-to-shelf "
      "average for that commodity across the whole US economy, including "
      "transport, wholesale and retail. These are economy-wide averages from "
      "the USEEIO input-output model — they can't see brands, so a dollar of "
      "artisanal furniture and a dollar of IKEA carry the same factor. All "
      "972 usable codes are browsable on the [industry table](naics.html).\n\n"
      "**This is no longer an EPA dataset.** EPA published through v1.3.0 and "
      "then stopped; v1.4.0 (October 2025) is published by the Cornerstone "
      "Sustainability Data Initiative and authored by Wesley Ingwersen, who "
      "built USEEIO at EPA before leaving in July 2025 — same model lineage, "
      "same structure, different publisher. It moves to 2023 emissions data "
      "and AR6 global warming potentials.\n\n"
      "Factors fell a median 9.5% across the codes we use, but most of that "
      "is the dollar rebase rather than decarbonisation: the release notes "
      "report a −0.9 correlation between a commodity's factor change and its "
      "price change. Rebasing our deflator from 2022 to 2024 dollars pushes "
      "the other way by about 7%, so the net effect on a typical footprint is "
      "small. The two changes ship together for exactly that reason — either "
      "alone would swing totals by more than the correction is worth. "
      "v1.4.0 publishes 2017 NAICS keys only, so the factors are joined onto "
      "the 2022 crosswalk from EPA's last release; 971 of our 972 codes match "
      "(the miss is electric power distribution, which is excluded to the "
      "Home tab anyway).",
      display="972 NAICS codes, kg CO₂e / 2024 USD",
      sources=(("Supply Chain GHG Emission Factors v1.4.0 (Cornerstone)",
                "https://zenodo.org/records/17202747"),
               ("EPA Supply Chain GHG Emission Factors v1.3.0 (last EPA release)",
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
      "are remembered.\n\n"
      "**This has never been measured, and the entry should say so until it "
      "is.** The right metric is not how often the code is exactly right: 972 "
      "codes collapse to 392 distinct factor sets, so many disagreements cost "
      "literally nothing, while a single retail-versus-commodity confusion is "
      "a 3–5× error. What would actually be informative is spend-weighted "
      "factor error against a hand-labelled sample — how close the assigned "
      "factor lands to the right one, weighted by the money involved. Until "
      "that exists, treat the ● review marks as the real quality control.\n\n"
      "One structural caveat: the classification cache is shared across users, "
      "so a wrong answer for a common merchant propagates and, unlike an LLM "
      "call, is not re-derived. Your corrections stay local and always win "
      "locally.",
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
      "Credit-card payments, account transfers, Venmo, paychecks and refunds "
      "are money movement, not consumption — they're detected (by category hint "
      "and merchant pattern), carry no emissions, and are dropped from spending "
      "totals rather than counted as zero-emission spending. Getting this wrong "
      "would double-count every card payment against the charges behind it.\n\n"
      "Two consequences worth naming. **Cash is invisible**: an ATM withdrawal "
      "is correctly not a purchase, but whatever the cash then bought never "
      "appears anywhere, so a cash-heavy household is undercounted with no "
      "sign that anything is missing. And mortgage payments are excluded, "
      "which is right for principal (housing is handled physically in the Home "
      "tab) though the interest is technically a purchased financial service.",
      bias="under",
      code=("src/cf/classify.py",)),

    A("gs.taxes_ignored", "gs", "Taxes and government fees ignored",
      "Tax payments, DMV registration and similar government fees carry no "
      "factor. The honest reason is a **boundary choice**, following the "
      "convention in the household-footprint literature that government "
      "emissions are collective rather than personal — not, as an earlier "
      "version of this note claimed, that taxes buy no production or that the "
      "emissions can't be modelled. They can: the factor set's own "
      "documentation says government sectors were dropped because they "
      "\"were not needed for the designated use case\" (corporate Scope 3 "
      "reporting), which is a scoping decision by the publisher.\n\n"
      "The omission is not small. Government consumption and investment is "
      "roughly 17–18% of US GDP, which puts something like **5–8 t CO₂e per "
      "household per year** outside this boundary — on the order of 10–15% of "
      "the US-average benchmark shown elsewhere in the app. Nothing on a bank "
      "statement covers it, so including it would mean a per-capita "
      "allocation rather than a measurement. Tolls, by contrast, pay for road "
      "operations and are counted (NAICS 488490).",
      bias="under",
      sources=(("EPA/Cornerstone Supply Chain GHG Emission Factors documentation",
                "https://zenodo.org/records/17202747"),),
      code=("src/cf/naics_prep.py",)),

    A("gs.health_insurance", "gs", "Insurance priced at utilization, not premium",
      "A health-insurance premium is mostly a transfer into a pool that buys "
      "care; the EPA \"insurance carriers\" factor (0.033 kg/$) covers only "
      "the carrier's own offices, so applying it to a premium prices the "
      "paperwork and not the medicine. Premiums are expanded instead to the "
      "mix of services they actually fund, grounded in CMS National Health "
      "Expenditure data: **36% hospitals, 26% physician & clinical, 11% "
      "retail prescription drugs, 15% other health services** (dental, labs, "
      "home health, outpatient) **and 12% carrier overhead** — about "
      "0.104 kg CO₂e/$, roughly 3× the bare carrier factor.\n\n"
      "Two details worth stating. Drugs administered inside a hospital or "
      "physician's office are already inside those buckets, so only the "
      "*retail* pharmacy line is additive — including it does not double "
      "count. And ACA medical-loss-ratio rules require 80–85% of premium to "
      "go to claims, which is what bounds carrier overhead near 12%. This "
      "mix is defined once in the registry and imported by the classifier, "
      "so the published figure and the computed one cannot drift apart.",
      value=[{"naics": "622110", "weight": 0.36},   # hospitals
             {"naics": "621111", "weight": 0.26},   # physician & clinical
             {"naics": "325412", "weight": 0.11},   # retail prescription drugs
             {"naics": "621498", "weight": 0.15},   # other outpatient/health services
             {"naics": "524114", "weight": 0.12}],  # carrier overhead
      display="36/26/11/15/12 hospital/physician/drugs/other/carrier",
      sources=(("CMS National Health Expenditure Accounts (2024)",
                "https://www.cms.gov/data-research/statistics-trends-and-reports/national-health-expenditure-data/nhe-fact-sheet"),
               ("ACA medical loss ratio, 45 CFR Part 158",
                "https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-B/part-158")),
      code=("src/cf/classify.py",)),

    A("gs.eeio_limitations", "gs", "What spend-based accounting cannot see",
      "Goods & Services is 30–40% of a typical footprint here and rests on "
      "input-output modelling, which has limits worth stating plainly rather "
      "than leaving to be discovered.\n\n"
      "**Price stands in for quantity.** A dollar is the only input, so a $24 "
      "bottle of detergent carries exactly twice the emissions of a $12 one. "
      "Buying secondhand is charged as if new; a sale reads as a reduction; "
      "switching to a cheaper supplier \"cuts\" your footprint. This is the "
      "limitation most likely to mislead someone taking action, because it "
      "rewards spending less rather than consuming less — and those diverge "
      "exactly where you have premium low-carbon options.\n\n"
      "**The resolution is coarser than the code list suggests.** The 972 "
      "codes collapse to 392 distinct factor sets, so many pairs of "
      "\"different\" industries are numerically identical. Errors that stay "
      "inside one factor set cost nothing; the ones that matter are "
      "retail-versus-commodity and included-versus-excluded.\n\n"
      "**Imports are priced at domestic technology.** The model behind our "
      "factor set carries no import-specific emission factors, so a "
      "Vietnamese-made shirt is charged as though it were made under the US "
      "grid and US industrial efficiency. Since US production is generally "
      "less carbon-intensive per dollar than what the US imports, this biases "
      "the number **down**, most for the import-heavy categories: apparel, "
      "electronics, furniture, toys.\n\n"
      "What follows is that the app is far more trustworthy about *shape* "
      "than *level* — which categories dominate, and how this year compares "
      "with last — than about any single merchant or the absolute total. "
      "Read the goods figure as two significant figures at best.",
      bias="varies",
      sources=(("USEEIO model registry (import factor status)",
                "https://github.com/USEPA/USEEIO/blob/master/models.md"),
               ("EPA 600/R-24/116, Estimating Embodied Environmental Flows in Imports",
                "https://www.epa.gov/land-research/us-environmentally-extended-input-output-useeio-technical-content"),
               ("Design notes §7 (known limitations)", REPO + "naics_mapping_design_notes.md")),
      code=("src/cf/naics_prep.py", "site/v2-template.html")),

    A("gs.counted_elsewhere", "gs", "Fuel, flights, utilities, food leave G&S",
      "Transactions whose emissions are modeled physically in another tab are "
      "excluded from Goods & Services to prevent double counting: gas stations "
      "(NAICS 457) and airlines (481) → Travel; electric, gas, and water "
      "utilities (2211/221210/2213) → Home; groceries (445/311/312) and "
      "restaurants (722) → Food's diet model. Each tab shows the excluded "
      "spend as a cross-check against your physical inputs. Grocery-heavy "
      "general merchandisers are handled by splitting the ticket rather than "
      "excluding it — see the basket entry.\n\n"
      "Cost of the choice: a restaurant bill is mostly *service* — the "
      "building, the staff, the dishwasher — and only about a third food, but "
      "excluding the whole transaction drops that overhead along with the "
      "meal. At roughly 0.13–0.17 kg/$ of genuine non-food emissions, a "
      "household spending $3,500 a year on restaurants loses **450–600 kg** "
      "that nothing else picks up. Not \"slight\", as an earlier version of "
      "this note put it.",
      bias="under",
      code=("src/cf/naics_prep.py", "site/v2-template.html")),

    # ------------------------------------------------------------------
    # Travel
    # ------------------------------------------------------------------
    A("travel.vehicle_fuel", "travel", "Vehicle fuel factors",
      "Gasoline 8.87 kg CO₂/gallon tailpipe + 2.31 upstream (extraction, "
      "refining, transport); diesel 10.21 + 2.36. Fuel burn is miles ÷ MPG — "
      "your real MPG, not the sticker, if you know it. Gasoline is "
      "CoolClimate's measured value and sits inside every authoritative band "
      "(EPA 8.78, EPA equivalencies 8.887); the upstream uplift of ~26% "
      "matches EPA's RFS2 life-cycle figures via ICCT. **Diesel comes from EPA "
      "instead**: CoolClimate's calculator returns 11.30, which is 10.21 × "
      "(128,488 ÷ 116,090 Btu/gal) — the diesel-to-gasoline energy ratio "
      "applied to a factor already expressed per gallon of diesel, i.e. "
      "counted twice. Tailpipe figures are CO₂ only; CH₄ and N₂O add under "
      "0.2% for gasoline cars and ~2% for diesel.",
      value={1: {"label": "Gasoline", "d": 8.874, "u": 2.307},
             2: {"label": "Diesel", "d": 10.21, "u": 2.360},
             3: {"label": "Electric", "d": 0, "u": 0}},
      display="gas 8.87+2.31, diesel 10.21+2.36 kg/gal",
      sources=(EPA_HUB, ICCT_WTW, JK2011, CC_API),
      code=("data/coolclimate_travel_factors.json", "site/v2-template.html")),

    A("travel.ev_grid_missing", "travel", "EVs count zero fuel emissions",
      "CoolClimate's vehicle model prices electric vehicles' fuel at zero and "
      "does not add their charging load anywhere; the model is duplicated "
      "as-is. This is a missing scope, not an approximation. A typical EV "
      "(~0.32 kWh/mile real-world) on the US average grid causes about "
      "**1.1 t CO₂e per 10,000 miles**, ranging from ~0.35 t on the cleanest "
      "regional grids to ~1.8 t on the dirtiest — so if you drive an EV, add "
      "its kWh to your Home electricity, where your own ZIP's hourly grid "
      "factor will price it correctly.",
      bias="under",
      sources=(JK2011, CC_API),
      code=("site/v2-template.html",)),

    A("travel.vehicle_manufacture", "travel", "Vehicle manufacturing per mile",
      "Building and maintaining the vehicle, amortized over the miles it "
      "drives: **0.056 kg CO₂e/mile for petrol and diesel, 0.085 for "
      "electric**. The petrol figure is CoolClimate's and holds up well — "
      "rebuilding it from GREET vehicle-cycle parameters via ICCT gives "
      "0.046–0.051 for US cars and SUVs over their real lifetime mileage, so "
      "0.056 sits just above a defensible band.\n\n"
      "Electric vehicles carry more, not less: an 85 kWh pack at R&D GREET "
      "2024's US cell intensity of 64 kg CO₂e/kWh adds roughly 0.029 kg/mile "
      "on top of the glider. Using one powertrain-blind number understated "
      "EVs by about 1.5×. Note this is the opposite direction from their "
      "operating emissions, which the app currently omits entirely.\n\n"
      "Amortizing per mile has a consequence worth naming: a barely-driven "
      "second car is charged almost nothing here (2,000 miles/year → ~110 kg) "
      "even though its real embodied burden is ~8 t spread over its life, "
      "nearer 460 kg/year. The per-mile figure is right for comparing how you "
      "travel, and wrong for deciding whether to keep a car.",
      value={"ice": 0.056, "ev": 0.085}, display="0.056 kg/mi petrol · 0.085 EV",
      sources=(JK2011, CC_API),
      code=("data/coolclimate_travel_factors.json",)),

    A("travel.ground_transit", "travel", "Transit factors",
      "Per passenger-mile: simple \"public transit\" 225 g (CoolClimate, "
      "corroborated by an all-mode NTD bottom-up of 243–283 g); detailed mode "
      "— **bus 390 g**, transit rail 93 g, commuter rail 134 g, intercity "
      "rail (Amtrak) 97 g.\n\n"
      "Bus does not come from the EPA Factors Hub, and the reason matters. "
      "The Hub's bus row draws passenger-miles from FHWA Highway Statistics "
      "Table VM-1 — *every* US bus, school and intercity coach included — so "
      "its 66 g implies 26–35 people aboard. A US transit bus actually "
      "carries 6.6 (NTD RY2023: 12,344 M passenger-miles over 1,863 M vehicle "
      "revenue miles) at about 3.5 mpg, which works out near 390 g. FTA's own "
      "transit-climate report (0.32 kg) and ORNL's energy-intensity series "
      "(0.28–0.36 kg) independently agree. CoolClimate's own bus figure of "
      "1.3 g is unusable — roughly 100× low.\n\n"
      "Rail comes from EPA's Hub, which sources intercity directly from "
      "Amtrak. Those figures rest on 2019 passenger-miles, so post-COVID "
      "occupancy makes them optimistic: recomputing from NTD RY2023 gives "
      "106 g transit rail and 222 g commuter rail. The spread across these "
      "modes is driven almost entirely by how full the vehicle is, not by "
      "its technology, so a single national average is a blunt yardstick — "
      "a packed subway is far below the number shown and a near-empty "
      "suburban line far above. All figures are tailpipe/plug only, with no "
      "upstream fuel-production uplift (~21% for diesel).",
      value={"publictrans": 0.2253, "bus": 0.39, "transit": 0.093,
             "commuter": 0.134, "intercity": 0.0968},
      display="bus 390 g · rail 93–134 g/pax-mile",
      bias="under",
      sources=(EPA_HUB, NTD,
               ("FTA, Public Transportation's Role in Responding to Climate Change",
                "https://www.transit.dot.gov/regulations-and-programs/environmental-programs/transit-environmental-sustainability")),
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
      "$4.00/gallon, economy air fare ~17.5¢/mile, transit ~29¢/mile, and "
      "rideshare ~$2/mile. Rough yardsticks to catch a missing vehicle or "
      "forgotten trip — not accounting inputs.\n\n"
      "Gasoline is the trailing-year average rather than the spot price, "
      "since the panel converts a past year of card spend. Air fare comes "
      "from BTS 2025 domestic passenger revenue over revenue passenger-miles "
      "(~16.7¢, ~17.5¢ including bag and change fees); transit from APTA's "
      "national fare revenue over passenger-miles. The rideshare figure is an "
      "unsourced estimate — city TNC datasets would settle it. These move "
      "with the market; figures are as of September 2026.",
      value={"gas_per_gal": 4.00, "air_per_mile": 0.175,
             "transit_per_mile": 0.29, "taxi_per_mile": 2},
      display="$4.00/gal · 17.5¢/air-mile · 29¢/transit-mile",
      sources=(("EIA weekly US retail gasoline prices",
                "https://www.eia.gov/petroleum/gasdiesel/"),
               ("BTS airline financial data (2025)",
                "https://www.bts.gov/topics/airlines-and-airports/airline-financial-data"),
               NTD),
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
      "85% gas.\n\n"
      "Per-fuel combustion intensities are EIA's fleet averages — coal 1,048, "
      "gas 435, petroleum 1,116 g CO₂/kWh (the previous petroleum figure of "
      "700 was 37% low). They are CO₂-only against a CO₂e calibration target, "
      "roughly a 1% difference that α absorbs.\n\n"
      "**On the data year:** EIA-930 for 2025 is complete and published, and "
      "we are deliberately not using it yet. The reconstruction has to be "
      "calibrated against a *measured* annual rate from the same calendar "
      "year, and the newest eGRID available is 2024 — pairing a 2025 "
      "reconstruction with a 2024 target makes α absorb a year of real fleet "
      "change as though it were reconstruction error, which we measured: "
      "LDWP's α went to 1.53 and Arizona's to 1.52 under the mismatch, "
      "against 1.14 each when the years line up. The shape year advances when "
      "eGRID2025 does.",
      display="8,784 hours × fuel mix, per BA",
      sources=(("EIA-930 Hourly Electric Grid Monitor", "https://www.eia.gov/electricity/gridmonitor/"),
               ("EIA FAQ #74, CO₂ per kWh by fuel", "https://www.eia.gov/tools/faqs/faq.php?id=74&t=11")),
      code=("zip2co2_2/make_real_cache.py", "zip2co2_2/gridcarbon/core.py")),

    A("elec.alpha_calibration", "electricity", "Calibrated to eGRID (α), which doubles as validation",
      "The reconstructed hourly curve has the right *shape* but an uncertain "
      "*level* (fuel factors are national fleet averages; CISO's gas fleet is "
      "newer than average). One scalar α per BA rescales the curve so its "
      "annual average exactly matches the measured eGRID rate — level from "
      "eGRID, shape from EIA-930. α also doubles as an integrity check: "
      "|α−1| measures reconstruction error, and the median BA now sits within "
      "**7.8%** of its measured rate, with 55 of 61 inside the shipping band "
      "(ERCO 0.98, MISO 0.96, NYIS 0.95, PJM 0.93). BAs outside [0.70, 1.45] "
      "indicate a structural data problem and don't ship an hourly shape.\n\n"
      "**Provenance caveat.** The calibration target is eGRID2024, and EPA "
      "has not released it — as of September 2026 it is eight months past "
      "EPA's own stated January 2026 date. These rates come from the "
      "community edition produced by running EPA's MIT-licensed eGRID code "
      "on March 2026 inputs and published on Zenodo. Same code, same inputs, "
      "different publisher, so \"calibrated to EPA's published rate\" is no "
      "longer literally true. It is still much better than calibrating "
      "against 2023: refreshing the target also let the calibration and the "
      "shape use the *same calendar year* for the first time, which is what "
      "α assumes. Moving to eGRID2024 shifted rates by a median −2.4%, and "
      "much more in places — CISO −11%, PNM −24%, BPAT −18%.",
      value={"band_lo": 0.70, "band_hi": 1.45},
      display="α ∈ [0.70, 1.45] to ship hourly · eGRID2024",
      sources=(("eGRID2024 community edition (EPA's code, Cornerstone build)",
                "https://zenodo.org/records/18968658"),
               ("EPA eGRID", "https://www.epa.gov/egrid"),
               ("Alpha validation table", REPO + "zip2co2_2/README.md")),
      code=("zip2co2_2/gridcarbon/core.py", "scripts/build_gridcarbon_web.py")),

    A("elec.production_based", "electricity", "Production-based: imports not counted",
      "Intensity covers generation *inside* your balancing authority only. "
      "Consumption-based accounting would price imports too, and a credible "
      "national method exists — de Chalendar, Taggart & Benson (PNAS 2019) "
      "reconstruct hourly consumption-based CO₂ for every US BA, with an "
      "MIT-licensed implementation. Two things argue against adopting it "
      "here: its hosted data feed is no longer maintained (the portal's "
      "certificate expired in April 2026 and the hosting account is "
      "suspended), and more fundamentally, a consumption-based reconstruction "
      "cannot be calibrated against eGRID, whose published rates are "
      "production-based — that mismatch is exactly what our α check is "
      "designed to catch, and it blew out on a third of BAs when we tried "
      "pricing imports at CARB's 428 g default (a California construct that "
      "would price Seattle's BPA-hydro imports as gas).\n\n"
      "Cost of the choice: import-heavy BAs are understated — CISO by roughly "
      "10–15% against consumption accounting — and the evening-import share "
      "of the daily shape is lost. This matches how eGRID location-based "
      "rates are normally used, though it is worth noting the GHG Protocol's "
      "draft Scope 2 revision would prioritise consumption-based factors.",
      bias="under",
      sources=(("de Chalendar, Taggart & Benson 2019, PNAS 116(51):25497",
                "https://www.pnas.org/doi/10.1073/pnas.1912950116"),
               ("Deviation #1, gridcarbon README", REPO + "zip2co2_2/README.md")),
      code=("zip2co2_2/gridcarbon/build.py",)),

    A("elec.storage_split", "electricity", "Batteries split out of \"Other\"",
      "CAISO and others report grid batteries inside \"Other Fuel Sources\", "
      "which otherwise gets priced like gas — so evening discharge of stored "
      "midday solar counted as fossil. Per local day, the excess of Other "
      "above its daily minimum is reclassified as storage (0 combustion, "
      "30 g/kWh); flat fossil \"Other\" (waste-coal BAs) has no daily cycle "
      "and is untouched. Validation: CISO's α moved 0.73 → 0.97 and "
      "post-battery evenings (~235 g) now correctly read cleaner than deep "
      "night (~279 g).\n\n"
      "The 30 g is **battery manufacturing amortized over lifetime "
      "throughput** and nothing else (utility-scale Li-ion, 15–60 g "
      "depending on cycle life; modern LFP sits at the low end). The "
      "charging mix and round-trip losses are deliberately excluded: the "
      "hourly reconstruction already counts charging energy in the hour it "
      "was generated, so pricing it again on discharge would double count. "
      "The convention is that stored energy is charged to the hour it was "
      "generated, which is what makes evening discharge correctly read as "
      "midday solar.",
      display="daily-min split; storage at 30 g/kWh",
      sources=(("Hiremath et al. 2015, Environ. Sci. Technol. 49(8):4825",
                "https://pubs.acs.org/doi/10.1021/es504572q"),
               ("Deviation #3, gridcarbon README", REPO + "zip2co2_2/README.md")),
      code=("zip2co2_2/make_real_cache.py", "zip2co2_2/gridcarbon/data/fuel_factors.csv")),

    A("elec.load_shape", "electricity", "Weighted by when households use power",
      "The hourly intensity is averaged using a typical home's hourly usage — "
      "households use most power in the evening, when solar is gone and gas is "
      "ramping, so the factor runs a little above or below the flat annual "
      "average depending on the grid (CISO +1.0%, PNM +9.9%, Portland −4.7%). "
      "The weights only decide which hours count more; your kWh input supplies "
      "all the scale.\n\n"
      "The shape comes from **NREL's ResStock 2025 release**, summing all five "
      "residential building types per state so it reflects the actual housing "
      "stock rather than a handful of prototype houses. It replaces OpenEI's "
      "TMY3 residential profiles, which OpenEI has formally deprecated — those "
      "were five EnergyPlus models with documented defects that landed on this "
      "app's own stations, including no air conditioning at all in the Marine "
      "climate region (Portland, Seattle) and a Tampa heating season applied "
      "across the Hot-Humid zone (Dallas). All 61 balancing authorities now "
      "use ResStock; every one of the 48 that ships an hourly shape peaks "
      "between 18:00 and 23:00 local, as a residential profile should.",
      display="NREL ResStock 2025, per-BA local time",
      sources=(("NREL End-Use Load Profiles / ResStock 2025 release 1",
                "https://data.openei.org/submissions/4520"),
               ("OpenEI TMY3 profiles (deprecated, previously used)",
                "https://data.openei.org/submissions/153")),
      code=("zip2co2_2/make_real_cache.py", "zip2co2_2/gridcarbon/core.py")),

    A("elec.seasonal_limit", "electricity", "Seasonality is TMY3's typical home, not yours",
      "The load weighting carries a season, and it can dominate the daily "
      "cycle: Portland (PGE) comes out **−6%** vs flat because the "
      "hydro-backed grid is dirtiest in late summer (reservoirs low, ~500 g) "
      "and cleanest during spring runoff (~216 g), while a typical home peaks "
      "in winter. The mirror case is PNM, where Albuquerque's summer air "
      "conditioning lands squarely in the dirty months.\n\n"
      "But that seasonality is a *typical* home's, not yours, and how your "
      "home heats changes it — a heavily electrically-heated house has a "
      "different winter-to-summer ratio than a gas-heated one, which shifts "
      "the result by a percentage point or two and can flip its sign "
      "entirely in some regions. We don't currently model which you have, so "
      "the app cannot tell you which direction your household moves. If your "
      "utility offers interval data (Green Button), the advanced upload "
      "replaces the assumed seasonality with your actual meter and removes "
      "the guess.",
      bias="varies",
      sources=(("Seasonal covariance, gridcarbon README", REPO + "zip2co2_2/README.md"),),
      code=("zip2co2_2/gridcarbon/core.py", "site/v2-template.html")),

    A("elec.upstream", "electricity", "Upstream fuel-cycle emissions added",
      "Combustion isn't the whole story: extracting, processing, and "
      "transporting fuels adds emissions (methane leakage dominates for gas), "
      "and non-combusting sources carry manufacturing and construction "
      "emissions. Mix-weighted factors are added after calibration — upstream "
      "only for fossil fuels, full lifecycle for wind, solar, nuclear and "
      "hydro, so this is a **lifecycle adder rather than a purely fuel-cycle "
      "one**. Across the 49 balancing authorities with a usable eGRID rate it "
      "adds a **median +19.8%** (10th percentile +9.0%, 90th +33.6%): CISO "
      "+34.1%, NYIS +27.6%, ISNE +26.5%, PJM +17.7%, MISO +13.2%. It is "
      "largest on the *cleanest* grids, because a roughly fixed absolute "
      "adder divides into a smaller combustion number. The gas figure of "
      "97 g/kWh implies about a 1.8% supply-chain leakage rate at GWP-100, "
      "between EPA's inventory (~1.3%) and Alvarez et al.'s measured 2.3%; on "
      "a 20-year horizon it would roughly triple.",
      display="lifecycle adder, median +19.8%",
      sources=(("IPCC AR5 WG3 Annex III Table A.III.2", "https://www.ipcc.ch/site/assets/uploads/2018/02/ipcc_wg3_ar5_annex-iii.pdf"),
               ("Alvarez et al. 2018, Science 361:186", "https://www.science.org/doi/10.1126/science.aar7204")),
      code=("zip2co2_2/gridcarbon/data/fuel_factors.csv", "scripts/build_gridcarbon_web.py")),

    A("elec.delivery_loss", "electricity", "Delivery losses",
      "Intensities are computed at the busbar; 4.4% of US generation is lost "
      "in transmission and distribution before it reaches your meter, so "
      "factors are grossed up by 1/(1−0.044). Your kWh input is what your "
      "meter (and bill) shows.\n\n"
      "This is eGRID's grid gross loss, which is the right basis and a "
      "little lower than EIA's headline ~5%: eGRID divides estimated losses "
      "by total disposition *after* subtracting direct use (electricity that "
      "never transits the grid and so cannot be lost) and net interstate "
      "exports. Regional variation is small — 4.36% Western, 4.41% Eastern, "
      "4.42% ERCOT, 4.76% Hawaii — so one national figure is fine. The "
      "series is noisy year to year (5.1% in 2022, 4.2% in 2023, 4.4% in "
      "2024), and 4.2% happened to be its low point.",
      value=0.044, display="+4.6% (grid gross loss 4.4%)",
      sources=(("EPA eGRID technical guide §3.5 (grid gross loss)", "https://www.epa.gov/egrid"),),
      code=("scripts/build_gridcarbon_web.py",)),

    A("elec.ship_gate", "electricity", "When hourly data isn't trusted: flat fallback",
      "A BA ships its hourly shape only if α is in band, the load-weighting "
      "adjustment is |uplift| ≤ 12%, and ≥ 90% of hours reconstructed — 48 of "
      "61 BAs pass. The rest (plus ZIPs with no BA mapping) degrade to EPA's "
      "flat annual eGRID rate plus upstream: level right, no claim about "
      "hours. Better no shape than a wrong shape.",
      value={"uplift_max_pct": 12.0, "coverage_min": 0.90},
      display="|uplift| ≤ 12% · coverage ≥ 90% · 48/61 BAs ship",
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
      "5.31 kg CO₂e/therm combustion + 1.50 upstream (production and "
      "leakage), both re-sourced away from CoolClimate.\n\n"
      "Combustion is EPA's 53.06 kg CO₂/mmBtu plus CH₄ and N₂O, i.e. 5.311 "
      "per therm. CoolClimate's 5.470 is very close to EIA's coefficient of "
      "5.481 kg per *hundred cubic feet* — and a CCF is 1.037 therms, so it "
      "looks like a per-CCF factor applied per therm.\n\n"
      "Upstream is the genuinely contested number. Our previous 0.766 implied "
      "a supply-chain methane loss rate of only about 0.5%, below even NETL's "
      "inventory-anchored 0.74% and well below every measurement campaign: "
      "Alvarez et al. (2018) found 2.3% of gross production, Sherwin et al. "
      "(2024) 2.43% from ~1M aerial site measurements, and MacKay et al. "
      "(2026, MethaneAIR) 1.6% across 12 basins — roughly 4× EPA's inventory "
      "for the same areas. The 1.50 used here sits deliberately between the "
      "inventory and the measurements (≈1.7% loss at GWP-100), because "
      "measurement campaigns oversample high-emitting basins. On a 20-year "
      "horizon this would roughly triple. Where your gas comes from matters "
      "more than any national average can express.",
      value={"d": 5.311, "u": 1.50},
      display="5.31 + 1.50 kg/therm",
      bias="varies",
      sources=(EPA_HUB,
               ("NETL, Life Cycle Analysis of Natural Gas… U.S. 2020 Emissions Profile (Jan 2025)",
                "https://www.netl.doe.gov/projects/files/LifeCycleAnalysisofNaturalGasExtractionandPowerGenerationUS2020EmissionsProfile_012425.pdf"),
               ("Alvarez et al. 2018, Science 361:186", "https://www.science.org/doi/10.1126/science.aar7204"),
               ("Sherwin et al. 2024, Nature 627:328", "https://www.nature.com/articles/s41586-024-07117-5")),
      code=("site/v2-template.html",)),

    A("home.oil", "home", "Heating oil per gallon",
      "10.24 kg CO₂e/gallon combustion + 3.07 upstream. Combustion is EPA's "
      "distillate fuel oil No. 2 factor (0.138 mmBtu/gal × 73.96 kg CO₂/mmBtu "
      "plus CH₄ and N₂O), independently confirmed by EIA's coefficient of "
      "10.19 to within 0.2%. CoolClimate's 11.79 is 15% higher and could not "
      "be reproduced from any fuel in EPA's table — not No. 1, No. 4, "
      "residual No. 5 or 6, kerosene, heavy gas oils or asphalt — so it is "
      "replaced rather than carried. Upstream of 3.07 does check out against "
      "GREET well-to-pump for distillate (~22 g CO₂e/MJ, dominated by "
      "refining) and is kept.",
      value={"d": 10.240, "u": 3.066},
      display="10.24 + 3.07 kg/gal",
      sources=(EPA_HUB,
               ("EIA carbon dioxide emission coefficients",
                "https://www.eia.gov/environment/emissions/co2_vol_mass.php"),
               ("Argonne GREET / CA-GREET distillate pathway",
                "https://greet.anl.gov/")),
      code=("site/v2-template.html",)),

    A("home.construction", "home", "Home construction amortized",
      "Building and maintaining the structure is amortized at 0.93 kg CO₂e "
      "per square foot per year (CoolClimate). A 1,500 sqft home carries "
      "~1.4 t/yr regardless of energy use — one reason square footage is a "
      "bigger lever than insulation marketing suggests.",
      value=0.93, display="0.93 kg/sqft/yr",
      sources=(JK2011, CC_API),
      code=("site/v2-template.html",)),

    A("home.water_omitted", "home", "Water & sewer deliberately omitted",
      "Municipal water and wastewater run about 4 kWh per 1,000 gallons, "
      "which for a typical household (~110,000 gal/yr) is roughly 160 kg "
      "CO₂e/yr at the US average grid. But electricity is not the larger "
      "share: treatment and effluent discharge emit methane and nitrous "
      "oxide directly, and EPA's own GHG Inventory puts domestic wastewater "
      "at about **105 kg CO₂e per person per year** — with recent "
      "measurement work suggesting the real figure is higher still. A "
      "household therefore sits near **0.4–0.9 t CO₂e/yr**, about 1% of a "
      "total footprint.\n\n"
      "It is still left out, for two reasons: it isn't actionable from a "
      "utility bill the way kWh and therms are, and CoolClimate's much "
      "larger water figure is expenditure-based and double-counts water "
      "heating your gas and electricity inputs already cover. But it is an "
      "omission, not a rounding error — an earlier version of this note "
      "claimed 25–110 kg/yr by counting only the electricity.",
      bias="under",
      sources=(("EPA Inventory of U.S. GHG Emissions and Sinks 1990–2022, ch. 7 (Waste)",
                "https://www.epa.gov/ghgemissions/inventory-us-greenhouse-gas-emissions-and-sinks"),
               ("EPA WaterSense (household water use)", "https://www.epa.gov/watersense")),
      code=("site/v2-template.html",)),

    A("home.crosscheck_rates", "home", "Home cross-check price assumptions",
      "Utility spend from your upload is converted to implied usage at US "
      "average residential rates — $0.182/kWh, $1.48/therm, $3.98/gallon of "
      "heating oil — to compare against what you entered. These come from "
      "EIA's published series (electricity 2026 year-to-date, gas the 2025 "
      "volume-weighted annual average, oil the 2025–26 heating season) and "
      "replace CoolClimate's much older assumptions, which had drifted 23% "
      "high on electricity and 36% high on gas.\n\n"
      "Treat it as a consistency check, not a bill audit: residential "
      "electricity spans nearly 4× across states (North Dakota ~12¢ to "
      "Hawaii ~46¢), so your own tariff may sit well away from the average. "
      "Figures are as of September 2026.",
      value={"kwh": 0.1816, "therm": 1.479, "oil_gal": 3.98},
      display="$0.182/kWh · $1.48/therm",
      sources=(("EIA Electric Power Monthly, Table 5.6.B",
                "https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_b"),
               ("EIA Natural Gas Monthly, residential price",
                "https://www.eia.gov/dnav/ng/hist/n3010us3a.htm"),
               ("EIA Heating Oil and Propane Update",
                "https://www.eia.gov/petroleum/heatingoilpropane/")),
      code=("site/v2-template.html",)),

    # ------------------------------------------------------------------
    # Food
    # ------------------------------------------------------------------
    A("food.model", "food", "Diet model: servings × emissions per serving",
      "You give weekly servings; each is multiplied by the emissions of one "
      "serving of that food. **No dollars and no calories enter the "
      "calculation** — the calorie figure shown next to each row is a "
      "plausibility readout, not an input to the maths.\n\n"
      "Factors are **Poore & Nemecek 2018 medians**, the standard food-LCA "
      "reference: ~1,530 studies covering 38,700 farms in 119 countries, "
      "re-run to one common cradle-to-retail boundary including land-use "
      "change. The single boundary is the point — individual food LCAs draw "
      "theirs differently, so a beef paper and a lentil paper cannot "
      "legitimately be compared, while these can. That comparison is the "
      "advice this tab exists to give.\n\n"
      "**Median rather than mean, deliberately.** P&N's means are pulled "
      "upward by a long tail of extensive tropical systems — their global "
      "beef mean is 99 kg CO₂e/kg against a median of 60. Their own 12 US "
      "beef observations have a median of 59, the 56th percentile of the "
      "global distribution, so the median is both the more robust estimator "
      "and very close to what American production actually measures. That "
      "single choice does the work an explicit US correction would, without "
      "importing a second study's system boundary.\n\n"
      "Serving sizes are the FDA's reference amounts (21 CFR 101.12; USDA "
      "FSIS 9 CFR 317.312 for meat), which resolve the trap in this "
      "conversion: the regulation sets the same portion as **85 g cooked or "
      "110 g uncooked**, and P&N's meat unit is raw retail weight, so a "
      "serving is 110 g of their unit. Grains and legumes run the other way — "
      "140 g of cooked rice is 45 g dry, and their rice unit is dry.\n\n"
      "Two honest caveats. Seafood uses global farmed-fish values because "
      "**80% of US seafood is imported** (NOAA) and P&N has no North American "
      "observations — global is the correct choice here, not a compromise. "
      "And this model puts meat at ~70% of food emissions against Heller's "
      "NHANES estimate of 57%; ours is cradle-to-retail with land-use change "
      "while Heller's is farm-gate, which explains much but likely not all of "
      "the gap.",
      value={
        # kg = kg CO2e per serving (P&N median kg/kg x serving grams).
        # g   = serving mass in P&N's functional unit (raw/as-purchased).
        # cps = calories per serving, for the plausibility readout only.
        # def = US-average servings/week, from per-capita availability.
        "beeflamb":  {"label": "Beef & lamb", "kg": 6.600, "g": 110, "cps": 213, "def": 4.5,
                      "src": "P&N median, beef+lamb blended by US availability"},
        "pork":      {"label": "Pork", "kg": 1.163, "g": 110, "cps": 213, "def": 3.8,
                      "src": "P&N median"},
        "poultry":   {"label": "Poultry", "kg": 0.827, "g": 110, "cps": 190, "def": 5.4,
                      "src": "P&N median"},
        "eggs":      {"label": "Eggs", "kg": 0.210, "g": 50, "cps": 78, "def": 5.4,
                      "src": "P&N median, one large egg"},
        "fish":      {"label": "Fish & seafood", "kg": 0.868, "g": 110, "cps": 180, "def": 1.5,
                      "src": "P&N farmed median; 80% of US seafood is imported"},
        "dairy":     {"label": "Dairy", "kg": 0.636, "g": 240, "cps": 120, "def": 10.5,
                      "src": "P&N milk median, milk-equivalent serving"},
        "legume":    {"label": "Legumes, tofu, nuts", "kg": 0.090, "g": 60, "cps": 150, "def": 1.8,
                      "src": "P&N blend: pulses, tofu, nuts"},
        "fruits":    {"label": "Fruits", "kg": 0.084, "g": 140, "cps": 70, "def": 12.4,
                      "src": "P&N blend: apples, citrus, bananas, berries"},
        "veggies":   {"label": "Vegetables", "kg": 0.038, "g": 85, "cps": 35, "def": 12.4,
                      "src": "P&N blend: brassicas, root, other, tomatoes"},
        "grains":    {"label": "Grains & baked goods", "kg": 0.064, "g": 50, "cps": 130, "def": 28.6,
                      "src": "P&N wheat & rye median"},
        "otherfood": {"label": "Snacks, drinks, oils, sugar", "kg": 0.130, "g": None, "cps": 200,
                      "def": 23.6,
                      "src": "composite ~0.65 kg/1000 kcal (sugar, oil, flour, maize, beer)"},
      },
      display="beef 6.60 → vegetables 0.04 kg per serving",
      sources=(("Poore & Nemecek 2018, Science 360(6392):987–995",
                "https://www.science.org/doi/10.1126/science.aaq0216"),
               ("Poore 2018, full model (per-observation database), Univ. of Oxford",
                "https://doi.org/10.5287/bodleian:0z9MYbMyZ"),
               ("FDA reference amounts, 21 CFR 101.12",
                "https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-101/subpart-A/section-101.12"),
               ("NOAA Fisheries of the United States (seafood import share)",
                "https://www.fisheries.noaa.gov/national/sustainable-fisheries/fisheries-united-states")),
      code=("site/v2-template.html",)),

    A("food.snacks_composite", "food", "The snacks and drinks bucket is constructed",
      "Everything that isn't a recognisable food group — sugar, cooking oil, "
      "crisps, soft drinks, beer, baked goods — lands in one row at **0.65 kg "
      "CO₂e per 1,000 kcal**. Unlike the other rows there is no \"kg of "
      "snacks\" to measure, so this number is built rather than looked up: a "
      "basket of refined sugar (0.65 per 1,000 kcal), vegetable oil (0.71), "
      "wheat flour (0.58), maize (0.41) and beer (2.74), weighted 30/20/35/10/5 "
      "by calories.\n\n"
      "The reassuring part is how little the weighting matters. Every "
      "component except beer sits between 0.41 and 0.71, so the basket would "
      "have to be badly wrong to move the answer much — the bucket is "
      "heterogeneous in content but nearly homogeneous in carbon per calorie. "
      "Using P&N medians instead of means gives 0.61 rather than 0.72. The "
      "basket shares are our estimate, not a sourced figure, and this is the "
      "least firmly grounded row in the model — it is also about 6% of a "
      "default food footprint, down from 22% under the previous model.",
      value=0.65, display="0.65 kg CO₂e / 1,000 kcal",
      bias="varies",
      sources=(("Poore & Nemecek 2018, per-product retail values",
                "https://www.science.org/doi/10.1126/science.aaq0216"),),
      code=("site/v2-template.html",)),

    A("food.swaps", "food", "What the diet levers actually show",
      "With every row on one boundary, the comparisons the tab exists to make "
      "become meaningful. A serving of beef or lamb carries **6.6 kg CO₂e**; "
      "the same serving of pork carries 1.16, chicken 0.83, and beans or tofu "
      "0.09. So swapping one weekly beef dinner for legumes saves about "
      "340 kg a year per person — more than a third of a typical household's "
      "entire annual food footprint per head.\n\n"
      "The ruminant/non-ruminant line is the one that matters, and it is "
      "biological rather than agricultural: cattle and sheep ferment feed in a "
      "rumen and emit methane directly, while pigs and chickens do not. That "
      "is why pork sits closer to chicken than to beef despite both being red "
      "meat, and it is why \"eat less meat\" is weaker advice than \"eat less "
      "beef\".\n\n"
      "An earlier version of this model — inherited from CoolClimate — "
      "compressed this spread about fivefold, showing beef at only ~2× "
      "chicken. Notably CoolClimate's own published work says beef is "
      "\"nearly 10 times\" chicken per gram; it was their deployed calculator, "
      "not their research, that had lost the signal.",
      sources=(("Poore & Nemecek 2018", "https://www.science.org/doi/10.1126/science.aaq0216"),
               ("Jones, Kammen & McGrath 2008 (the ~10x claim)",
                "https://escholarship.org/uc/item/55b3r1qj")),
      code=("site/v2-template.html",)),

    A("food.crosscheck_grocery", "food", "Grocery cross-check composite",
      "The cross-check converts uploaded grocery spend (2024 USD) at a "
      "**0.53 kg CO₂e/$** commodity composite — a diet-mix assumption, since "
      "spend can't see what's in the cart. Rebuilt bottom-up from the factor "
      "set this repo ships, weighted by BLS Consumer Expenditure "
      "food-at-home shares: cereals and bakery 12.5% × 0.268, meat/fish/eggs "
      "22.7% × 0.914, dairy 10.1% × 0.740, fruit and vegetables 15.3% × "
      "0.433, everything else 39.4% × 0.375. Restaurants use their own "
      "factors: **0.12–0.22 kg/$** (full-service 0.168, limited-service "
      "0.220, the rest of NAICS 722 at 0.117) — mostly the service, "
      "diet-blind. An earlier version of this note quoted 0.23–0.36 for "
      "restaurants, which contradicted the data in this repo.\n\n"
      "A strong disagreement with the diet estimate usually means heavy "
      "dining out (undercounted there) or premium groceries (overcounted). "
      "Note also that spend-based estimates run structurally below process "
      "LCA, so this cross-check reads low against the diet model by "
      "construction.",
      value=0.53, display="0.53 kg CO₂e / 2024 $",
      sources=(("Supply Chain GHG Emission Factors v1.4.0",
                "https://zenodo.org/records/17202747"),
               ("BLS Consumer Expenditures 2024", "https://www.bls.gov/news.release/cesan.nr0.htm"),
               ("Design notes §3", REPO + "naics_mapping_design_notes.md")),
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
      "2.83 t CO₂e avoided per short ton of mixed recyclables vs landfilling "
      "— EPA's current published figure (WARM v16). Estimating: US average is "
      "~8 lbs/person/week; a full 13-gal bag ≈ 8–10 lbs.\n\n"
      "Two things to be honest about. \"Mixed recyclables\" spans more than "
      "10× by material — an aluminum-heavy bin far exceeds this and a "
      "glass-heavy one falls well below — so what's actually in your bin "
      "matters more than the weight. And most of this credit is avoided "
      "*virgin production*, which accrues to whoever manufactures the next "
      "product, not to you; your Goods figure already reflects the average "
      "recycled content of what you bought. Counting it here double-counts. "
      "Treat it as a directional nudge, not a subtraction you could defend "
      "in an inventory.",
      value=2.83, display="2.83 t CO₂e / ton recycled",
      bias="over",
      sources=(("EPA GHG Equivalencies Calculator (WARM v16)", "https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator-calculations-and-references"),),
      code=("site/v2-template.html",)),

    A("offsets.compost", "offsets", "Compost credit",
      "0.65 t CO₂e per ton of food scraps diverted from landfill, credited "
      "against Food. This is EPA WARM v16's composting (−0.15) against its "
      "national-average landfill (+0.50). A full countertop pail ≈ 3–5 lbs; "
      "composting all scraps ≈ 4–7 lbs/person/week.\n\n"
      "The number hinges almost entirely on landfill gas capture, which our "
      "previous 0.6 quietly assumed the best case for: against a landfill "
      "with gas-to-energy the credit is 0.60, against one with no capture at "
      "all it is **1.60**. Food waste is the highest-methane material in "
      "WARM, so if your county landfill flares or captures you are at the low "
      "end. Unlike recycling, this credit is defensible to claim — the "
      "avoided methane is a real emission that genuinely does not happen, "
      "downstream of your own consumption.",
      value=0.65, display="0.65 t CO₂e / ton composted",
      bias="varies",
      sources=(("EPA WARM v16, Food Waste chapter, Exhibits 1-10 and 1-49",
                "https://www.epa.gov/system/files/documents/2023-12/warm_organic_materials_v16_dec.pdf"),),
      code=("site/v2-template.html",)),

    # ------------------------------------------------------------------
    # Impact numbers
    # ------------------------------------------------------------------
    A("impact.scc", "impact", "Social cost of carbon",
      "$270 per tonne, derived explicitly so it can be checked: EPA's 2023 "
      "SC-GHG report, Appendix A.5 Table A.5.1, gives **$215/t for a 2026 "
      "emission year** at a 2.0% near-term Ramsey discount rate in 2020 "
      "dollars (the headline tables are decadal — $193 for 2020, $230 for "
      "2030 — so the annual row is the one to use). Converting 2020 to 2026 "
      "dollars with the BEA GDP implicit price deflator (105.362 → 132.819) "
      "gives ×1.2606, so $215 × 1.2606 ≈ **$270**. EPA's own band for 2026 "
      "emissions runs $170 at 2.5% to $460 at 1.5%.\n\n"
      "This is the discounted present value of future damages — storms, "
      "floods, crop loss, heat mortality — mostly borne by people other than "
      "the emitter. Two caveats: EPA's estimate was sidelined for federal "
      "rulemaking in 2025 (EO 14154, OMB M-25-27) though never withdrawn as a "
      "technical document, and the economics literature has moved *up* — "
      "Bilal & Känzig (QJE 2026) estimate above $1,200/t using a "
      "global-temperature identification strategy.",
      value=270, display="$270 / t CO₂e",
      bias="under",
      sources=(("EPA SC-GHG report (2023), Appendix A.5 Table A.5.1",
                "https://www.epa.gov/system/files/documents/2023-12/epa_scghg_2023_report_final.pdf"),
               ("BEA GDP implicit price deflator (FRED GDPDEF)",
                "https://fred.stlouisfed.org/series/GDPDEF"),
               ("Bilal & Känzig 2026, Quarterly Journal of Economics 141(2)",
                "https://www.nber.org/papers/w32450")),
      code=("site/v2-template.html",)),

    A("impact.mortality", "impact", "Excess deaths (Bressler mortality cost)",
      "Bressler (2021, *Nature Communications*) estimates that each tonne of "
      "CO₂ causes about **2.26×10⁻⁴ excess deaths** through 2100 — one death "
      "per roughly 4,400 tonnes. The card shows what your current rate would "
      "cause if sustained for 50 years: tonnes × 226 micromorts × 50. A "
      "micromort is a one-in-a-million chance of death.\n\n"
      "**The scenario matters and we used to describe it wrongly.** The "
      "estimate comes from DICE-2016's no-policy baseline, which reaches "
      "**4.1 °C** above pre-industrial by 2100 — a pessimistic path, not a "
      "middling one. Bressler's own later work describes that baseline as "
      "\"quite pessimistic\". An earlier version of this note called it "
      "\"RCP6.1-like\", which understated the warming it assumes.\n\n"
      "**On uncertainty, in both directions.** The *scope* is conservative: "
      "it counts temperature-related mortality only, excluding famine, "
      "conflict, flooding and disease. But the *estimate* is not a floor — "
      "the paper's own 90% interval runs −1.71×10⁻⁴ to +6.78×10⁻⁴, which "
      "includes zero. An earlier version of this note called it a "
      "\"conservative floor\", which was wrong: conservative in what it "
      "counts, not in how certain it is.\n\n"
      "For a 50 t/yr household sustained 50 years, the defensible range "
      "across Bressler's own work spans roughly **0.3 to 1.5 deaths** around "
      "a central 0.6: the 2021 interval gives −0.4 to 1.5, and his 2025 "
      "update — new scenarios, an updated climate module and heat adaptation "
      "— gives 1.37×10⁻⁴ (about 0.3 deaths), roughly 39% below the published "
      "figure. That update remains a working paper; as of September 2026 no "
      "peer-reviewed version is indexed, so the 2021 coefficient is what we "
      "use. The card shows one decimal deliberately — a second would claim "
      "precision this range cannot support.\n\n"
      "One simplification worth recording: an earlier version applied an "
      "undocumented quadratic decay (0.04028·k²) meant to reflect later "
      "emission-years having less time to accumulate deaths before 2100. It "
      "could not be reconstructed from either paper, reduced the total by "
      "14%, and implied deaths accrue *faster* early — while Bressler's own "
      "figures show them back-loaded. It has been dropped in favour of the "
      "plain linear sum.",
      value={"umort_per_t": 226.0, "horizon_years": 50},
      display="≈ 1 death / 4,400 t (50-yr horizon)",
      bias="varies",
      sources=(("Bressler 2021, Nature Communications 12:4467",
                "https://www.nature.com/articles/s41467-021-24487-w"),
               ("Bressler 2025, Breaking Down the Mortality and Social Cost of Carbon (working paper)",
                "https://static1.squarespace.com/static/59bf26af29f187c6f3a9fbbf/t/678f111666f6b97afdd39e9c/1737429272631/JMP.pdf")),
      code=("site/v2-template.html",)),

    A("impact.us_benchmark", "impact", "US household benchmark",
      "The amber comparison tick is the average US household from "
      "CoolClimate's national defaults: 49.9 t CO₂e/yr — travel 15.7, home "
      "12.2, food 7.0, goods 7.9, services 7.0. A *household* average (not "
      "per person — at 2.5 people that is ~20 t each), computed under this "
      "app's own section boundaries so the comparison is apples-to-apples. "
      "It reproduces the 48 t published in Jones & Kammen 2011 almost "
      "exactly.\n\n"
      "The **food slice is the exception and is our own**: 7.06 t, which is "
      "what this app's diet model produces at US-average servings. When the "
      "food factors were re-derived from Poore & Nemecek, leaving "
      "CoolClimate's 7.0 t in the benchmark would have compared a household "
      "measured one way against an average measured another. It lands within "
      "1% of the figure it replaced, which is coincidence rather than "
      "confirmation — the composition underneath is very different.\n\n"
      "**Which is the problem: their base year is 2005.** US per-capita "
      "greenhouse emissions have fallen roughly 30% since then (about 25 to "
      "17.5 t CO₂e per person), and the decline is concentrated in a cleaner "
      "grid and more efficient vehicles — precisely the home and travel "
      "slices that dominate this benchmark. So the tick is probably 15–30% "
      "above a true 2026 US household and flatters every user a little. "
      "Correcting it properly means rescaling home and travel by their own "
      "sector declines rather than deflating the total, since food, goods "
      "and services have barely moved; that work is still outstanding.",
      bias="over",
      value={"travel": 15718, "home": 12219, "food": 7056,
             "goods": 7920, "services": 7032},  # kg/yr; totals 49.9 t
      display="49.9 t CO₂e / household / yr",
      sources=(JK2011, CC_API),
      code=("site/v2-template.html",)),

    # ------------------------------------------------------------------
    # Data handling
    # ------------------------------------------------------------------
    A("data.local_first", "data", "Your data stays in your browser",
      "Your transactions, amounts, dates and every tab input live in this "
      "browser's localStorage. They are never uploaded and the server never "
      "stores them.\n\n"
      "What does leave the browser: **merchant names and bank category "
      "hints**, sent to a shared classification cache and — for merchants "
      "nobody has classified yet — to Anthropic's API, which the server calls "
      "on your behalf. A merchant classified this way is written into the "
      "shared cache, so the next person who uploads the same merchant name "
      "reads your result rather than paying for a fresh lookup. Corrections "
      "you make are the exception: they stay in this browser and are never "
      "uploaded.\n\n"
      "The advanced hourly-electricity upload sends about **24 rows** of your "
      "CSV — the header block plus rows spread through the file, each "
      "truncated — so a model can work out the column layout. Those spread "
      "rows are real timestamped readings, which say something about when you "
      "are home. The file itself never leaves the browser.\n\n"
      "The server keeps requester IP addresses for roughly a day, in order to "
      "rate-limit the classification and parsing endpoints.",
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
HEALTH_INSURANCE_MIX = REGISTRY["gs.health_insurance"].value
ALPHA_BAND = (REGISTRY["elec.alpha_calibration"].value["band_lo"],
              REGISTRY["elec.alpha_calibration"].value["band_hi"])
UPLIFT_MAX = REGISTRY["elec.ship_gate"].value["uplift_max_pct"]
COVERAGE_MIN = REGISTRY["elec.ship_gate"].value["coverage_min"]
GRID_LOSS = REGISTRY["elec.delivery_loss"].value
