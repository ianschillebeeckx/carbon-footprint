"""Render the methodology pages from the assumptions registry.

  web/methodology.html  — every assumption: value, rationale, bias, sources,
                          code links (site/methodology-template.html + registry)
  web/naics.html        — the full browsable EPA/USEEIO factor table

The page and the app consume the same registry (src/cf/assumptions.py), so a
value can't drift between code and writeup. Enforcement here:
  - every <!--SECTION:x--> marker must exist for every section that has entries
  - every registry entry is rendered exactly once
  - every {{id}} inline placeholder must resolve

Run:  .venv/bin/python scripts/build_methodology.py   (also called by build_web.py)
"""

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cf.assumptions import ASSUMPTIONS, REGISTRY, SECTIONS, REPO  # noqa: E402
from cf import classify  # noqa: E402
from cf.naics_prep import CATEGORIES  # noqa: E402

TEMPLATE = ROOT / "site" / "methodology-template.html"
OUT = ROOT / "web" / "methodology.html"
OUT_NAICS = ROOT / "web" / "naics.html"

BIAS_LABEL = {"under": "understates", "over": "overstates",
              "neutral": "neutral", "varies": "varies"}


def inline_md(text: str) -> str:
    """Escape HTML, then render the tiny markdown subset rationales use."""
    s = html.escape(text, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               lambda m: f'<a href="{m.group(2)}"'
                         + (' target="_blank" rel="noopener"' if m.group(2).startswith("http") else "")
                         + f'>{m.group(1)}</a>', s)
    return s


def code_link(path: str) -> str:
    name = path.rstrip("/").split("/")[-1]
    return f'<a href="{REPO}{path}" target="_blank" rel="noopener"><code>{name}</code></a>'


def card(a) -> str:
    bias = BIAS_LABEL[a.bias]
    val = a.display or (str(a.value) if a.value is not None else "")
    head = [f'<h3><a class="anchor" href="#a-{a.id}">§</a> {html.escape(a.label)}']
    if val:
        head.append(f'<span class="val">{html.escape(val)}</span>')
    head.append(f'<span class="bias b-{a.bias}" title="Direction of error for your footprint">{bias}</span></h3>')
    meta = []
    if a.sources:
        meta.append("Sources: " + " · ".join(
            f'<a href="{u}" target="_blank" rel="noopener">{html.escape(t)}</a>' for t, u in a.sources))
    if a.code:
        meta.append("Code &amp; data: " + " · ".join(code_link(p) for p in a.code))
    meta_html = f'<p class="meta">{" &nbsp;|&nbsp; ".join(meta)}</p>' if meta else ""
    return (f'<article class="a" id="a-{a.id}">{" ".join(head)}'
            f'<p class="r">{inline_md(a.rationale)}</p>{meta_html}</article>')


def build_methodology() -> None:
    s = TEMPLATE.read_text()
    rendered = set()
    for sec in SECTIONS:
        entries = [a for a in ASSUMPTIONS if a.section == sec]
        marker = f"<!--SECTION:{sec}-->"
        assert marker in s, f"template missing {marker}"
        s = s.replace(marker, "\n".join(card(a) for a in entries))
        rendered.update(a.id for a in entries)
    missing = {a.id for a in ASSUMPTIONS} - rendered
    assert not missing, f"assumptions with unknown section, never rendered: {missing}"

    def inline_value(m):
        aid = m.group(1)
        assert aid in REGISTRY, f"unknown assumption id in template: {{{{{aid}}}}}"
        a = REGISTRY[aid]
        return html.escape(a.display or str(a.value))
    s = re.sub(r"\{\{([a-z0-9_.]+)\}\}", inline_value, s)
    leftovers = re.findall(r"<!--SECTION:[^>]+-->|\{\{[^}]+\}\}", s)
    assert not leftovers, f"unresolved placeholders: {leftovers}"

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(s)
    print(f"Built {OUT}: {len(s):,} bytes, {len(rendered)} assumptions")


NAICS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Carbon Ledger — Industry factor table</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --ground:#F7F8F5; --card:#FFFFFF; --ink:#262B21; --muted:#6C7465; --leaf:#3E7C3A;
          --chip:#E6EEE1; --line:#D9DED2; --mono: ui-monospace, "SF Mono", Menlo, monospace; }
  @media (prefers-color-scheme: dark) {
    :root { --ground:#161A15; --card:#1E241D; --ink:#E4E8DD; --muted:#98A18F; --leaf:#7DB979;
            --chip:#263124; --line:#333B31; } }
  * { box-sizing: border-box; }
  body { background: var(--ground); color: var(--ink); font-family: "Avenir Next", Seravek, system-ui, sans-serif; line-height: 1.45; margin: 0; padding: 0 16px 60px; }
  main { max-width: 900px; margin: 0 auto; }
  header.top { max-width: 900px; margin: 0 auto; display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; padding: 14px 0 10px; border-bottom: 1px solid var(--line); }
  header.top h1 { font-size: 1.02rem; font-weight: 600; margin: 0; }
  a { color: var(--leaf); font-size: 0.8rem; text-underline-offset: 2px; }
  p.lead { color: var(--muted); font-size: 0.88rem; }
  input#q { width: 100%; max-width: 420px; padding: 7px 10px; font: inherit; font-size: 0.88rem;
            border: 1px solid var(--line); border-radius: 8px; background: var(--card); color: var(--ink); }
  .count { color: var(--muted); font-size: 0.78rem; margin-left: 10px; }
  .tw { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; margin-top: 12px; font-size: 0.84rem; }
  th, td { text-align: left; padding: 5px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { position: sticky; top: 0; background: var(--ground); font-size: 0.72rem; text-transform: uppercase;
       letter-spacing: 0.06em; color: var(--muted); cursor: pointer; user-select: none; }
  td.f, td.c { font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
  td.cat { color: var(--muted); }
</style>
</head>
<body>
<header class="top">
  <h1>Carbon Ledger — Industry factor table</h1>
  <a href="methodology.html">← methodology</a>
  <a href="index.html">app</a>
</header>
<main>
<p class="lead">All __COUNT__ NAICS commodities the app can assign, with their EPA supply-chain factor
(kg CO₂e per 2022 dollar at purchaser price, retail margins included) and the category they roll up
into. From <a href="https://catalog.data.gov/dataset/supply-chain-greenhouse-gas-emission-factors-v1-3-by-naics-6"
target="_blank" rel="noopener">EPA Supply Chain GHG Emission Factors v1.3.0</a> — see the
<a href="methodology.html#a-gs.epa_factors">methodology entry</a> for what these factors do and don't
capture. Click a column header to sort.</p>
<input id="q" type="search" placeholder="Filter: code, title, keyword (toll, veterinary, streaming…)">
<span class="count" id="count"></span>
<div class="tw"><table id="t">
<thead><tr><th data-k="code">NAICS</th><th data-k="title">Commodity</th><th data-k="cat">Category</th><th data-k="factor">kg CO₂e/$</th></tr></thead>
<tbody></tbody>
</table></div>
</main>
<script>
"use strict";
const ROWS = __ROWS__;
const tbody = document.querySelector("#t tbody");
let sortK = "code", sortAsc = true;
function esc(s) { return String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function render() {
  const q = document.getElementById("q").value.trim().toLowerCase();
  const words = q.split(/\\s+/).filter(Boolean);
  let rows = ROWS.filter(r => words.every(w => r.hay.includes(w)));
  rows.sort((a, b) => {
    const va = a[sortK], vb = b[sortK];
    const c = typeof va === "number" ? va - vb : String(va).localeCompare(String(vb));
    return sortAsc ? c : -c;
  });
  document.getElementById("count").textContent = `${rows.length} of ${ROWS.length}`;
  tbody.innerHTML = rows.map(r =>
    `<tr><td class="c">${r.code}</td><td>${esc(r.title)}${r.basket ? " · 🧺 basket" : ""}</td>` +
    `<td class="cat">${esc(r.cat)}</td><td class="f">${r.factor.toFixed(3)}</td></tr>`).join("");
}
document.getElementById("q").oninput = render;
for (const th of document.querySelectorAll("th")) th.onclick = () => {
  const k = th.dataset.k;
  sortAsc = k === sortK ? !sortAsc : true;
  sortK = k;
  render();
};
render();
</script>
</body>
</html>
"""


def build_naics() -> None:
    cats = {k: lbl for k, (_sec, lbl) in CATEGORIES.items()}
    rows = []
    for e in classify.naics_all():
        cat = cats.get(e["category"], e["category"])
        rows.append({
            "code": e["code"], "title": e["title"], "cat": cat,
            "factor": round(e["factor"], 4), "basket": bool(e.get("basket")),
            "hay": " ".join([e["code"], e["title"], e.get("kw", ""), cat]).lower(),
        })
    page = NAICS_PAGE.replace("__COUNT__", str(len(rows))) \
                     .replace("__ROWS__", json.dumps(rows, separators=(",", ":")))
    OUT_NAICS.write_text(page)
    print(f"Built {OUT_NAICS}: {len(page):,} bytes, {len(rows)} codes")


if __name__ == "__main__":
    build_methodology()
    build_naics()
