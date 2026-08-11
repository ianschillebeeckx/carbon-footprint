"""Inject data/values.json into site/template.html -> site/index.html."""

import json
from pathlib import Path

VALUES_FILE = Path("data/values.json")
TEMPLATE = Path("site/template.html")
OUTPUT = Path("site/index.html")
PLACEHOLDER = "/*__VALUES__*/null"


def run() -> Path:
    values = VALUES_FILE.read_text()
    html = TEMPLATE.read_text()
    assert PLACEHOLDER in html, f"placeholder missing from {TEMPLATE}"
    OUTPUT.write_text(html.replace(PLACEHOLDER, values))
    print(f"Wrote {OUTPUT}")
    return OUTPUT
