"""Build the static web app (web/index.html) from site/v2-template.html.

One source template serves two backends: the local Python server injects
data/state server-side; this script produces the Cloudflare version where
static reference data (NAICS index, options) is inlined at build time,
user state lives in localStorage, and classification goes through the
Worker API (shared merchant cache + LLM) instead of the local pipeline.

Every transformation asserts it matched, so template drift fails the build
instead of silently shipping a broken page.

Run:  .venv/bin/python scripts/build_web.py
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "src")
from cf import classify  # noqa: E402
from cf.naics_prep import CATEGORIES  # noqa: E402

TEMPLATE = Path("site/v2-template.html")
OUT = Path("web/index.html")
INDEX = Path("data/naics_index.json")

n_replacements = 0


def sub(s: str, old: str, new: str, count: int = 1) -> str:
    global n_replacements
    found = s.count(old)
    assert found >= count, f"template drift: not found ({found}/{count}): {old[:80]!r}"
    n_replacements += 1
    return s.replace(old, new, count)


def build() -> None:
    s = TEMPLATE.read_text()
    index = json.loads(INDEX.read_text())

    # Compact per-code entries for client-side assignment expansion.
    by_code = {}
    for e in index["entries"]:
        by_code[e["code"]] = {
            "code": e["code"], "title": e["title"], "factor": e["factor"],
            "factor_production": e.get("factor_production", e["factor"]),
            "factor_margins": e.get("factor_margins", 0.0),
            "category": e["category"],
            "naics2017": e.get("naics2017"), "useeio": e.get("useeio"),
            **({"basket": e["basket"]} if e.get("basket") else {}),
            **({"factor_note": e["factor_note"]} if e.get("factor_note") else {}),
        }

    categories = {k: {"section": sec, "label": lbl} for k, (sec, lbl) in CATEGORIES.items()}

    # LLMs sometimes emit 2017-vintage codes; the EPA CSV is the crosswalk.
    # 2017 codes that fan out to multiple 2022 codes get a curated consumer
    # target (auto-picking the first row once sent Amazon to motorcycle
    # dealers); unique mappings are automatic.
    CURATED_2017 = {
        "454110": "459999",  # e-commerce -> online marketplace (basket-priced)
        "454390": "459999",  # direct selling -> general merch
        "453998": "459999",  # misc store retailers -> general merch
        "515120": "516210",  # TV broadcasting -> streaming/media
        "519130": "516210",  # internet publishing -> streaming/social media
        "517312": "517112",  # wireless carriers, 2022 renumbering
        "517911": "517121",  # telecom resellers
        "212113": "212114",  # anthracite mining (not exactly consumer-relevant)
    }
    import csv as _csv
    from collections import defaultdict
    fan = defaultdict(set)
    with open("data/naics2022_with_2017_emission_factors_1.csv") as fh:
        for row in _csv.DictReader(fh):
            c17, c22 = row["2017 NAICS Code"].strip(), row["2022 NAICS Code"].strip()
            if c17 and c22 in by_code and c17 not in by_code:
                fan[c17].add(c22)
    remap = dict(classify.NO_FACTOR_REMAP)
    for c17, targets in fan.items():
        if len(targets) == 1:
            remap[c17] = next(iter(targets))
        else:
            assert c17 in CURATED_2017, f"ambiguous 2017 code {c17} needs a curated target"
            remap[c17] = CURATED_2017[c17]

    runtime = WEB_RUNTIME \
        .replace("__BY_CODE__", json.dumps(by_code, separators=(",", ":"))) \
        .replace("__CATEGORIES__", json.dumps(categories, separators=(",", ":"))) \
        .replace("__DATASET__", json.dumps(index.get("dataset", "EPA supply-chain factors"))) \
        .replace("__DEFLATORS__", json.dumps(classify.CPI_DEFLATOR)) \
        .replace("__NP_HINTS__", json.dumps(sorted(classify.NON_PURCHASE_HINTS))) \
        .replace("__NP_MERCHANT_RE__", js_regex(classify.NON_PURCHASE_MERCHANT)) \
        .replace("__PREFIX_RE__", js_regex(classify._PREFIXES)) \
        .replace("__HEALTH_RE__", js_regex(classify.HEALTH_INSURER)) \
        .replace("__HEALTH_MIX__", json.dumps(classify.HEALTH_INSURANCE_MIX)) \
    .replace("__HINT_RULES__", json.dumps(classify.HINT_RULES)) \
        .replace("__REMAP__", json.dumps(remap, separators=(",", ":")))

    # ---- static reference data: inline at build time (as the server does) ----
    s = sub(s, "/*__NAICS_OPTIONS__*/null", json.dumps(classify.naics_options(), separators=(",", ":")))
    s = sub(s, "/*__CAT_DEFAULTS__*/null", json.dumps(classify.default_naics(), separators=(",", ":")))
    s = sub(s, "/*__BASKET_OPTIONS__*/null", json.dumps(classify.basket_options(), separators=(",", ":")))
    s = sub(s, "/*__NAICS_ALL__*/null", json.dumps(classify.naics_all(), separators=(",", ":")))

    s = sub(s, "/*__CATS__*/null", json.dumps(categories, separators=(",", ":")))

    # ---- user state: localStorage instead of server injection ----
    s = sub(s, "/*__V2DATA__*/null", 'JSON.parse(localStorage.getItem("cf_data") || "null")')
    s = sub(s, "/*__TRAVEL__*/null", 'JSON.parse(localStorage.getItem("cf_travel") || "null")')
    s = sub(s, "/*__HOME__*/null", 'JSON.parse(localStorage.getItem("cf_home") || "null")')
    s = sub(s, "/*__FOOD__*/null", 'JSON.parse(localStorage.getItem("cf_food") || "null")')
    s = sub(s, "/*__OFFSETS__*/null", 'JSON.parse(localStorage.getItem("cf_offsets") || "null")')

    # ---- inject the web runtime right after "use strict" ----
    s = sub(s, '"use strict";', '"use strict";\n' + runtime)

    # ---- input autosaves -> localStorage ----
    global n_replacements
    s2, n = re.subn(
        r'fetch\("/api/v2/(travel|home|food|offsets)", \{\s*'
        r'method: "POST", headers: \{"Content-Type": "application/json"\}, body: JSON\.stringify\((\w+)\),\s*\}\)',
        r'localStorage.setItem("cf_\1", JSON.stringify(\2))', s)
    assert n == 4, f"expected 4 autosave fetches, replaced {n}"
    n_replacements += n
    s = s2

    # ---- corrections: local rules instead of the server ----
    s = sub(s, '''async function postCorrection(merchant, hint, naics, mix, confirm, allCats, basket, category) {
  const res = await fetch("/api/v2/correct", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({merchant, category_hint: hint, naics, mix, basket, category,
                          all_categories: !!allCats, confirm: !!confirm}),
  });
  if (!res.ok) { alert("Correction failed"); return false; }
  const upd = await res.json();
  for (const tx of DATA.transactions) {
    if (tx.merchant !== merchant) continue;
    if (allCats ? (upd.locked_hints || []).includes(tx.category_hint) : tx.category_hint !== hint) continue;
    Object.assign(tx, upd.assignment, {source: confirm ? "confirmed" : "manual", confidence: 1});
  }
  renderAll();
  return true;
}''', '''async function postCorrection(merchant, hint, naics, mix, confirm, allCats, basket, category) {
  try {
    applyCorrectionW(merchant, hint, {naics, mix, basket, category,
      source: confirm ? "confirmed" : "manual", all_categories: !!allCats});
  } catch (e) { alert("Correction failed: " + (e.message || e)); return false; }
  renderAll();
  return true;
}''')

    # ---- upload: client-side pipeline + Worker classification ----
    s = sub(s, '''$("csvfile").onchange = async e => {
  const f = e.target.files[0];
  if (!f) return;
  $("upload-msg").textContent = "Classifying (vector + LLM) — can take a minute or two…";
  const res = await fetch("/api/v2/upload", {method: "POST", headers: {"Content-Type": "text/csv"}, body: await f.text()});
  if (res.ok) location.reload();
  else { const d = await res.json().catch(() => ({})); $("upload-msg").textContent = "Failed: " + (d.error || res.status); }
  e.target.value = "";
};''', '''$("csvfile").onchange = async e => {
  const f = e.target.files[0];
  if (!f) return;
  $("upload-msg").textContent = "Classifying — usually a few seconds…";
  try {
    await webUpload(await f.text());
    location.reload();
  } catch (err) {
    $("upload-msg").textContent = "Failed: " + (err.message || err);
  }
  e.target.value = "";
};''')

    # ---- reset: clear this browser's state ----
    s = sub(s, '''$("reset").onclick = async () => {
  if (!confirm("Delete ALL transactions, EVERY merchant rule (including your corrections), and all Travel/Home/Food/Offsets inputs? This cannot be undone.")) return;
  const res = await fetch("/api/v2/reset", {method: "POST"});
  if (res.ok) { try { localStorage.removeItem("cf_splash"); localStorage.removeItem("cf_elec_hourly"); } catch (e) {} location.reload(); }
  else alert("Reset failed");
};''', '''$("reset").onclick = () => {
  if (!confirm("Delete ALL transactions, EVERY merchant rule (including your corrections), and all Travel/Home/Food/Offsets inputs stored in this browser? This cannot be undone.")) return;
  for (const k of ["cf_data", "cf_rules", "cf_travel", "cf_home", "cf_food", "cf_offsets", "cf_splash", "cf_elec_hourly"]) localStorage.removeItem(k);
  location.reload();
};''')

    # ---- copy tweaks: browser persistence + privacy note ----
    s = sub(s, "Corrections apply to all transactions of the same merchant and persist server-side.",
            "Corrections apply to all transactions of the same merchant and are saved in this browser.")
    s = sub(s, "transfers, card payments, and income are detected and excluded automatically.",
            "transfers, card payments, and income are detected and excluded automatically. "
            "Your file never leaves this browser — only merchant names and category hints are "
            "sent (to a shared classification cache); amounts, dates, and accounts stay local.")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(s)
    js = re.search(r'"use strict";(.*?)</script>', s, re.S).group(1)
    for a, b in (("{", "}"), ("(", ")"), ("[", "]")):
        assert js.count(a) == js.count(b), f"unbalanced {a}{b}: {js.count(a)} vs {js.count(b)}"
    print(f"Built {OUT}: {len(s):,} bytes, {n_replacements} transformations, JS balanced")


def js_regex(py_pattern) -> str:
    """Convert a compiled Python regex to a JS literal (flags i where set)."""
    flags = "i" if py_pattern.flags & re.I else ""
    return f"/{py_pattern.pattern.replace('/', chr(92) + '/')}/{flags}"


# ---------------------------------------------------------------------------
# The web runtime: client-side port of src/cf/classify.py's expansion,
# correction, and upload pipeline. Placeholders (__X__) are filled in build().
# ---------------------------------------------------------------------------
WEB_RUNTIME = r'''
// ---- web runtime (generated by scripts/build_web.py — do not edit web/index.html) ----
const BY_CODE = __BY_CODE__;
const WEB_CATEGORIES = __CATEGORIES__;
const WEB_META = {dataset: __DATASET__, deflators: __DEFLATORS__};
const WEB_RULES = JSON.parse(localStorage.getItem("cf_rules") || "{}");
const NP_HINTS = new Set(__NP_HINTS__);
const NP_MERCHANT = __NP_MERCHANT_RE__;
const M_PREFIX = __PREFIX_RE__;
const M_STORE = /\s*#?\d{3,}\s*$/;
const HEALTH_RE = __HEALTH_RE__;
const HEALTH_MIX = __HEALTH_MIX__;
const HINT_RULES = __HINT_RULES__;
const NAICS_REMAP = __REMAP__;

function deflatorFor(d) {
  const y = parseInt((d || "").slice(0, 4), 10);
  if (!y) return 1;
  return WEB_META.deflators[Math.min(Math.max(y, 2022), 2026)] || 1;
}

function normalizeMerchant(name) {
  let s = (name || "").trim().replace(M_PREFIX, "").replace(M_STORE, "").replace(/\s{2,}/g, " ");
  s = s.replace(/^[\s\-*·]+|[\s\-*·]+$/g, "");
  return s || (name || "").trim();
}

function saveWebData() { localStorage.setItem("cf_data", JSON.stringify(DATA)); }
function saveWebRules() { localStorage.setItem("cf_rules", JSON.stringify(WEB_RULES)); }

function resolveBasketW(host, partsIn) {
  const usable = partsIn.filter(p => p.naics !== host && BY_CODE[p.naics]);
  const tw = usable.reduce((a, p) => a + +p.weight, 0) || 1;
  const parts = usable.map(p => {
    const e = BY_CODE[p.naics];
    return {naics: e.code, title: e.title, factor: e.factor,
            factor_production: e.factor_production ?? e.factor,
            factor_margins: e.factor_margins ?? 0, weight: +p.weight / tw};
  });
  const factor = parts.length ? +parts.reduce((a, p) => a + p.factor * p.weight, 0).toFixed(4) : null;
  return [parts, factor];
}

function expandAssignmentW(a) {
  if (a.mix && a.mix.length) {
    const tw = a.mix.reduce((x, p) => x + +p.weight, 0) || 1;
    const parts = [];
    for (const p of a.mix) {
      const e = BY_CODE[p.naics];
      if (!e) continue;
      parts.push({naics: e.code, title: e.title, factor: e.factor, category: e.category, weight: +p.weight / tw});
    }
    if (parts.length) {
      const factor = parts.reduce((x, p) => x + p.factor * p.weight, 0);
      const label = "Split: " + parts.map(p => `${Math.round(p.weight * 100)}% ${p.title}`).join(", ");
      return {naics: null, naics_title: label, factor: +factor.toFixed(4), category: "mixed",
              mix: parts, basket: null, margin_warn: false, unmapped: false};
    }
  }
  if (a.naics == null) {
    return {naics: null, naics_title: "Non-purchase (transfer/income/payment)", factor: null,
            category: "ignored", mix: null, basket: null, margin_warn: false, unmapped: false};
  }
  const code = NAICS_REMAP[a.naics] || a.naics;
  const entry = BY_CODE[code];
  if (!entry) {
    return {naics: a.naics, naics_title: `${a.naics} — no EPA factor, assign an industry`, factor: null,
            category: "excluded", mix: null, basket: null, margin_warn: false, unmapped: true};
  }
  if (a.basket && a.basket.length) {
    const [parts, factor] = resolveBasketW(code, a.basket);
    const prod = parts.length ? +parts.reduce((x, p) => x + p.factor_production * p.weight, 0).toFixed(4) : null;
    const marg = parts.length ? +parts.reduce((x, p) => x + p.factor_margins * p.weight, 0).toFixed(4) : null;
    return {naics: code, naics_title: entry.title + " · custom basket", factor,
            category: WEB_CATEGORIES[a.cat] ? a.cat : entry.category,
            mix: null, factor_production: prod, factor_margins: marg,
            naics2017: entry.naics2017, useeio: entry.useeio, basket: parts, basket_custom: true,
            margin_warn: false, unmapped: false};
  }
  let db = null;
  if (entry.basket) [db] = resolveBasketW(code, entry.basket);
  const out = {naics: code, naics_title: entry.title, factor: entry.factor, category: entry.category,
               factor_production: entry.factor_production, factor_margins: entry.factor_margins,
               naics2017: entry.naics2017, useeio: entry.useeio, factor_note: entry.factor_note || null,
               mix: null, basket: db, unmapped: false};
  out.margin_warn = !!(entry.category.startsWith("goods_") && !db &&
                       !(entry.factor_margins > 0) && !code.startsWith("4595"));
  if (a.cat && WEB_CATEGORIES[a.cat]) out.category = a.cat;   // user rollup override
  return out;
}

function applyCorrectionW(merchant, hint, {naics = null, mix = null, basket = null, category = null,
                                           source = "manual", all_categories = false} = {}) {
  const key = all_categories ? `${merchant}|*` : `${merchant}|${hint}`;
  let assignment;
  if (basket && basket.length) {
    if (!naics) throw new Error("basket requires the merchant's NAICS code");
    for (const p of basket) if (p.naics !== naics && !BY_CODE[p.naics]) throw new Error(`unknown NAICS ${p.naics}`);
    assignment = {naics, basket: basket.map(p => ({naics: p.naics, weight: +p.weight})), confidence: 1, source};
  } else if (mix && mix.length) {
    for (const p of mix) if (!BY_CODE[p.naics]) throw new Error(`unknown NAICS ${p.naics}`);
    assignment = {naics: null, mix: mix.map(p => ({naics: p.naics, weight: +p.weight})), confidence: 1, source};
  } else {
    if (naics != null && !BY_CODE[naics] && !NAICS_REMAP[naics]) throw new Error(`unknown NAICS ${naics}`);
    assignment = {naics, confidence: 1, source};
  }
  if (category != null) {
    if (!WEB_CATEGORIES[category]) throw new Error(`unknown category ${category}`);
    assignment.cat = category;
  }
  WEB_RULES[key] = assignment;
  const locked_hints = [];
  if (all_categories) {
    for (const k of Object.keys(WEB_RULES)) {
      if (k.startsWith(merchant + "|") && k !== key) {
        if (["manual", "confirmed"].includes(WEB_RULES[k].source)) locked_hints.push(k.slice(merchant.length + 1));
        else delete WEB_RULES[k];
      }
    }
  }
  saveWebRules();
  const fields = expandAssignmentW(assignment);
  if (fields.naics == null && !fields.mix) fields.naics_title = "Non-purchase (manual)";
  fields.scope = all_categories ? "merchant" : null;
  if (DATA) {
    for (const t of DATA.transactions) {
      if (t.merchant !== merchant) continue;
      if (all_categories) { if (locked_hints.includes(t.category_hint)) continue; }
      else if (t.category_hint !== hint) continue;
      Object.assign(t, fields, {source, confidence: 1});
    }
    saveWebData();
  }
  return {assignment: fields, locked_hints};
}

// Minimal CSV parser (handles quoted fields, embedded commas/newlines).
function parseCsvRows(text) {
  const rows = [];
  let row = [], field = "", inQ = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQ) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else inQ = false; }
      else field += c;
    } else if (c === '"') inQ = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else field += c;
  }
  row.push(field);
  if (row.length > 1 || row[0] !== "") rows.push(row);
  return rows;
}

async function webUpload(text) {
  const rows = parseCsvRows(text.replace(/^﻿/, ""));
  if (rows.length < 2) throw new Error("Empty CSV");
  const headers = rows[0].map(h => h.toLowerCase().trim());
  const col = names => { for (const n of names) { const i = headers.indexOf(n); if (i >= 0) return i; } return -1; };
  const mI = col(["merchant", "description", "name", "payee"]), aI = col(["amount"]);
  if (mI < 0 || aI < 0) throw new Error("CSV needs at least a Merchant (or Description/Payee) and an Amount column");
  const dI = col(["date", "transaction date", "posted date"]), cI = col(["category"]);

  const txns = [];
  for (const r of rows.slice(1)) {
    const rawA = String(r[aI] ?? "").replace(/[$,]/g, "").trim();
    if (!rawA || !r[mI]) continue;
    const amount = parseFloat(rawA);
    if (!isFinite(amount) || amount === 0) continue;
    txns.push({date: dI >= 0 ? (r[dI] || "").trim() : "", merchant_raw: r[mI].trim(),
               merchant: normalizeMerchant(r[mI]), category_hint: cI >= 0 ? (r[cI] || "").trim() : "", amount});
  }
  if (!txns.length) throw new Error("No parseable transactions found");
  const dates = txns.map(t => t.date).filter(Boolean).sort();
  let months = 1;
  if (dates.length) {
    const days = (new Date(dates[dates.length - 1]) - new Date(dates[0])) / 86400000 + 1;
    months = Math.max(1, Math.round(days / 30.44));
  }

  // dedupe to merchant|hint; resolve: local rules -> tier-0 rules -> shared cache -> LLM
  const merchants = new Map();
  for (const t of txns) {
    const k = `${t.merchant}|${t.category_hint}`;
    if (!merchants.has(k)) merchants.set(k, {merchant: t.merchant, hint: t.category_hint});
  }
  const assignments = {};
  const toResolve = [];
  for (const [k, m] of merchants) {
    const local = WEB_RULES[k] || WEB_RULES[`${m.merchant}|*`];
    const locked = local && ["manual", "confirmed"].includes(local.source);
    if (!locked && HINT_RULES[m.hint.toLowerCase()]) {
      assignments[k] = {...HINT_RULES[m.hint.toLowerCase()]};
      continue;
    }
    if (!locked && HEALTH_RE.test(m.merchant)) {
      assignments[k] = {naics: null, mix: HEALTH_MIX, confidence: 1, source: "rule"};
      continue;
    }
    if (local) { assignments[k] = local; continue; }
    const hint = m.hint.toLowerCase();
    if ((hint && NP_HINTS.has(hint)) || (!hint && NP_MERCHANT.test(m.merchant))) {
      assignments[k] = {naics: null, confidence: 1, source: "rule"};
      continue;
    }
    toResolve.push(m);
  }
  for (let i = 0; i < toResolve.length; i += 500) {
    const chunk = toResolve.slice(i, i + 500);
    const res = await fetch("/api/cache/lookup", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({keys: chunk})});
    if (!res.ok) throw new Error(`cache lookup failed (${res.status})`);
    const {found} = await res.json();
    Object.assign(assignments, found);
  }
  const unknown = toResolve.filter(m => !assignments[`${m.merchant}|${m.hint}`]);
  for (let i = 0; i < unknown.length; i += 120) {
    const chunk = unknown.slice(i, i + 120);
    $("upload-msg").textContent = `Classifying new merchants ${i + 1}–${Math.min(i + 120, unknown.length)} of ${unknown.length}…`;
    const res = await fetch("/api/classify", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({merchants: chunk})});
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.error || `classification failed (${res.status})`);
    }
    const {assignments: got} = await res.json();
    Object.assign(assignments, got);
  }

  const out = txns.map(t => {
    const a = assignments[`${t.merchant}|${t.category_hint}`] || {naics: null, confidence: 0, source: "rule"};
    return {...t, ...expandAssignmentW(a), deflator: deflatorFor(t.date), scope: null,
            confidence: a.confidence ?? null, source: a.source || "llm"};
  });
  const result = {meta: {start: dates[0] || null, end: dates[dates.length - 1] || null,
                         months, count: txns.length, dataset: WEB_META.dataset, deflators: WEB_META.deflators},
                  categories: WEB_CATEGORIES, transactions: out};
  localStorage.setItem("cf_data", JSON.stringify(result));
}
// ---- end web runtime ----
'''


if __name__ == "__main__":
    build()
