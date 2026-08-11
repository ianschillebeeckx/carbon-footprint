"""Import a Monarch Money CSV export (Download CSV on the transactions
page) instead of pulling from the API."""

import csv
import datetime as dt
import io
import json
from pathlib import Path

DATA_DIR = Path("data")
TRANSACTIONS_FILE = DATA_DIR / "transactions.json"

REQUIRED = {"Date", "Category", "Amount"}


def parse(text: str) -> dict:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or not REQUIRED.issubset(rows[0].keys()):
        raise ValueError(
            "Doesn't look like a Monarch transactions CSV (need Date, Category, Amount columns)"
        )
    txns = []
    for r in rows:
        txns.append(
            {
                "date": r["Date"],
                "amount": float(str(r["Amount"]).replace(",", "").replace("$", "")),
                "merchant": r.get("Merchant") or "",
                "category": r.get("Category") or "Uncategorized",
                "group_type": None,  # CSV has no category group; mapping decides
                "pending": False,
                "hidden": False,
                "notes": r.get("Notes") or "",
            }
        )
    dates = sorted(t["date"] for t in txns)
    start, end = dates[0], dates[-1]
    days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days + 1
    return {
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": "csv",
        "start_date": start,
        "end_date": end,
        "months": max(1, round(days / 30.44)),
        "categories": [],
        "transactions": txns,
    }


def run(path: str) -> dict:
    data = parse(Path(path).read_text(encoding="utf-8-sig"))
    DATA_DIR.mkdir(exist_ok=True)
    TRANSACTIONS_FILE.write_text(json.dumps(data, indent=1))
    print(
        f"Imported {len(data['transactions'])} transactions "
        f"({data['start_date']} to {data['end_date']}, ~{data['months']} months) -> {TRANSACTIONS_FILE}"
    )
    return data
