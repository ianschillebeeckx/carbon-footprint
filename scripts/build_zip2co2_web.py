"""Precompute web/data/zip2co2.json — the browser-side electricity factor dataset.

All six accounting decisions live in zip2co2/tier2_factors.py (Python); this
script runs that resolver at build time so the client only does lookups:

  entries  : per subregion (and per multi-subregion combo that occurs in the
             ZIP crosswalk): location-based (tier 1) and residual-mix (tier
             2-residual, non-claimant) results.
  sups     : one row per CEC supplier x product — market-based factor with the
             mix-weighted upstream adder, carbon-free band, disclosed mix.
  zips     : ZIP -> index into entries (all US).
  zip_sup  : CA ZIP -> [[sup index, is_default], ...].
  green100 : synthetic 100%-renewable product for green customers outside the
             disclosure data: upstream-only 50/50 wind/solar embodied
             emissions, from fuel_factors.csv.

Seed guard: any data_quality=SEED_APPROXIMATE row aborts the build (the seeds
shipped with the module must never reach production output).

Run:  .venv/bin/python scripts/build_zip2co2_web.py
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "zip2co2"))

from tier2_factors import Tier2FactorResolver   # noqa: E402

DATA = ROOT / "zip2co2" / "data"
OUT = ROOT / "web" / "data" / "zip2co2.json"
YEAR = 2023


def assert_no_seeds():
    for name in ("egrid_subregion_factors.csv", "greene_residual_mix.csv",
                 "cec_psd_supplier_factors.csv", "zip_supplier_candidates.csv"):
        for ln in (DATA / name).read_text().splitlines():
            if "SEED_APPROXIMATE" in ln:
                raise SystemExit(f"SEED data in {name} — refusing to build: {ln[:80]}")


def res_to_obj(r):
    return {
        "kg": round(r.kg_co2e_per_kwh, 4),
        "comb": round(r.combustion_kg_per_kwh, 4),
        "up": round(r.upstream_kg_per_kwh, 4),
        "cf": round(r.pct_carbon_free or 0, 1),
        "cf_lo": round(r.pct_carbon_free_low or 0, 1),
        "cf_hi": round(r.pct_carbon_free_high or 0, 1),
        "phys_cf": round(r.physical_pct_carbon_free, 1) if r.physical_pct_carbon_free is not None else None,
        "tier": r.tier,
    }


def main():
    assert_no_seeds()
    r = Tier2FactorResolver(data_dir=str(DATA), reporting_year=YEAR)

    # --- ZIP -> subregion-combo entries ---
    combos = {}          # "CAMX" or "CAMX+NWPP" -> index
    zips = {}
    for z, row in r.zip_subregion.items():
        subs = [row.get(f"subregion_{i}") for i in (1, 2, 3)]
        key = "+".join(s for s in subs if s)
        if not key:
            continue
        zips[z] = combos.setdefault(key, len(combos))

    entries = []
    zip_of = {}          # combo key -> a zip resolving to it, for resolve()
    for key, i in combos.items():
        zip_of[key] = next(z for z, j in zips.items() if j == i)
    if "US" not in combos:      # fallback entry for empty/unknown ZIPs
        combos["US"] = len(combos)
        zip_of["US"] = "00000"  # not in the crosswalk -> resolver's US-average path
    for key in combos:
        z = zip_of[key]
        # supplier="__NONE__" defeats resolve()'s modal-supplier guess (which
        # would turn a CA ZIP's "location" entry into that CCA's market-based
        # row): the unknown id misses the PSD table and falls through to the
        # location/residual paths these entries are supposed to hold.
        loc = r.resolve(z, supplier="__NONE__", apply_losses=True)              # tier 1
        resid = r.resolve(z, supplier="__NONE__", claims_green_product=False)   # tier 2-residual
        entries.append({"sub": key, "loc": res_to_obj(loc), "resid": res_to_obj(resid)})

    # --- supplier x product rows (market-based) ---
    cand = {}
    for row in csv.DictReader(ln for ln in (DATA / "zip_supplier_candidates.csv")
                              .read_text().splitlines() if not ln.startswith("#")):
        cand.setdefault(row["zip"], []).append(row)
    rep_zip = {}         # supplier -> a zip in its territory (for CF-high residual)
    for z, rows in cand.items():
        for row in rows:
            rep_zip.setdefault(row["supplier_id"], z)

    sups, sup_idx = [], {}
    names = {}
    for (sid, prod), row in r.psd.items():
        if prod is None:
            continue
        names[sid] = row["supplier_name"]
        res = r.resolve(rep_zip.get(sid, "94110"), supplier=sid, product=prod)
        mix = {k.replace("pct_", ""): float(row[k] or 0)
               for k in row if k.startswith("pct_") and k != "pct_eligible_renewable"
               and float(row[k] or 0) > 0}
        o = res_to_obj(res)
        o.update({"id": sid, "name": row["supplier_name"], "prod": prod,
                  "unspec": float(row.get("pct_unspecified") or 0),
                  "mix": {k: round(v, 1) for k, v in
                          sorted(mix.items(), key=lambda kv: -kv[1])}})
        sup_idx[(sid, prod)] = len(sups)
        sups.append(o)

    zip_sup = {}
    for z, rows in cand.items():
        lst = []
        for row in rows:
            key = (row["supplier_id"], row["default_product"])
            if key not in sup_idx:      # default product name drift — first product
                key = next((k for k in sup_idx if k[0] == row["supplier_id"]), None)
            if key is None:
                continue
            lst.append([sup_idx[key], 1 if row["is_default"] == "TRUE" else 0])
        if lst:
            zip_sup[z] = lst

    # supplier -> all product row indexes (for the product dropdown)
    sup_products = {}
    for (sid, prod), i in sup_idx.items():
        sup_products.setdefault(sid, []).append(i)

    up = {f["fuel"]: float(f["g_co2e_per_kwh"]) for f in r.upstream.values()} if False else None
    wind = float(r.upstream["wind"]["g_co2e_per_kwh"])
    solar = float(r.upstream["solar"]["g_co2e_per_kwh"])
    green100 = {"kg": round((wind + solar) / 2 / 1000, 4), "comb": 0.0,
                "up": round((wind + solar) / 2 / 1000, 4),
                "cf": 100.0, "cf_lo": 100.0, "cf_hi": 100.0, "phys_cf": None,
                "tier": "2-synthetic",
                "note": "Green-e-style 100% renewable product (unlisted supplier): "
                        "embodied wind/solar manufacturing only"}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"year": YEAR, "entries": entries, "zips": zips, "us": combos["US"],
               "sups": sups, "zip_sup": zip_sup, "sup_products": sup_products,
               "green100": green100,
               "sources": "EPA eGRID2023 rev2; CRS 2025 Residual Mix (2023 data); "
                          "CEC 2023 Power Content Labels; IPCC AR5 A.III.2 upstream"}
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"Wrote {OUT}: {OUT.stat().st_size:,}b — {len(zips):,} ZIPs, "
          f"{len(entries)} subregion combos, {len(sups)} supplier products, "
          f"{len(zip_sup):,} CA ZIPs with candidates")

    # Parity checks against direct resolver calls
    cpsf = sups[sup_idx[("CLEANPOWERSF", "SuperGreen")]]
    direct = r.resolve("94110", supplier="CLEANPOWERSF", product="SuperGreen")
    assert abs(cpsf["kg"] - direct.kg_co2e_per_kwh) < 6e-5, (cpsf, direct)
    camx = entries[zips["94110"]]
    assert camx["sub"] == "CAMX" and abs(camx["loc"]["kg"] - r.resolve("94110", supplier="__NONE__").kg_co2e_per_kwh) < 6e-5
    print(f"parity OK — CAMX location {camx['loc']['kg']} kg/kWh, "
          f"CleanPowerSF SuperGreen {cpsf['kg']} kg/kWh")


if __name__ == "__main__":
    main()
