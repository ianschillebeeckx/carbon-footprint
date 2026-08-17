"""Interval-CSV column mapper — local-server twin of the worker's
/api/parse-hourly route (worker/src/index.js is the deployed copy; keep the
schema and prompt in sync). The LLM only names columns/units/date order from
the file's first rows; all unit math and quality gating stays in the browser.
"""

import json
import urllib.request

SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "reason": {"type": ["string", "null"]},
        "header_rows": {"type": "integer"},
        "ts_col": {"type": ["integer", "null"]},
        "date_col": {"type": ["integer", "null"]},
        "time_col": {"type": ["integer", "null"]},
        "usage_col": {"type": ["integer", "null"]},
        "usage_unit": {"type": "string", "enum": ["kwh", "wh", "kw", "w"]},
        "date_order": {"type": "string", "enum": ["mdy", "dmy", "ymd"]},
    },
    "required": ["ok", "reason", "header_rows", "ts_col", "date_col", "time_col",
                 "usage_col", "usage_unit", "date_order"],
    "additionalProperties": False,
}

PROMPT = (
    "Below are the first rows of a CSV a household downloaded from their electric "
    "utility, expected to be interval (hourly or 15-minute) electricity CONSUMPTION.\n"
    "Identify, using 0-based comma-split column indexes:\n"
    "- ts_col: a combined date+time column (else null and use date_col/time_col)\n"
    "- date_col / time_col: separate date and time-of-day columns (null if ts_col)\n"
    "- usage_col: the energy/power reading per interval. NOT cost, NOT temperature, "
    "NOT meter register/cumulative totals (a register only ever increases; per-interval "
    "usage fluctuates).\n"
    "- usage_unit: kwh, wh, kw or w — from the header text or magnitudes (a home draws "
    "~0.2-3 kW; hourly kWh ~0.1-5; a 15-min kWh ~0.02-1.5).\n"
    "- header_rows: how many leading rows are headers/preamble before data starts.\n"
    "- date_order: mdy, dmy or ymd for slash-separated dates (judge from values >12 or context).\n"
    "If this is not interval electricity usage data (billing summary, gas therms, water), "
    "set ok=false with a one-sentence reason.\n\n"
)


def map_columns(sample: str, api_key: str, model: str = "claude-haiku-4-5") -> dict:
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({
            "model": model,
            "max_tokens": 500,
            "temperature": 0,
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": PROMPT + sample}],
        }).encode(),
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    text = next(b["text"] for b in data["content"] if b["type"] == "text")
    return json.loads(text)
