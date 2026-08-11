"""v2 transaction classification pipeline.

CSV -> normalize merchants -> dedupe -> tier 0 (cache + seeds) ->
tier 1 (vector top-10) -> tier 2 (LLM picks NAICS + confidence, batched,
top ~70% of spend only; the tail takes the top vector hit) -> per-transaction
kg/$ from EPA supply-chain factors -> data/v2_classified.json.

LLM access: Anthropic SDK when credentials exist (ANTHROPIC_API_KEY or an
`ant auth login` profile), else `claude -p` headless via the local CLI.
"""

import csv
import io
import json
import re
import subprocess
import datetime as dt
from pathlib import Path

from . import embed_index

DATA_DIR = Path("data")
CACHE_FILE = DATA_DIR / "merchant_cache.json"
OUT_FILE = DATA_DIR / "v2_classified.json"
INDEX_FILE = DATA_DIR / "naics_index.json"

LLM_SPEND_COVERAGE = 0.70   # merchants covering this share of |spend| get the LLM
LLM_BATCH_SIZE = 40

# EPA factors are per 2022 USD; deflate nominal spend before multiplying.
# CPI-U all-items; 2022-23 exact from BLS, later years chained estimates.
CPI_DEFLATOR = {2022: 1.000, 2023: 0.960, 2024: 0.933, 2025: 0.909, 2026: 0.882}


def deflator_for(date_str: str) -> float:
    try:
        year = int(date_str[:4])
    except (ValueError, TypeError):
        return 1.0
    return CPI_DEFLATOR.get(min(max(year, 2022), 2026), 1.0)


# Codes the EPA table has no factor for (dropped rows, not nulls). Remap to
# the standard proxy instead of silently missing the join.
NO_FACTOR_REMAP = {
    "814110": "561720",  # private households (housekeeper/nanny) -> janitorial services
}

# Money movement, not consumption: skip classification entirely (tier 0 rule).
NON_PURCHASE_HINTS = {
    "transfer", "credit card payment", "paychecks", "paycheck", "bonus", "other income",
    "interest", "tax refund", "ca benefits", "credit card rewards", "cash & atm", "check",
    "taxes", "mortgage", "heloc", "loan repayment", "balance adjustments",
}
NON_PURCHASE_MERCHANT = re.compile(
    r"\b(transfer|payment thank you|autopay|paycheck|payroll|direct deposit|withdrawal|zelle|cash app|atm|irs|franchise tax)\b", re.I)

# Health insurance premiums fund healthcare delivery, which is ~3x more
# emissions-intensive than the carrier's back office (524114 at 0.033 prices
# the insurer, not the care). Tier-0 rule: route known health insurers to a
# delivery blend (~0.12 kg/$), overriding stale llm/vector cache entries but
# never a manual/confirmed one.
HEALTH_INSURER = re.compile(
    r"\b(kaiser|blue shield|blue cross|anthem|aetna|cigna|united ?health|humana|"
    r"oscar health|health net|molina|delta dental|vsp)\b", re.I)
HEALTH_INSURANCE_MIX = [{"naics": "622110", "weight": 0.6},   # hospitals
                        {"naics": "621111", "weight": 0.4}]   # physicians

# ---------------- merchant normalization ----------------

_PREFIXES = re.compile(r"^(SQ \*|SQ\*|TST\*|TST \*|PYPL \*|PAYPAL \*|IN \*|IN\*|GOOGLE \*|APPLE\.COM/BILL|AMZN MKTP [A-Z]{2}\*?|CKE\*|EV \*|SP )", re.I)
_STORE_NO = re.compile(r"\s*#?\d{3,}\s*$")
_MULTISPACE = re.compile(r"\s{2,}")


def normalize_merchant(name: str) -> str:
    s = name.strip()
    s = _PREFIXES.sub("", s)
    s = _STORE_NO.sub("", s)
    s = _MULTISPACE.sub(" ", s)
    return s.strip(" -*·").strip() or name.strip()


# ---------------- CSV parsing (flexible) ----------------

def parse_csv(text: str) -> tuple[list[dict], dict]:
    """Require Merchant + Amount columns (case-insensitive); Date and
    Category are used when present."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise ValueError("Empty CSV")
    cols = {c.lower().strip(): c for c in rows[0].keys()}
    m_col = next((cols[k] for k in ("merchant", "description", "name", "payee") if k in cols), None)
    a_col = next((cols[k] for k in ("amount",) if k in cols), None)
    if not m_col or not a_col:
        raise ValueError("CSV needs at least a Merchant (or Description/Payee) and an Amount column")
    d_col = next((cols[k] for k in ("date", "transaction date", "posted date") if k in cols), None)
    c_col = next((cols[k] for k in ("category",) if k in cols), None)

    txns = []
    for r in rows:
        raw_amount = str(r.get(a_col, "")).replace("$", "").replace(",", "").strip()
        if not raw_amount or not r.get(m_col):
            continue
        try:
            amount = float(raw_amount)
        except ValueError:
            continue
        if amount == 0:
            continue
        txns.append({
            "date": (r.get(d_col) or "").strip() if d_col else "",
            "merchant_raw": r[m_col].strip(),
            "merchant": normalize_merchant(r[m_col]),
            "category_hint": (r.get(c_col) or "").strip() if c_col else "",
            "amount": amount,
        })
    if not txns:
        raise ValueError("No parseable transactions found")
    dates = sorted(t["date"] for t in txns if t["date"])
    months = 1
    if dates:
        days = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days + 1
        months = max(1, round(days / 30.44))
    meta = {"start": dates[0] if dates else None, "end": dates[-1] if dates else None,
            "months": months, "count": len(txns)}
    return txns, meta


# ---------------- LLM tier ----------------

def _llm_available_via_sdk() -> bool:
    import os
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _call_llm_sdk(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=8000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model refused classification request")
    return next(b.text for b in response.content if b.type == "text")


def _call_llm_cli(prompt: str) -> str:
    """Fallback: Claude Code headless (uses the local `claude` login)."""
    proc = subprocess.run(
        ["claude", "-p", "--model", "opus", "--output-format", "json"],
        input=prompt, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr[:300]}")
    return json.loads(proc.stdout)["result"]


def _extract_json(text: str):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError(f"no JSON array in LLM output: {text[:200]}")
    return json.loads(m.group(0))


def classify_batch_llm(batch: list[dict]) -> list[dict]:
    """batch items: {merchant, category_hint, total, candidates:[{code,title}]}
    Returns [{merchant, naics, confidence}]."""
    lines = []
    for i, b in enumerate(batch):
        cands = "; ".join(f"{c['code']}={c['title']}" for c in b["candidates"])
        hint = f" (statement category: {b['category_hint']})" if b["category_hint"] else ""
        lines.append(f'{i}. "{b["merchant"]}"{hint}, ${b["total"]:,.0f} total. Candidates: {cands}')
    prompt = (
        "Assign each consumer credit-card merchant below to the single best NAICS 2022 code "
        "for WHAT WAS BOUGHT, not where it was bought — the code feeds a per-dollar emission "
        "factor, and retail codes price only the store's markup, not the goods on the shelf.\n"
        "Rules:\n"
        "- Goods from a single-category brand or maker: use the commodity/manufacturing code "
        "(sectors 11-33). Detergent is 325611, an apparel brand is 315xxx, not a store code.\n"
        "- Multi-category stores (home centers, pharmacies, department stores, warehouse clubs, "
        "online marketplaces): use the retail store code — the pipeline re-prices those as "
        "commodity baskets of what's sold there.\n"
        "- A bare retail/delivery code is right only when the purchase IS the retail service "
        "(membership fee, delivery fee) or the goods are secondhand.\n"
        "- Services: the service's own code (51-81). Restaurants/food/travel/utilities keep "
        "their usual codes (722xxx, 445110, 481111, ...); they are scoped out downstream.\n"
        "- Health insurance premiums fund healthcare delivery: use 622110, not 524114.\n"
        "- Digital subscriptions: 513210 software/SaaS, 516210 streaming, 518210 cloud storage.\n"
        "Prefer a code from the merchant's candidate list when one fits; otherwise give your "
        "best NAICS-6 from anywhere in NAICS 2022. The statement category says what was bought "
        "(strong signal); the merchant name says where (weak signal).\n\n"
        + "\n".join(lines)
        + '\n\nReturn ONLY a JSON array, one object per line item, in order: '
        '[{"i": 0, "naics": "722515", "confidence": 0.95}, ...] '
        "where confidence is 0-1 for how sure you are of the industry assignment."
    )
    text = _call_llm_sdk(prompt) if _llm_available_via_sdk() else _call_llm_cli(prompt)
    out = _extract_json(text)
    results = []
    for b, r in zip(batch, sorted(out, key=lambda r: r["i"])):
        results.append({"merchant": b["merchant"], "naics": str(r["naics"]), "confidence": float(r["confidence"])})
    return results


# ---------------- pipeline ----------------

def run(csv_path: str, coverage: float = LLM_SPEND_COVERAGE) -> dict:
    text = Path(csv_path).read_text(encoding="utf-8-sig")
    txns, meta = parse_csv(text)
    index = json.loads(INDEX_FILE.read_text())
    by_code = {e["code"]: e for e in index["entries"]}
    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}

    # dedupe merchants (keyed by normalized name + dominant category hint)
    merchants: dict[str, dict] = {}
    for t in txns:
        key = f"{t['merchant']}|{t['category_hint']}"
        m = merchants.setdefault(key, {"merchant": t["merchant"], "category_hint": t["category_hint"],
                                       "total": 0.0, "count": 0})
        m["total"] += abs(t["amount"])
        m["count"] += 1
    print(f"{meta['count']} transactions -> {len(merchants)} unique merchant/category pairs")

    # tier 0: cache + rules; tier 1: vector candidates. Cache resolution:
    # exact "merchant|hint" beats the merchant-wide "merchant|*" wildcard.
    # Rules re-apply over stale llm/vector cache entries, but never over
    # manual/confirmed ones.
    for key, m in merchants.items():
        cached, scope = cache.get(key), "exact"
        if cached is None:
            cached, scope = cache.get(f"{m['merchant']}|*"), "merchant"
        user_locked = cached is not None and cached.get("source") in ("manual", "confirmed")
        if not user_locked and HEALTH_INSURER.search(m["merchant"]):
            m["assignment"] = {"naics": None, "mix": HEALTH_INSURANCE_MIX,
                               "confidence": 1.0, "source": "rule"}
            continue
        if cached is not None:
            m["assignment"] = cached
            if scope == "merchant":
                m["scope"] = "merchant"
            continue
        hint = m["category_hint"].lower()
        if (hint and hint in NON_PURCHASE_HINTS) or (not hint and NON_PURCHASE_MERCHANT.search(m["merchant"])):
            m["assignment"] = {"naics": None, "confidence": 1.0, "source": "rule"}
            continue
        query = m["merchant"] + (f" · {m['category_hint']}" if m["category_hint"] else "")
        m["candidates"] = embed_index.top_k(query, 10)

    # spend ranking: top `coverage` share of spend -> LLM; rest -> top vector hit
    uncached = [m for m in merchants.values() if "assignment" not in m]
    uncached.sort(key=lambda m: -m["total"])
    grand = sum(m["total"] for m in uncached) or 1.0
    running = 0.0
    llm_pool, tail = [], []
    for m in uncached:
        (llm_pool if running / grand < coverage else tail).append(m)
        running += m["total"]
    print(f"cache hits: {len(merchants) - len(uncached)}; LLM: {len(llm_pool)} merchants "
          f"({sum(m['total'] for m in llm_pool)/grand:.0%} of uncached spend); vector-only: {len(tail)}")

    # tier 2: batched LLM
    for start in range(0, len(llm_pool), LLM_BATCH_SIZE):
        batch = llm_pool[start:start + LLM_BATCH_SIZE]
        payload = [{"merchant": m["merchant"], "category_hint": m["category_hint"],
                    "total": m["total"],
                    "candidates": [{"code": c["code"], "title": c["title"]} for c in m["candidates"]]}
                   for m in batch]
        print(f"  LLM batch {start // LLM_BATCH_SIZE + 1} ({len(batch)} merchants)...")
        results = classify_batch_llm(payload)
        for m, r in zip(batch, results):
            m["assignment"] = {"naics": r["naics"], "confidence": r["confidence"], "source": "llm"}

    for m in tail:
        top = m["candidates"][0]
        m["assignment"] = {"naics": top["code"], "confidence": None, "source": "vector"}

    # write back cache — but never materialize a wildcard-derived assignment
    # as an exact entry, or later edits to the merchant-wide rule wouldn't
    # propagate (exact beats wildcard).
    for key, m in merchants.items():
        if m.get("scope") == "merchant":
            continue
        cache[key] = m["assignment"]
    DATA_DIR.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=1))

    # per-transaction assignment
    out_txns = []
    for t in txns:
        key = f"{t['merchant']}|{t['category_hint']}"
        a = merchants[key]["assignment"]
        out_txns.append({**t, **expand_assignment(a, by_code),
                         "deflator": deflator_for(t["date"]),
                         "scope": merchants[key].get("scope"),
                         "confidence": a.get("confidence"), "source": a["source"]})

    meta["dataset"] = index.get("dataset", "EPA supply-chain factors")
    meta["deflators"] = CPI_DEFLATOR
    result = {"meta": meta, "categories": index["categories"], "transactions": out_txns}
    OUT_FILE.write_text(json.dumps(result, indent=1))
    print(f"Wrote {OUT_FILE}")
    _summary(result)
    return result


def _resolve_basket(host_code: str, parts_in: list[dict], by_code: dict) -> tuple[list[dict], float]:
    """Resolve basket parts (commodity codes; weights are the manufacturing
    mix) into detailed parts + blended factor. Each part's `factor` is the
    with-margins total; `factor_production`/`factor_margins` split out the
    manufacturing chain vs the retail/distribution layer, which is additive
    on top of the mix rather than a competing share."""
    commodity_parts = [p for p in parts_in if p["naics"] != host_code and p["naics"] in by_code]
    parts, total_w = [], sum(p["weight"] for p in commodity_parts) or 1.0
    for p in commodity_parts:
        e = by_code[p["naics"]]
        parts.append({"naics": e["code"], "title": e["title"], "factor": e["factor"],
                      "factor_production": e.get("factor_production", e["factor"]),
                      "factor_margins": e.get("factor_margins", 0.0),
                      "weight": p["weight"] / total_w})
    factor = round(sum(p["factor"] * p["weight"] for p in parts), 4) if parts else None
    return parts, factor


def expand_assignment(a: dict, by_code: dict) -> dict:
    """Resolve a cached assignment ({naics}, {mix:[...]}, or
    {naics, basket:[...]}) into the display/rollup fields stored on each
    transaction."""
    if a.get("mix"):
        parts, total_w = [], sum(p["weight"] for p in a["mix"]) or 1.0
        for p in a["mix"]:
            e = by_code.get(p["naics"])
            if e is None:
                continue
            parts.append({"naics": e["code"], "title": e["title"], "factor": e["factor"],
                          "category": e["category"], "weight": p["weight"] / total_w})
        if parts:
            factor = sum(p["factor"] * p["weight"] for p in parts)
            label = "Split: " + ", ".join(f"{p['weight']:.0%} {p['title']}" for p in parts)
            return {"naics": None, "naics_title": label, "factor": round(factor, 4),
                    "category": "mixed", "mix": parts, "basket": None,
                    "margin_warn": False, "unmapped": False}
    if a.get("naics") is None:
        return {"naics": None, "naics_title": "Non-purchase (transfer/income/payment)",
                "factor": None, "category": "excluded", "mix": None, "basket": None,
                "margin_warn": False, "unmapped": False}
    code = NO_FACTOR_REMAP.get(a["naics"], a["naics"])
    entry = by_code.get(code)
    if entry is None:
        # EPA has no factor for this code (dropped row, e.g. 92xxxx/221xxx).
        # Surface it for correction rather than silently zeroing.
        return {"naics": a["naics"], "naics_title": f"{a['naics']} — no EPA factor, assign an industry",
                "factor": None, "category": "excluded", "mix": None, "basket": None,
                "margin_warn": False, "unmapped": True}
    if a.get("basket"):   # user-customized commodity basket for this merchant
        parts, factor = _resolve_basket(code, a["basket"], by_code)
        prod = round(sum(p["factor_production"] * p["weight"] for p in parts), 4) if parts else None
        marg = round(sum(p["factor_margins"] * p["weight"] for p in parts), 4) if parts else None
        return {"naics": code, "naics_title": entry["title"] + " · custom basket",
                "factor": factor, "category": entry["category"], "mix": None,
                "factor_production": prod, "factor_margins": marg,
                "naics2017": entry.get("naics2017"), "useeio": entry.get("useeio"),
                "basket": parts, "basket_custom": True,
                "margin_warn": False, "unmapped": False}
    default_basket = None
    if entry.get("basket"):
        default_basket, _ = _resolve_basket(code, entry["basket"], by_code)
    out = {"naics": code, "naics_title": entry["title"],
           "factor": entry["factor"], "category": entry["category"],
           "factor_production": entry.get("factor_production"),
           "factor_margins": entry.get("factor_margins"),
           "naics2017": entry.get("naics2017"), "useeio": entry.get("useeio"),
           "factor_note": entry.get("factor_note"),
           "mix": None, "basket": default_basket, "unmapped": False}
    # A goods purchase mapped to a zero-margin row usually means the store got
    # mapped instead of the product (secondhand 4595x is deliberate).
    out["margin_warn"] = bool(
        entry["category"].startswith("goods_") and not default_basket
        and entry.get("factor_margins", 0) == 0 and not code.startswith("4595"))
    return out


def apply_correction(merchant: str, category_hint: str, naics: str | None = None,
                     mix: list[dict] | None = None, basket: list[dict] | None = None,
                     source: str = "manual", all_categories: bool = False) -> dict:
    """Manual correction from the v2 UI: update the merchant cache and rewrite
    all matching transactions in v2_classified.json. With all_categories the
    rule is stored merchant-wide ("merchant|*") and applies to every category
    of the merchant except those pinned by a manual/confirmed exact rule.
    Returns the new assignment fields for the client to merge."""
    index = json.loads(INDEX_FILE.read_text())
    by_code = {e["code"]: e for e in index["entries"]}
    key = f"{merchant}|*" if all_categories else f"{merchant}|{category_hint}"

    if basket:
        if not naics:
            raise ValueError("basket requires the merchant's NAICS code")
        for p in basket:
            if p["naics"] != naics and p["naics"] not in by_code:
                raise ValueError(f"unknown NAICS code {p['naics']}")
            if not (0 < float(p["weight"])):
                raise ValueError("basket weights must be positive")
        assignment = {"naics": naics,
                      "basket": [{"naics": p["naics"], "weight": float(p["weight"])} for p in basket],
                      "confidence": 1.0, "source": source}
    elif mix:
        for p in mix:
            if p["naics"] not in by_code:
                raise ValueError(f"unknown NAICS code {p['naics']}")
            if not (0 < float(p["weight"])):
                raise ValueError("split weights must be positive")
        assignment = {"naics": None, "mix": [{"naics": p["naics"], "weight": float(p["weight"])} for p in mix],
                      "confidence": 1.0, "source": source}
    else:
        if naics is not None and naics not in by_code and naics not in NO_FACTOR_REMAP:
            raise ValueError(f"unknown NAICS code {naics}")
        assignment = {"naics": naics, "confidence": 1.0, "source": source}

    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    cache[key] = assignment
    locked_hints = []
    if all_categories:
        # exact entries would shadow the new merchant-wide rule: drop the
        # machine-made ones, keep (and report) user-pinned category rules.
        for k in [k for k in cache if k.startswith(f"{merchant}|") and k != key]:
            if cache[k].get("source") in ("manual", "confirmed"):
                locked_hints.append(k.split("|", 1)[1])
            else:
                del cache[k]
    CACHE_FILE.write_text(json.dumps(cache, indent=1))

    fields = expand_assignment(assignment, by_code)
    if fields["naics"] is None and not fields.get("mix"):
        fields["naics_title"] = "Non-purchase (manual)"
    fields["scope"] = "merchant" if all_categories else None

    if OUT_FILE.exists():
        result = json.loads(OUT_FILE.read_text())
        for t in result["transactions"]:
            if t["merchant"] != merchant:
                continue
            if all_categories:
                if t["category_hint"] in locked_hints:
                    continue
            elif t["category_hint"] != category_hint:
                continue
            t.update(fields)
            t["source"] = source
            t["confidence"] = 1.0
        OUT_FILE.write_text(json.dumps(result, indent=1))
    return {"assignment": fields, "locked_hints": locked_hints}


# Representative NAICS per category, used when the user assigns a top-level
# category directly instead of a specific industry.
CATEGORY_DEFAULT_NAICS = {
    "goods_furniture_appliances": "449110",
    "goods_clothing": "458110",
    "goods_entertainment": "459110",
    "goods_paper_office_reading": "459410",
    "goods_personal_care_cleaning": "456120",
    "goods_auto_parts": "441330",
    "goods_medical": "456110",
    "goods_other": "459999",
    "services_health_care": "621111",
    "services_education": "611110",
    "services_information_communication": "517112",
    "digital_subscriptions": "513210",
    "services_vehicle_service": "811111",
    "services_personal_business_finance": "522110",
    "services_household_maintenance_repair": "561720",
    "services_organizations_charity": "813211",
    "services_other": "812990",
}


def default_naics() -> dict:
    """Validated {category: representative NAICS} for the coarse dropdown."""
    index = json.loads(INDEX_FILE.read_text())
    by_code = {e["code"] for e in index["entries"]}
    by_cat: dict[str, str] = {}
    for e in index["entries"]:
        by_cat.setdefault(e["category"], e["code"])
    out = {}
    for cat, code in CATEGORY_DEFAULT_NAICS.items():
        out[cat] = code if code in by_code else by_cat.get(cat)
    return {k: v for k, v in out.items() if v}


def basket_options() -> list[dict]:
    """Commodity choices for the basket editor: every household-plausible
    physical-good commodity — goods-category rows passing the EPA
    "margins > 0" physical-good test (excluding store codes, which are
    baskets themselves) — plus anything referenced by a default basket."""
    from .naics_prep import RETAIL_BASKETS, GENERAL_MERCH_BASKET
    index = json.loads(INDEX_FILE.read_text())
    by_code = {e["code"]: e for e in index["entries"]}
    codes = {c for parts in RETAIL_BASKETS.values() for c, _ in parts}
    codes |= {c for c, _ in GENERAL_MERCH_BASKET}
    for e in index["entries"]:
        if (e["category"].startswith("goods_") and e.get("factor_margins", 0) > 0
                and not e.get("basket") and not e["code"].startswith(("44", "45"))):
            codes.add(e["code"])
    out = [{"code": c, "title": by_code[c]["title"], "factor": by_code[c]["factor"],
            "category": by_code[c]["category"],
            "factor_production": by_code[c].get("factor_production", by_code[c]["factor"]),
            "factor_margins": by_code[c].get("factor_margins", 0.0)}
           for c in codes if c in by_code]
    return sorted(out, key=lambda o: (o["category"], o["title"]))


def naics_options() -> list[dict]:
    """Curated consumer NAICS subset for the correction dropdown. `basket`
    marks store codes that are re-priced as commodity baskets (picking one
    assigns the blended manufacturing factor, not the retail service)."""
    from .naics_prep import ENRICH
    index = json.loads(INDEX_FILE.read_text())
    return [{"code": e["code"], "title": e["title"], "category": e["category"],
             "basket": bool(e.get("basket"))}
            for e in index["entries"] if e["code"] in ENRICH]


def _summary(result):
    from collections import defaultdict
    months = result["meta"]["months"]
    dollars = defaultdict(float)
    kg = defaultdict(float)
    for t in result["transactions"]:
        if t["naics"] is None and not t.get("mix"):
            continue                               # money movement: out of every denominator
        spend = -t["amount"]                       # refunds flow through as negative
        spend22 = spend * t.get("deflator", 1.0)   # factors are per 2022 USD
        if t.get("mix"):
            for p in t["mix"]:
                dollars[p["category"]] += spend * p["weight"]
                kg[p["category"]] += spend22 * p["weight"] * p["factor"]
        else:
            dollars[t["category"]] += spend
            if t["factor"] is not None:
                kg[t["category"]] += spend22 * t["factor"]
    print(f"\n{'category':<40} {'$/mo':>9} {'kg/mo':>8} {'kg/$':>6}")
    total_kg = 0.0
    for cat, info in result["categories"].items():
        if cat == "excluded" or dollars[cat] == 0:
            continue
        d, k = dollars[cat] / months, kg[cat] / months
        total_kg += k
        print(f"{info['label']:<40} {d:>9,.0f} {k:>8,.0f} {k/d if d else 0:>6.2f}")
    print(f"{'EXCLUDED (food/travel/housing/...)':<40} {dollars['excluded']/months:>9,.0f}")
    print(f"\nG&S footprint: {total_kg * 12 / 1000:.1f} t CO2e/yr")
