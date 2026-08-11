# carbon_footprint

Personal carbon-footprint estimation from bank/credit-card transactions
(Monarch Money CSV export or any CSV with Merchant + Amount columns).

**v2 (current)** — bottom-up per-transaction emissions: each merchant is
resolved to a NAICS industry (tier 0 rules → local vector retrieval → LLM),
multiplied by EPA Supply Chain GHG Emission Factors (v1.3.0, kg CO₂e per
2022 USD, spend CPI-deflated), and rolled up to CoolClimate-style Goods &
Services categories. Retail store codes are re-priced as editable commodity
baskets (manufacturing + additive retail/wholesale/transport margins) so
purchases carry the goods' footprint, not the store's markup. Serve with
`cf serve` and open `http://127.0.0.1:8742/v2` — upload a CSV, review/correct
merchant rules (per category or merchant-wide), see monthly emissions.
Design rationale: `naics_mapping_design_notes.md`.

**v1** — top-down: aggregates 12 months of spending into the
[CoolClimate calculator](https://coolclimate.org/calculator)'s Goods &
Services dollar inputs and shows them beside the embedded calculator.

## Setup

```sh
uv venv --python 3.12
uv pip install -e .
```

## Usage

Easiest — everything from the browser:

```sh
.venv/bin/cf serve          # opens http://127.0.0.1:8742
```

The page shows the CoolClimate calculator next to your values, with a
"Fetch latest from Monarch" button. First time, it asks for your Monarch
email/password/MFA right on the page — credentials go only to the local
Python process (bound to 127.0.0.1) and are never stored; the login session
is saved encrypted under `.mm/` so later fetches are one click.

Or via the CLI:

```sh
.venv/bin/cf all            # fetch -> aggregate -> build + open static site
```

Step by step:

```sh
.venv/bin/cf fetch          # login to Monarch (interactive MFA on first run), cache transactions
.venv/bin/cf aggregate      # apply config/mapping.yaml, print summary, write data/values.json + .csv
.venv/bin/cf site           # build site/index.html (calculator iframe + your values) and open it
```

The first `fetch` prompts for Monarch email/password/MFA; the session is
saved under `.mm/` and reused. Transactions are cached in
`data/transactions.json`, so you can iterate on the mapping without
re-hitting the API.

## Mapping

`config/mapping.yaml` maps each Monarch category to a CoolClimate field,
`exclude` (food/travel/housing — entered elsewhere in the calculator), or a
weighted split across fields. `merchant_overrides` (substring match) win
over category mappings. Categories missing from the file show up in the
"unmapped" report — add them and rerun `cf aggregate && cf site`.

`data/values.json` keeps a per-month series per field for phase 2
(USEEIO-based monthly emissions).
