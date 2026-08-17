// Carbon-footprint API worker: shared merchant->NAICS cache + rate-limited
// LLM classification proxy. All user transaction data stays in the browser;
// the only things that cross the wire are normalized merchant names and
// category hints (no amounts, no dates, no accounts).

const JSON_HEADERS = { "Content-Type": "application/json" };

function cors(env, extra = {}) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    ...extra,
  };
}

const cacheKey = (merchant, hint) => `m:${merchant.toLowerCase()}|${(hint || "").toLowerCase()}`;

// Same tier-0 idea as the local pipeline: money movement never reaches the LLM.
const NON_PURCHASE_HINTS = new Set([
  "transfer", "credit card payment", "paychecks", "paycheck", "bonus", "other income",
  "interest", "tax refund", "credit card rewards", "cash & atm", "check",
  "taxes", "mortgage", "heloc", "loan repayment", "balance adjustments",
]);

async function ipAllowed(env, ip) {
  const key = `rl:${ip}:${new Date().toISOString().slice(0, 10)}`;
  const n = parseInt((await env.MERCHANT_CACHE.get(key)) || "0", 10);
  if (n >= parseInt(env.DAILY_IP_LIMIT, 10)) return false;
  await env.MERCHANT_CACHE.put(key, String(n + 1), { expirationTtl: 90000 });
  return true;
}

// ---- vector candidate tier: precomputed index embeddings (web/data/
// naics_vectors.json, built by scripts/build_worker_vectors.py with the same
// Workers AI model used for queries) + brute-force cosine in the worker. ----
let VEC = null;   // {dim, codes, titles, mat: Float32Array} | false when absent
async function loadVectors(env) {
  if (VEC) return VEC;
  try {
    const res = await env.ASSETS.fetch("https://assets.local/data/naics_vectors.json");
    if (!res.ok) throw new Error(String(res.status));
    const d = await res.json();
    const bin = Uint8Array.from(atob(d.vec_b64), (c) => c.charCodeAt(0));
    VEC = { dim: d.dim, codes: d.codes, titles: d.titles, mat: new Float32Array(bin.buffer) };
  } catch (e) {
    console.log("vectors_unavailable", { error: String(e.message || e).slice(0, 100) });
    return null;   // don't cache the failure — retry on the next request
  }
  return VEC;
}

async function topCandidatesRaw(env, queries, k = 10) {
  const vec = await loadVectors(env);
  if (!vec) return queries.map(() => null);
  const out = await env.AI.run("@cf/baai/bge-small-en-v1.5", { text: queries });
  return out.data.map((q) => {
    let norm = Math.sqrt(q.reduce((a, x) => a + x * x, 0)) || 1;
    const scores = new Array(vec.codes.length);
    for (let r = 0; r < vec.codes.length; r++) {
      let dot = 0;
      const off = r * vec.dim;
      for (let j = 0; j < vec.dim; j++) dot += q[j] * vec.mat[off + j];
      scores[r] = dot / norm;
    }
    return [...scores.keys()].sort((a, b) => scores[b] - scores[a]).slice(0, k)
      .map((r) => ({ code: vec.codes[r], title: vec.titles[r], score: Math.round(scores[r] * 1e4) / 1e4 }));
  });
}

async function topCandidates(env, queries, k = 10) {
  const raw = await topCandidatesRaw(env, queries, k);
  return raw.map((cs) => cs ? cs.map((c) => `${c.code}=${c.title}`).join("; ") : null);
}

// Batch prompt mirroring src/cf/classify.py's rules ("map what was bought").
function buildPrompt(items) {
  const lines = items.map((it, i) =>
    `${i}. "${it.merchant}"${it.hint ? ` (statement category: ${it.hint})` : ""}` +
    (it.cands ? `\n   Candidates: ${it.cands}` : ""));
  return (
    "Assign each consumer credit-card merchant below to the single best NAICS code " +
    "for WHAT WAS BOUGHT, not where it was bought. Use 2022-revision codes only " +
    "(apparel mfg is 315250 not 315220; clothing stores 458110 not 448140; streaming 516210).\n" +
    "Rules:\n" +
    "- Goods from a single-category brand: the commodity/manufacturing code (sectors 11-33).\n" +
    "- Multi-category stores: the retail store code — online marketplaces (Amazon, Etsy, eBay) " +
    "459999; department stores 455110; warehouse clubs (Costco) 455211; home centers 444110; " +
    "pharmacies 456110.\n" +
    "- Services: the service's own code (51-81). Restaurants/food/travel/utilities keep their usual codes.\n" +
    "- Insurance premiums are real purchases, never null: auto/home/renters 524126, " +
    "life 524113. Health insurance premiums fund healthcare delivery: 622110, not 524114.\n" +
    "- Digital subscriptions: 513210 software/SaaS, 516210 streaming, 518210 cloud.\n" +
    "- Utility bills: electricity 221122, natural gas 221210, water 221310, sewer 221320 " +
    "(PG&E and other combined utilities: 221122).\n" +
    "- Pure money movement (transfers, card payments, income): null. BUT a payment app " +
    "(Zelle, Venmo, PayPal, Check) WITH a real statement category is just how a purchase was " +
    "paid — classify by the category: Zelle + Child Care is 624410, Venmo + Haircut is 812112.\n" +
    "The statement category says what was bought (strong signal); the merchant name says " +
    "where or how it was paid (weak signal). When they conflict, trust the category.\n" +
    "Where a merchant has a Candidates list, prefer a code from it when one fits; " +
    "otherwise use your best NAICS-6 from anywhere.\n\n" +
    lines.join("\n") +
    "\n\nFor each numbered item return {i, naics, confidence} — naics null for " +
    "non-purchases, confidence 0-1."
  );
}

// Structured output schema: the decoder can only produce this shape.
const CLASSIFY_SCHEMA = {
  type: "object",
  properties: {
    results: {
      type: "array",
      items: {
        type: "object",
        properties: {
          i: { type: "integer" },
          naics: { type: ["string", "null"] },
          confidence: { type: "number" },
        },
        required: ["i", "naics", "confidence"],
        additionalProperties: false,
      },
    },
  },
  required: ["results"],
  additionalProperties: false,
};

async function classifyWithLLM(env, items) {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": env.ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: env.CLASSIFY_MODEL,
      max_tokens: 4000,
      temperature: 0,
      output_config: { format: { type: "json_schema", schema: CLASSIFY_SCHEMA } },
      messages: [{ role: "user", content: buildPrompt(items) }],
    }),
  });
  if (!res.ok) throw new Error(`anthropic ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const data = await res.json();
  const text = data.content.find((b) => b.type === "text")?.text || "";
  return JSON.parse(text).results;   // schema-guaranteed valid
}

async function classifyWithRetry(env, items) {
  try { return await classifyWithLLM(env, items); }
  catch (e) {
    console.log("classify_retry", { error: String(e.message || e).slice(0, 120) });
    return await classifyWithLLM(env, items);   // one retry, then surface the error
  }
}

// ---- interval-CSV column mapper (advanced electricity upload) ----
// Input: the FIRST ~25 rows of a utility interval export (never the full
// file). Output: a column mapping the browser applies locally. Deterministic
// unit/interval math and quality gates stay client-side — the LLM only names
// columns, units and date order.
const PARSE_HOURLY_SCHEMA = {
  type: "object",
  properties: {
    ok: { type: "boolean" },
    reason: { type: ["string", "null"] },
    header_rows: { type: "integer" },
    ts_col: { type: ["integer", "null"] },
    date_col: { type: ["integer", "null"] },
    time_col: { type: ["integer", "null"] },
    usage_col: { type: ["integer", "null"] },
    usage_unit: { type: "string", enum: ["kwh", "wh", "kw", "w"] },
    date_order: { type: "string", enum: ["mdy", "dmy", "ymd"] },
  },
  required: ["ok", "reason", "header_rows", "ts_col", "date_col", "time_col",
             "usage_col", "usage_unit", "date_order"],
  additionalProperties: false,
};

function buildParseHourlyPrompt(sample) {
  return (
    "Below are the first rows of a CSV a household downloaded from their electric " +
    "utility, expected to be interval (hourly or 15-minute) electricity CONSUMPTION.\n" +
    "Identify, using 0-based comma-split column indexes:\n" +
    "- ts_col: a combined date+time column (else null and use date_col/time_col)\n" +
    "- date_col / time_col: separate date and time-of-day columns (null if ts_col)\n" +
    "- usage_col: the energy/power reading per interval. NOT cost, NOT temperature, " +
    "NOT meter register/cumulative totals (a register only ever increases; per-interval " +
    "usage fluctuates).\n" +
    "- usage_unit: kwh, wh, kw or w — from the header text or magnitudes (a home draws " +
    "~0.2-3 kW; hourly kWh ~0.1-5; a 15-min kWh ~0.02-1.5).\n" +
    "- header_rows: how many leading rows are headers/preamble before data starts.\n" +
    "- date_order: mdy, dmy or ymd for slash-separated dates (judge from values >12 or context).\n" +
    "If this is not interval electricity usage data (billing summary, gas therms, water), " +
    "set ok=false with a one-sentence reason.\n\n" + sample
  );
}

export default {
  async fetch(request, env) {
    try {
      return await handle(request, env);
    } catch (e) {
      console.error("unhandled", { path: new URL(request.url).pathname, error: String(e.message || e).slice(0, 300) });
      return new Response(JSON.stringify({ error: String(e.message || e).slice(0, 300) }),
        { status: 500, headers: { ...JSON_HEADERS, ...cors(env) } });
    }
  },
};

async function handle(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(env) });

    // POST /api/cache/lookup {keys: [{merchant, hint}]} -> {found: {key: assignment}}
    if (url.pathname === "/api/cache/lookup" && request.method === "POST") {
      const { keys = [] } = await request.json();
      const found = {};
      await Promise.all(keys.slice(0, 500).map(async (k) => {
        const v = await env.MERCHANT_CACHE.get(cacheKey(k.merchant, k.hint));
        if (v) found[`${k.merchant}|${k.hint || ""}`] = JSON.parse(v);
      }));
      console.log("cache_lookup", { asked: keys.length, found: Object.keys(found).length });
      return new Response(JSON.stringify({ found }), { headers: { ...JSON_HEADERS, ...cors(env) } });
    }

    // POST /api/candidates {merchant, hint} -> {candidates: [{code,title,score}]}
    // Vector top-10 for the advanced NAICS search. Cheap (one Workers AI embed)
    // but still capped per IP under its own generous bucket.
    if (url.pathname === "/api/candidates" && request.method === "POST") {
      const ip = request.headers.get("CF-Connecting-IP") || "unknown";
      const key = `rlc:${ip}:${new Date().toISOString().slice(0, 10)}`;
      const n = parseInt((await env.MERCHANT_CACHE.get(key)) || "0", 10);
      if (n >= 300) {
        return new Response(JSON.stringify({ error: "daily limit reached" }),
          { status: 429, headers: { ...JSON_HEADERS, ...cors(env) } });
      }
      await env.MERCHANT_CACHE.put(key, String(n + 1), { expirationTtl: 90000 });
      const { merchant = "", hint = "" } = await request.json();
      const q = hint ? `${merchant} · ${hint}` : merchant;
      const [cands] = await topCandidatesRaw(env, [String(q).slice(0, 200)]);
      return new Response(JSON.stringify({ candidates: cands || [] }),
        { headers: { ...JSON_HEADERS, ...cors(env) } });
    }

    // POST /api/parse-hourly {sample} -> column mapping (see PARSE_HOURLY_SCHEMA)
    if (url.pathname === "/api/parse-hourly" && request.method === "POST") {
      if (!env.ANTHROPIC_API_KEY) {
        return new Response(JSON.stringify({ error: "ANTHROPIC_API_KEY secret not configured" }),
          { status: 503, headers: { ...JSON_HEADERS, ...cors(env) } });
      }
      const ip = request.headers.get("CF-Connecting-IP") || "unknown";
      if (!(await ipAllowed(env, ip))) {
        return new Response(JSON.stringify({ error: "daily limit reached" }),
          { status: 429, headers: { ...JSON_HEADERS, ...cors(env) } });
      }
      const { sample = "" } = await request.json();
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "x-api-key": env.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                   "content-type": "application/json" },
        body: JSON.stringify({
          model: env.CLASSIFY_MODEL,
          max_tokens: 500,
          temperature: 0,
          output_config: { format: { type: "json_schema", schema: PARSE_HOURLY_SCHEMA } },
          messages: [{ role: "user", content: buildParseHourlyPrompt(String(sample).slice(0, 8000)) }],
        }),
      });
      if (!res.ok) {
        console.log("parse_hourly_err", { status: res.status });
        return new Response(JSON.stringify({ error: "mapper unavailable" }),
          { status: 502, headers: { ...JSON_HEADERS, ...cors(env) } });
      }
      const data = await res.json();
      const text = data.content.find((b) => b.type === "text")?.text || "{}";
      console.log("parse_hourly", { ok: JSON.parse(text).ok });
      return new Response(text, { headers: { ...JSON_HEADERS, ...cors(env) } });
    }

    // POST /api/classify {merchants: [{merchant, hint}]} -> {assignments: {key: {naics, confidence, source}}}
    if (url.pathname === "/api/classify" && request.method === "POST") {
      if (!env.ANTHROPIC_API_KEY) {
        return new Response(JSON.stringify({ error: "ANTHROPIC_API_KEY secret not configured" }),
          { status: 503, headers: { ...JSON_HEADERS, ...cors(env) } });
      }
      const ip = request.headers.get("CF-Connecting-IP") || "unknown";
      if (!(await ipAllowed(env, ip))) {
        console.log("rate_limited", {});
        return new Response(JSON.stringify({ error: "daily limit reached" }),
          { status: 429, headers: { ...JSON_HEADERS, ...cors(env) } });
      }
      const t0 = Date.now();
      const { merchants = [] } = await request.json();
      const assignments = {};
      const toClassify = [];
      for (const m of merchants.slice(0, 120)) {
        const key = `${m.merchant}|${m.hint || ""}`;
        if ((m.hint || "").toLowerCase() && NON_PURCHASE_HINTS.has(m.hint.toLowerCase())) {
          assignments[key] = { naics: null, confidence: 1, source: "rule" };
          continue;
        }
        const hit = await env.MERCHANT_CACHE.get(cacheKey(m.merchant, m.hint));
        if (hit) assignments[key] = JSON.parse(hit);
        else toClassify.push(m);
      }
      for (let i = 0; i < toClassify.length; i += 40) {
        const batch = toClassify.slice(i, i + 40);
        try {
          const cands = await topCandidates(env, batch.map((m) => m.hint ? `${m.merchant} · ${m.hint}` : m.merchant));
          batch.forEach((m, j) => { m.cands = cands[j]; });
        } catch (e) {
          console.log("candidates_failed", { error: String(e.message || e).slice(0, 100) });
        }
        const out = await classifyWithRetry(env, batch);
        for (const r of out) {
          const m = batch[r.i];
          if (!m) continue;
          const a = { naics: r.naics ? String(r.naics) : null,
                      confidence: +r.confidence || 0.5, source: "llm" };
          assignments[`${m.merchant}|${m.hint || ""}`] = a;
          await env.MERCHANT_CACHE.put(cacheKey(m.merchant, m.hint), JSON.stringify(a));
        }
      }
      console.log("classify", { asked: merchants.length, rule: Object.values(assignments).filter(a => a.source === "rule").length,
        llm_batches: Math.ceil(toClassify.length / 40), llm_merchants: toClassify.length,
        with_candidates: toClassify.filter(m => m.cands).length, ms: Date.now() - t0 });
      return new Response(JSON.stringify({ assignments }), { headers: { ...JSON_HEADERS, ...cors(env) } });
    }

    return new Response(JSON.stringify({ error: "not found" }),
      { status: 404, headers: { ...JSON_HEADERS, ...cors(env) } });
}
