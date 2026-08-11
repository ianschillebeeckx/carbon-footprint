"""Apply the category mapping to cached transactions and produce
per-field totals, monthly averages, and a per-month series."""

import csv
import json
from collections import defaultdict
from pathlib import Path

import yaml

from .fields import FIELDS, EXCLUDE

DATA_DIR = Path("data")
TRANSACTIONS_FILE = DATA_DIR / "transactions.json"
MAPPING_FILE = Path("config/mapping.yaml")
VALUES_FILE = DATA_DIR / "values.json"
CSV_FILE = DATA_DIR / "values.csv"


def _load_mapping():
    cfg = yaml.safe_load(MAPPING_FILE.read_text())
    categories = cfg.get("categories") or {}
    overrides = cfg.get("merchant_overrides") or []
    for cat, target in categories.items():
        keys = target.keys() if isinstance(target, dict) else [target]
        for k in keys:
            if k != EXCLUDE and k not in FIELDS:
                raise SystemExit(f"mapping.yaml: unknown field '{k}' for category '{cat}'")
    for o in overrides:
        if o["field"] != EXCLUDE and o["field"] not in FIELDS:
            raise SystemExit(f"mapping.yaml: unknown field '{o['field']}' in merchant override '{o['match']}'")
    return categories, overrides


def _target_for(txn, categories, overrides):
    """Return {field: weight} for a transaction, or None if unmapped."""
    merchant = txn["merchant"].lower()
    for o in overrides:
        if o["match"].lower() in merchant and (
            "category" not in o or o["category"] == txn["category"]
        ):
            return {o["field"]: 1.0}
    target = categories.get(txn["category"])
    if target is None:
        return None
    if isinstance(target, dict):
        return target
    return {target: 1.0}


def run() -> dict:
    data = json.loads(TRANSACTIONS_FILE.read_text())
    categories, overrides = _load_mapping()
    months = data["months"]

    totals = defaultdict(float)
    monthly = defaultdict(lambda: defaultdict(float))
    field_txns = defaultdict(list)
    excluded = defaultdict(float)
    unmapped = defaultdict(lambda: {"total": 0.0, "count": 0, "merchants": defaultdict(float)})

    for t in data["transactions"]:
        if t["pending"] or t["hidden"]:
            continue
        if t["group_type"] in ("income", "transfer"):
            continue
        spend = -t["amount"]  # Monarch expenses are negative; refunds net out
        month = t["date"][:7]
        target = _target_for(t, categories, overrides)
        if target is None:
            u = unmapped[t["category"]]
            u["total"] += spend
            u["count"] += 1
            u["merchants"][t["merchant"] or "(no merchant)"] += spend
            continue
        for field, weight in target.items():
            if field == EXCLUDE:
                excluded[t["category"]] += spend * weight
            else:
                totals[field] += spend * weight
                monthly[field][month] += spend * weight
                field_txns[field].append(
                    {
                        "date": t["date"],
                        "merchant": t["merchant"] or "(no merchant)",
                        "category": t["category"],
                        "amount": round(spend * weight, 2),
                    }
                )

    values = {
        "window": {"start": data["start_date"], "end": data["end_date"], "months": months},
        "fields": {
            key: {
                "section": section,
                "label": label,
                "monthly_avg": round(totals[key] / months, 2),
                "annual_total": round(totals[key], 2),
                "monthly": {m: round(v, 2) for m, v in sorted(monthly[key].items())},
                **_top_transactions(field_txns[key]),
            }
            for key, (section, label) in FIELDS.items()
        },
        "excluded": {k: round(v, 2) for k, v in sorted(excluded.items(), key=lambda x: -x[1])},
        "unmapped": {
            cat: {
                "total": round(u["total"], 2),
                "count": u["count"],
                "top_merchants": dict(sorted(u["merchants"].items(), key=lambda x: -x[1])[:5]),
            }
            for cat, u in sorted(unmapped.items(), key=lambda x: -x[1]["total"])
        },
    }

    DATA_DIR.mkdir(exist_ok=True)
    VALUES_FILE.write_text(json.dumps(values, indent=1))
    with CSV_FILE.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["section", "field", "monthly_avg", "annual_total"])
        for key, (section, label) in FIELDS.items():
            w.writerow([section, label, values["fields"][key]["monthly_avg"], values["fields"][key]["annual_total"]])

    _print_summary(values)
    return values


def _top_transactions(txns, limit=10):
    top = sorted(txns, key=lambda t: -t["amount"])[:limit]
    return {
        "txn_count": len(txns),
        "top_transactions": top,
        "more_count": max(0, len(txns) - limit),
        "more_total": round(sum(t["amount"] for t in txns) - sum(t["amount"] for t in top), 2),
    }


def _print_summary(values):
    print(f"\nWindow: {values['window']['start']} to {values['window']['end']} ({values['window']['months']} months)\n")
    section = None
    for key, (sec, label) in FIELDS.items():
        if sec != section:
            section = sec
            print(f"--- {sec} ($/month) ---")
        print(f"  {label:<38} {values['fields'][key]['monthly_avg']:>10,.2f}")
    total = sum(v["monthly_avg"] for v in values["fields"].values())
    print(f"  {'TOTAL goods+services':<38} {total:>10,.2f}")

    if values["unmapped"]:
        print("\nUnmapped categories (add these to config/mapping.yaml):")
        for cat, u in values["unmapped"].items():
            print(f"  {cat:<30} ${u['total']:>10,.2f}  ({u['count']} txns)")
    excl = sum(values["excluded"].values())
    print(f"\nExcluded (food/travel/housing/etc.): ${excl:,.2f} over the window")
    print(f"Wrote {VALUES_FILE} and {CSV_FILE}")
