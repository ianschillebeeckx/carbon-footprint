"""
Tier 2 electricity emission factor resolution: ZIP (+ optional supplier) -> kg CO2e / kWh.

Tier 2 means MARKET-BASED: what the customer's retail supplier actually procured, as reported
under a state power-source disclosure program. This is a different quantity from Tier 1
(location-based eGRID subregion average) and the two are not interchangeable.

--------------------------------------------------------------------------------------
YOUR SIX DECISIONS, AND WHERE I DEVIATED
--------------------------------------------------------------------------------------

(1) "Average the factors for multi-subregion ZIPs."
    PARTIALLY IMPLEMENTED, and demoted. Multi-subregion ZIPs exist *because* different
    utilities serve different parts of the ZIP. So when you know the supplier, averaging
    throws away the exact information that resolves the ambiguity - EPA's own Power Profiler
    disambiguates by utility rather than averaging. I average only on the supplier-unknown
    fallback path. See _resolve_subregion().
    Second issue: an unweighted mean assumes the ZIP's load splits 50/50 across subregions,
    which is arbitrary. The defensible weight is customer count by utility from EIA-861.
    I left the unweighted mean as the default but the weighting hook is there.

(2) "Use the source that is more up to date, lean EPA."
    NOT IMPLEMENTED AS STATED - it doesn't parse for Tier 2. EPA does not publish
    supplier-level factors at all. For a CA supplier the CEC is the only source of the
    number you asked for; eGRID is a different quantity, not a fresher version of the same
    one. Recency cannot arbitrate between them.
    Worse, "prefer the most recent" is actively harmful here: CEC publishes with a ~1-year
    lag and Green-e residual mix lags eGRID by ~1 year, so a recency rule would silently
    prefer eGRID location-based and collapse your Tier 2 into Tier 1 without telling you.
    What I implemented instead: source is chosen by QUESTION, then vintages are matched to a
    common reporting year (VINTAGE_POLICY). "Prefer EPA" is applied where it's meaningful -
    grid loss factors, subregion fallback, and the CO2->CO2e rescaling of residual mix.

(3) "Use CO2e if published."
    IMPLEMENTED, with a scope correction you'll want to know about. CEC publishes CO2e.
    Green-e's residual mix rate has historically been CO2 only. Mixing them gives you a
    silently inconsistent series, so residual-mix values flagged includes_ch4_n2o=FALSE get
    scaled by the subregion's eGRID co2e/co2 ratio. Also: check that all three sources use
    the same GWP set before you trust a cross-source comparison - AR4 vs AR5 moves CH4 from
    25 to 28. Relevant to you specifically given the Joos/Bressler work downstream.

(4) "Use annual total output."
    IMPLEMENTED but it's close to vacuous at Tier 2. The total-output vs non-baseload split
    is an eGRID artifact; CEC has no such distinction, its numbers are annual by construction.
    The decision only binds on the eGRID fallback path.

(5) "Use grid losses."
    IMPLEMENTED BUT DEFAULTED OFF FOR CEC-DERIVED FACTORS. This is my strongest objection.
    CEC computes intensity as (emissions from procured power) / (retail sales). Procurement
    exceeds retail sales by roughly the T&D loss amount, so the denominator choice already
    embeds losses. Grossing up by another ~5% double-counts. eGRID rates ARE at the busbar
    and genuinely need the gross-up. So losses are applied per-source, not globally.
    ACTION REQUIRED: verify against the CEC PSD methodology for your target vintage and set
    basis_denominator in the CSV accordingly. I am not certain enough about this to hard-code it.

(6) "Include upstream fuel-cycle emissions."
    IMPLEMENTED, but mix-weighted rather than as a flat multiplier. A flat percentage adder
    is wrong in the direction that matters most for your app: it would scale a 100%-renewable
    product's ~0 gCO2e/kWh to ~0, when the correct answer is 20-40 g/kWh of embodied
    manufacturing and construction emissions. Because CEC publishes the resource mix
    alongside the intensity, you can do this properly: sum(share_f * upstream_f).

ONE DECISION YOU DIDN'T MAKE, THAT YOU HAVE TO:
    If any customer is allowed to claim a green product, every other customer in that
    subregion must be assigned the RESIDUAL MIX, not the location-based average, or the
    system double-counts renewables. This is implemented and is the reason greene_residual_mix
    is a required input rather than optional.

CARBON-FREE PERCENTAGE (added alongside kg/kWh):
    Comes free - both the CEC Power Content Label and the eGRID subregion resource mix are
    already loaded for the upstream adder. Three things to know:

    a) "Carbon-free" is three different numbers. CA's RPS-eligible renewable figure - the one
       printed on the customer's bill - EXCLUDES large hydro and nuclear. Carbon-free includes
       both. On a nuclear-heavy portfolio these differ by 2-3x on identical generation. The
       resolver returns all three definitions (strict / conventional / rps_ca) so you never
       have to guess which one a user is comparing against.

    b) Unspecified power breaks the arithmetic. CEC labels routinely carry 15-30% unspecified
       market purchases - not carbon-free, but not characterized either. Returning a point
       estimate here is false precision, so the resolver returns a BAND:
         low  = unspecified counted as 0% carbon-free (conservative)
         high = unspecified allocated at the subregion residual-mix carbon-free share
       and a point estimate per unspecified_treatment. If the band is wide, say so in the UI.

    c) The carbon-free % and the kg/kWh must be mutually consistent, and checking that is the
       real payoff. _implied_intensity() recomputes intensity from the disclosed mix using
       per-fuel combustion factors and compares it to the disclosed intensity. A large gap
       means a bad disclosure row, a bad ZIP->supplier join, or a mix/intensity vintage
       mismatch. This catches join errors nothing else in the pipeline will.

    NOTE ON BASIS: for a supplier-level result, BOTH numbers are market-based/contractual.
    A CleanPowerSF SuperGreen customer is 100% carbon-free on paper while physically drawing
    the CAISO mix in real time. physical_pct_carbon_free is returned alongside so you can show
    both; they are answers to different questions and users conflate them constantly.

SCOPE: supplier-level disclosure data only exists in disclosure states. Outside those, this
function degrades to Tier 1 and says so in the result. Check result.tier before aggregating.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

LB_TO_KG = 0.45359237          # exact international definition
MWH_TO_KWH = 1000.0

# Vintage policy. Sources publish on different lags; pinning a reporting year and taking the
# newest release at or before it keeps a multi-year series comparable. Re-baseline
# deliberately, not implicitly.
VINTAGE_POLICY = "match_reporting_year"

# CARB default factor for unspecified market purchases: 0.428 metric tons CO2e/MWh.
# Used only if a disclosure row reports unspecified power but no overall intensity.
CARB_UNSPECIFIED_KG_PER_KWH = 0.428

CARBON_FREE_DEFINITIONS = ("strict", "conventional", "rps_ca")
DEFAULT_CF_DEFINITION = "conventional"

# How much the implied intensity may diverge from the disclosed intensity before warning.
IMPLIED_INTENSITY_TOLERANCE = 0.30

# Maps CEC Power Content Label mix categories -> upstream factor keys.
MIX_COLUMN_TO_FUEL = {
    "pct_biomass": "biomass",
    "pct_geothermal": "geothermal",
    "pct_small_hydro": "hydro_small",
    "pct_solar": "solar",
    "pct_wind": "wind",
    "pct_coal": "coal",
    "pct_large_hydro": "hydro_large",
    "pct_natural_gas": "natural_gas",
    "pct_nuclear": "nuclear",
    "pct_other": "other",
    "pct_unspecified": "unspecified",
}
# NOTE: pct_eligible_renewable is a ROLLUP of biomass+geothermal+small hydro+solar+wind in the
# CEC label. It is deliberately excluded from the sum to avoid double-counting.

EGRID_MIX_TO_FUEL = {
    "pct_coal": "coal",
    "pct_natural_gas": "natural_gas",
    "pct_oil": "oil",
    "pct_nuclear": "nuclear",
    "pct_hydro": "hydro_large",
    "pct_wind": "wind",
    "pct_solar": "solar",
    "pct_geothermal": "geothermal",
    "pct_biomass": "biomass",
    "pct_other": "other",
}


@dataclass
class FactorResult:
    kg_co2e_per_kwh: float
    tier: str                      # "2-supplier", "2-residual", "1-location"
    method: str
    combustion_kg_per_kwh: float
    upstream_kg_per_kwh: float
    loss_gross_up_applied: bool
    loss_factor: float
    subregion: Optional[str]
    supplier_id: Optional[str]
    product: Optional[str]
    vintage: Optional[int]
    confidence: str                # "high", "medium", "low"

    # --- carbon-free reporting ---
    pct_carbon_free: Optional[float] = None        # point estimate, chosen definition
    pct_carbon_free_low: Optional[float] = None    # unspecified counted as 0% carbon-free
    pct_carbon_free_high: Optional[float] = None   # unspecified at residual-mix CF share
    carbon_free_definition: str = DEFAULT_CF_DEFINITION
    pct_by_definition: dict = field(default_factory=dict)   # all three, for UI toggling
    pct_unspecified: Optional[float] = None
    physical_pct_carbon_free: Optional[float] = None  # location-based, always eGRID subregion
    carbon_free_basis: Optional[str] = None           # "market" or "location"
    implied_kg_co2e_per_kwh: Optional[float] = None   # recomputed from mix, for the cross-check

    warnings: list = field(default_factory=list)
    alternatives: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

    def __repr__(self):
        cf = ("n/a" if self.pct_carbon_free is None
              else f"{self.pct_carbon_free:.1f}% CF "
                   f"[{self.pct_carbon_free_low:.0f}-{self.pct_carbon_free_high:.0f}]")
        return (f"<{self.kg_co2e_per_kwh:.4f} kg/kWh | {cf} | {self.tier} "
                f"| {self.confidence}>")


def _read_csv(name):
    """Reads a CSV, skipping '#' comment lines that precede the header."""
    path = os.path.join(DATA_DIR, name)
    with open(path, newline="", encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def _f(row, key, default=0.0):
    try:
        v = row.get(key, "")
        return float(v) if v not in ("", None) else default
    except (TypeError, ValueError):
        return default


class Tier2FactorResolver:
    def __init__(self, data_dir=DATA_DIR, reporting_year=2023,
                 gas_leakage_sensitivity=1.0, apply_upstream=True,
                 carbon_free_definition=DEFAULT_CF_DEFINITION,
                 unspecified_treatment="zero"):
        global DATA_DIR
        DATA_DIR = data_dir
        self.year = reporting_year
        self.apply_upstream = apply_upstream
        # Scales the natural_gas upstream factor. 1.0 == ~2.3% supply-chain CH4 leakage at
        # GWP100/AR5. Sweep 0.6-1.8 to see how much of your total footprint rests on a
        # number nobody can measure well.
        self.gas_leakage_sensitivity = gas_leakage_sensitivity
        if carbon_free_definition not in CARBON_FREE_DEFINITIONS:
            raise ValueError(f"carbon_free_definition must be one of {CARBON_FREE_DEFINITIONS}")
        self.cf_definition = carbon_free_definition
        # "zero" (unspecified is 0% carbon-free), "residual" (allocate at residual-mix share),
        # or "exclude" (renormalize - see the warning it emits before using it).
        self.unspecified_treatment = unspecified_treatment

        self.zip_subregion = {r["zip"]: r for r in _read_csv("zip_subregion.csv")}
        self.egrid = {r["subregion"]: r for r in _read_csv("egrid_subregion_factors.csv")
                      if int(r["egrid_year"]) <= reporting_year}
        self.residual = {r["subregion"]: r for r in _read_csv("greene_residual_mix.csv")
                         if int(r["residual_year"]) <= reporting_year}
        self.upstream = {r["fuel"]: r for r in _read_csv("fuel_factors.csv")}

        self.psd = {}
        for r in _read_csv("cec_psd_supplier_factors.csv"):
            if int(r["psd_year"]) <= reporting_year:
                self.psd[(r["supplier_id"], r["product"])] = r
                self.psd.setdefault((r["supplier_id"], None), r)  # first product as default

        self.zip_suppliers = {}
        for r in _read_csv("zip_supplier_candidates.csv"):
            self.zip_suppliers.setdefault(r["zip"], []).append(r)

    # ---------- Decision (1): subregion resolution ----------
    def _resolve_subregion(self, zip_code, supplier_id=None, warnings=None):
        """
        Multi-subregion ZIPs are averaged ONLY when supplier is unknown. When supplier IS
        known it should disambiguate - that is what the ambiguity is caused by in the first
        place. The disambiguation table is a TODO: it needs a supplier -> balancing authority
        -> subregion crosswalk from the eGRID Technical Guide, which I have not built here.
        """
        row = self.zip_subregion.get(str(zip_code))
        if not row:
            if warnings is not None:
                warnings.append(f"ZIP {zip_code} not in crosswalk; falling back to US average.")
            return ["US"]
        subs = [row.get(f"subregion_{i}") for i in (1, 2, 3)]
        subs = [s for s in subs if s]
        if len(subs) > 1 and warnings is not None:
            warnings.append(
                f"ZIP {zip_code} spans {len(subs)} subregions {subs}; unweighted mean used. "
                "Weight by EIA-861 customer counts for a defensible split."
            )
        return subs or ["US"]

    def _egrid_avg(self, subs, key):
        vals = [_f(self.egrid[s], key) for s in subs if s in self.egrid]
        return sum(vals) / len(vals) if vals else None

    # ---------- Decision (6): mix-weighted upstream ----------
    def _upstream_kg_per_kwh(self, mix_row, column_map, warnings):
        """
        Mix-weighted, not a flat multiplier. sum over fuels of (share * upstream factor).
        Fossil fuels contribute upstream-only (their combustion is already in the reported
        intensity). Non-combusting sources contribute their full lifecycle median, since
        there is no stack figure to avoid double-counting against.
        """
        if not self.apply_upstream:
            return 0.0
        total_g = 0.0
        share_sum = 0.0
        for col, fuel in column_map.items():
            share = _f(mix_row, col) / 100.0
            if share <= 0:
                continue
            uf = self.upstream.get(fuel)
            if uf is None:
                continue
            g = _f(uf, "g_co2e_per_kwh")
            if fuel == "natural_gas":
                g *= self.gas_leakage_sensitivity
            total_g += share * g
            share_sum += share
        if share_sum < 0.90:
            warnings.append(
                f"Resource mix sums to {share_sum:.0%}; upstream adder is understated. "
                "Check the disclosure row for missing categories."
            )
        elif share_sum > 1.10:
            warnings.append(
                f"Resource mix sums to {share_sum:.0%} - likely double-counting a rollup "
                "column such as pct_eligible_renewable."
            )
        return total_g / 1000.0  # g -> kg

    # ---------- Decision (3): CO2 -> CO2e rescale ----------
    def _residual_co2e(self, sub, warnings):
        r = self.residual.get(sub)
        if not r:
            return None
        val = _f(r, "residual_lb_per_mwh")
        if r.get("includes_ch4_n2o", "").strip().upper() != "TRUE":
            e = self.egrid.get(sub)
            if e and _f(e, "co2_lb_per_mwh") > 0:
                ratio = _f(e, "co2e_lb_per_mwh") / _f(e, "co2_lb_per_mwh")
                val *= ratio
                warnings.append(
                    f"Residual mix for {sub} is CO2-only; rescaled by eGRID CO2e/CO2 "
                    f"ratio {ratio:.4f} for consistency with CEC CO2e figures."
                )
            else:
                warnings.append(
                    f"Residual mix for {sub} is CO2-only and no eGRID rescale ratio is "
                    "available. Value is understated by roughly 1%."
                )
        return val


    # ---------- carbon-free share ----------
    def _carbon_free(self, mix_row, column_map, definition, subregion, warnings):
        """
        Returns (point, low, high, pct_unspecified, all_definitions).

        The band exists because unspecified power is genuinely unknown, not because I am
        hedging. low treats it as fully fossil; high allocates it at the subregion's
        residual-mix carbon-free share. A point estimate alone hides a spread that can
        exceed 25 percentage points on a CCA default product.
        """
        by_def = {}
        unspecified = _f(mix_row, "pct_unspecified")
        for d in CARBON_FREE_DEFINITIONS:
            cf = 0.0
            for col, fuel in column_map.items():
                if fuel == "unspecified":
                    continue
                row = self.upstream.get(fuel)
                if row and row.get(f"carbon_free_{d}", "").strip().upper() == "TRUE":
                    cf += _f(mix_row, col)
            by_def[d] = cf

        low = by_def[definition]
        resid = self.residual.get(subregion)
        resid_cf = _f(resid, "pct_carbon_free_residual") if resid else 0.0
        high = low + unspecified * (resid_cf / 100.0)

        if self.unspecified_treatment == "zero":
            point = low
        elif self.unspecified_treatment == "residual":
            point = high
        elif self.unspecified_treatment == "exclude":
            # Renormalize: drop unspecified from the denominator entirely. Defensible only if
            # you also drop it from the intensity, which the CEC number does not let you do.
            denom = 100.0 - unspecified
            point = (low / denom * 100.0) if denom > 0 else low
            warnings.append(
                "unspecified_treatment='exclude' renormalizes the carbon-free denominator but "
                "the intensity still includes unspecified power. The two numbers now have "
                "different denominators - do not present them as a matched pair."
            )
        else:
            point = low

        if unspecified >= 15.0:
            warnings.append(
                f"{unspecified:.0f}% of this portfolio is unspecified market power. "
                f"Carbon-free is only bounded to [{low:.0f}%, {high:.0f}%]; the point "
                "estimate carries less information than the band."
            )
        return point, low, high, unspecified, by_def

    def _implied_intensity(self, mix_row, column_map):
        """
        Recompute combustion intensity from the disclosed mix. Compared against the disclosed
        intensity as a join/vintage sanity check - NOT used as the returned factor, because
        fleet-average combustion factors are far cruder than a supplier's actual reported
        emissions.
        """
        g = 0.0
        for col, fuel in column_map.items():
            row = self.upstream.get(fuel)
            if row:
                g += (_f(mix_row, col) / 100.0) * _f(row, "combustion_g_co2e_per_kwh")
        return g / 1000.0

    def _physical_carbon_free(self, subs, definition):
        """Location-based carbon-free share from the eGRID subregion mix, market claims ignored."""
        vals = []
        for sub in subs:
            e = self.egrid.get(sub)
            if not e:
                continue
            cf = 0.0
            for col, fuel in EGRID_MIX_TO_FUEL.items():
                row = self.upstream.get(fuel)
                if row and row.get(f"carbon_free_{definition}", "").strip().upper() == "TRUE":
                    cf += _f(e, col)
            vals.append(cf)
        return sum(vals) / len(vals) if vals else None

    # ---------- main entry point ----------
    def resolve(self, zip_code, supplier=None, product=None,
                claims_green_product=None, apply_losses=True):
        """
        Returns a FactorResult in kg CO2e / kWh delivered.

        zip_code : str or int
        supplier : optional supplier_id (e.g. "CLEANPOWERSF"). If omitted, the modal
                   supplier for the ZIP is guessed and confidence is downgraded to "low" -
                   in CCA territory the same ZIP can differ by 5x between suppliers, so a
                   point estimate is not honest without this input. Prompt the user for it.
        product  : optional product tier (e.g. "SuperGreen"). Matters enormously - a CCA's
                   opt-up product and default product are different factors from one supplier.
        claims_green_product : if False and the supplier is unknown, the RESIDUAL mix is used
                   rather than the location-based average, so that other customers' green
                   claims are not double-counted.
        apply_losses : master switch. Even when True, the gross-up is skipped for factors
                   whose denominator is already retail sales (see decision 5).
        """
        zip_code = str(zip_code).strip()[:5]
        warnings, alternatives = [], []

        # --- resolve supplier ---
        supplier_id, prod, conf = supplier, product, "high"
        if supplier_id is None:
            cands = sorted(self.zip_suppliers.get(zip_code, []),
                           key=lambda r: -_f(r, "enrollment_share"))
            if cands:
                supplier_id = cands[0]["supplier_id"]
                prod = prod or cands[0]["default_product"]
                conf = "low"
                warnings.append(
                    f"Supplier not supplied; assumed modal supplier {supplier_id} "
                    f"({_f(cands[0], 'enrollment_share'):.0%} of accounts). In CCA territory "
                    "this is a coin flip on a 5x spread - ask the user."
                )
                alternatives = [
                    {"supplier_id": c["supplier_id"], "product": c["default_product"],
                     "enrollment_share": _f(c, "enrollment_share")} for c in cands[1:]
                ]

        # --- Tier 2 path: supplier-level disclosure ---
        row = self.psd.get((supplier_id, prod)) or self.psd.get((supplier_id, None))
        if row is not None:
            lb = _f(row, "ghg_intensity_lb_co2e_per_mwh")
            combustion = lb * LB_TO_KG / MWH_TO_KWH
            upstream = self._upstream_kg_per_kwh(row, MIX_COLUMN_TO_FUEL, warnings)

            # Decision (5), deviated: skip gross-up when the denominator is already retail sales.
            already_delivered = row.get("basis_denominator", "").strip() == "retail_sales"
            subs = self._resolve_subregion(zip_code, supplier_id, warnings)
            loss_pct = self._egrid_avg(subs, "grid_gross_loss_pct") or 0.0
            if apply_losses and not already_delivered:
                loss_factor = 1.0 / (1.0 - loss_pct / 100.0)
                applied = True
            else:
                loss_factor, applied = 1.0, False
                if apply_losses:
                    warnings.append(
                        "Loss gross-up SKIPPED: CEC intensity is reported per MWh of retail "
                        "sales, so T&D losses are already embedded in the denominator. "
                        "Applying eGRID's gross-up here would double-count ~"
                        f"{loss_pct:.1f}%. Verify against the CEC PSD methodology."
                    )

            cf_pt, cf_lo, cf_hi, unspec, by_def = self._carbon_free(
                row, MIX_COLUMN_TO_FUEL, self.cf_definition, subs[0], warnings)
            implied = self._implied_intensity(row, MIX_COLUMN_TO_FUEL)
            if combustion > 0 and abs(implied - combustion) / max(combustion, 1e-9) > IMPLIED_INTENSITY_TOLERANCE:
                warnings.append(
                    f"Consistency check: intensity implied by the disclosed mix is "
                    f"{implied:.4f} kg/kWh but the supplier reports {combustion:.4f}. "
                    "Likely a mix/intensity vintage mismatch, a bad ZIP->supplier join, or "
                    "a supplier whose fleet differs sharply from national averages."
                )

            total = (combustion + upstream) * loss_factor
            if _f(row, "ghg_intensity_lb_co2e_per_mwh") == 0.0:
                warnings.append(
                    "Supplier reports 0 lb CO2e/MWh (100% renewable product). The nonzero "
                    "result is embodied manufacturing/construction emissions, which is "
                    "correct and is the point of the mix-weighted upstream adder."
                )
            return FactorResult(
                kg_co2e_per_kwh=round(total, 6), tier="2-supplier",
                method="CEC Power Source Disclosure, market-based, mix-weighted upstream",
                combustion_kg_per_kwh=round(combustion, 6),
                upstream_kg_per_kwh=round(upstream, 6),
                loss_gross_up_applied=applied, loss_factor=round(loss_factor, 5),
                subregion="+".join(subs), supplier_id=supplier_id, product=prod,
                vintage=int(row["psd_year"]), confidence=conf,
                pct_carbon_free=round(cf_pt, 2),
                pct_carbon_free_low=round(cf_lo, 2),
                pct_carbon_free_high=round(cf_hi, 2),
                carbon_free_definition=self.cf_definition,
                pct_by_definition={k: round(v, 2) for k, v in by_def.items()},
                pct_unspecified=round(unspec, 2),
                physical_pct_carbon_free=self._physical_carbon_free(subs, self.cf_definition),
                carbon_free_basis="market",
                implied_kg_co2e_per_kwh=round(implied, 6),
                warnings=warnings, alternatives=alternatives,
                provenance={"intensity": "CEC PSD", "mix": "CEC Power Content Label",
                            "losses": "EPA eGRID", "upstream": "IPCC AR5 A.III.2",
                            "data_quality": row.get("data_quality")},
            )

        # --- Fallback: residual mix or location-based ---
        subs = self._resolve_subregion(zip_code, None, warnings)
        loss_pct = self._egrid_avg(subs, "grid_gross_loss_pct") or 0.0
        loss_factor = 1.0 / (1.0 - loss_pct / 100.0) if apply_losses else 1.0

        use_residual = claims_green_product is False
        if use_residual:
            vals = [v for v in (self._residual_co2e(s, warnings) for s in subs) if v]
            lb = sum(vals) / len(vals) if vals else None
            tier, method = "2-residual", "Green-e residual mix (non-claimant), CO2e-rescaled"
        else:
            lb = None

        if lb is None:
            lb = self._egrid_avg(subs, "co2e_lb_per_mwh")
            tier, method = "1-location", "eGRID annual total output rate, location-based"
            warnings.append(
                "No supplier-level disclosure available - degraded to Tier 1 location-based. "
                "This is a different accounting basis; do not aggregate with Tier 2 results "
                "without flagging."
            )
            conf = "medium" if conf == "high" else conf

        combustion = (lb or 0.0) * LB_TO_KG / MWH_TO_KWH
        mix_src = self.egrid.get(subs[0], {})
        upstream = self._upstream_kg_per_kwh(mix_src, EGRID_MIX_TO_FUEL, warnings)
        total = (combustion + upstream) * loss_factor

        physical_cf = self._physical_carbon_free(subs, self.cf_definition)
        if use_residual:
            # Non-claimants get the RESIDUAL carbon-free share, not the raw subregion share.
            # Crediting them with the full subregion mix would double-count renewables that
            # green-product customers have already claimed - the same reason the residual
            # intensity is used for the kg/kWh on this path.
            resid_vals = [_f(self.residual[s], "pct_carbon_free_residual")
                          for s in subs if s in self.residual]
            cf_pt = sum(resid_vals) / len(resid_vals) if resid_vals else physical_cf
            warnings.append(
                f"Non-claimant: carbon-free reported at the residual-mix share "
                f"({cf_pt:.0f}%), not the raw subregion share ({physical_cf:.0f}%)."
            )
        else:
            cf_pt = physical_cf
        by_def = {d: self._physical_carbon_free(subs, d) for d in CARBON_FREE_DEFINITIONS}

        return FactorResult(
            kg_co2e_per_kwh=round(total, 6), tier=tier, method=method,
            combustion_kg_per_kwh=round(combustion, 6),
            upstream_kg_per_kwh=round(upstream, 6),
            loss_gross_up_applied=bool(apply_losses),
            loss_factor=round(loss_factor, 5),
            subregion="+".join(subs), supplier_id=supplier_id, product=prod,
            vintage=self.year, confidence=conf,
            pct_carbon_free=round(cf_pt, 2) if cf_pt is not None else None,
            pct_carbon_free_low=round(cf_pt, 2) if cf_pt is not None else None,
            pct_carbon_free_high=round(cf_pt, 2) if cf_pt is not None else None,
            carbon_free_definition=self.cf_definition,
            pct_by_definition={k: (round(v, 2) if v is not None else None)
                               for k, v in by_def.items()},
            pct_unspecified=0.0,
            physical_pct_carbon_free=physical_cf,
            carbon_free_basis="location",
            implied_kg_co2e_per_kwh=round(self._implied_intensity(mix_src, EGRID_MIX_TO_FUEL), 6),
            warnings=warnings, alternatives=alternatives,
            provenance={"intensity": "Green-e residual" if use_residual else "EPA eGRID",
                        "losses": "EPA eGRID", "upstream": "IPCC AR5 A.III.2"},
        )


def electricity_factor(zip_code, supplier=None, product=None, **kw):
    """Convenience wrapper. Instantiate Tier2FactorResolver directly to avoid re-reading CSVs."""
    return Tier2FactorResolver(**{k: v for k, v in kw.items()
                                  if k in ("data_dir", "reporting_year",
                                           "gas_leakage_sensitivity", "apply_upstream",
                                           "carbon_free_definition", "unspecified_treatment")}
                               ).resolve(zip_code, supplier, product,
                                         **{k: v for k, v in kw.items()
                                            if k in ("claims_green_product", "apply_losses")})


if __name__ == "__main__":
    r = Tier2FactorResolver(carbon_free_definition="conventional")
    for args in [
        {"zip_code": "94110", "supplier": "CLEANPOWERSF", "product": "Green"},
        {"zip_code": "94110", "supplier": "CLEANPOWERSF", "product": "SuperGreen"},
        {"zip_code": "94110", "supplier": "PGE_BUNDLED"},
        {"zip_code": "89501", "claims_green_product": False},
        {"zip_code": "73301"},
    ]:
        res = r.resolve(**args)
        print(f"\n{args}")
        print(f"  {res}")
        print(f"  by definition: {res.pct_by_definition}   unspecified={res.pct_unspecified}%")
        print(f"  basis={res.carbon_free_basis}  physical CF={res.physical_pct_carbon_free}%")
        print(f"  combustion={res.combustion_kg_per_kwh}  upstream={res.upstream_kg_per_kwh}"
              f"  implied={res.implied_kg_co2e_per_kwh}")
        for w in res.warnings:
            print(f"  ! {w}")
