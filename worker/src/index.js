// Carbon-footprint API worker: shared merchant->NAICS cache + rate-limited
// LLM classification proxy. All user transaction data stays in the browser;
// the only things that cross the wire are normalized merchant names and
// category hints (no amounts, no dates, no accounts).

const JSON_HEADERS = { "Content-Type": "application/json" };

// ---------------------------------------------------------------------------
// First-party product analytics. Same origin, no cookie, no persistent
// identifier, no third-party request — so no consent banner, which matters
// because a banner is itself a funnel step that loses visitors.
//
// Two identities, deliberately. `uid` is a first-party identifier the browser
// keeps in localStorage for 180 days from creation; it is what makes retention
// measurable, and it is absent for anyone sending Global Privacy Control, using
// private browsing, or blocking storage. `visitor` is derived here from IP +
// user agent + the UTC date + a secret salt, with the IP discarded immediately;
// because the date is in the hash it cannot be joined across days, so it counts
// daily uniques for everyone including those without a uid.
//
// Use uid for retention and cohorts; use visitor for daily reach. Neither is
// ever derived from anything a person typed.
// ---------------------------------------------------------------------------

const BOT_RE = /bot|crawl|spider|slurp|headless|preview|scan|monitor|curl|wget|python-requests|facebookexternalhit|bingpreview|lighthouse/i;
const MAX_EVENTS = 50;      // per request; the client batches at 40
const MAX_PROPS = 12;       // per event

async function visitorHash(request, env, day) {
  const ip = request.headers.get("CF-Connecting-IP") || "";
  const ua = request.headers.get("User-Agent") || "";
  const salt = env.ANALYTICS_SALT || "unsalted";
  const buf = new TextEncoder().encode(`${ip}|${ua}|${day}|${salt}`);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", buf));
  return [...digest.slice(0, 8)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Fixed columns are the dimensions every event carries; everything else lands
// in a JSON blob queried with json_extract(props,'$.tab'). Keeps the schema
// stable while letting new events add fields without a migration.
function toRow(e, base) {
  const props = {};
  let n = 0;
  for (const k of Object.keys(e)) {
    if (k === "n" || k === "q") continue;
    if (n++ >= MAX_PROPS) break;
    const v = e[k];
    props[String(k).slice(0, 24)] =
      typeof v === "number" ? v : String(v == null ? "" : v).slice(0, 120);
  }
  return { ...base, name: String(e.n || "").slice(0, 40), seq: +e.q || 0,
           props: JSON.stringify(props) };
}

// Two sinks, both optional bindings: D1 is the primary (real SQL, no sampling,
// indefinite retention) and Analytics Engine mirrors it when the binding
// exists. Adding [[analytics_engine_datasets]] to wrangler.toml is the whole
// migration if D1's row-write budget is ever the constraint.
async function writeEvents(env, rows) {
  if (env.DB) {
    const stmt = env.DB.prepare(
      "INSERT INTO events (ts,day,visitor,uid,session,seq,name,page,ref,country,vw,is_returning,props) " +
      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)");
    await env.DB.batch(rows.map((r) => stmt.bind(
      r.ts, r.day, r.visitor, r.uid, r.session, r.seq, r.name,
      r.page, r.ref, r.country, r.vw, r.is_returning, r.props)));
  }
  if (env.AE) {
    for (const r of rows) {
      env.AE.writeDataPoint({
        indexes: [r.visitor],
        blobs: [r.name, r.page, r.session, r.ref, r.country, r.props, r.uid],
        doubles: [r.vw, r.is_returning, r.seq],
      });
    }
  }
}

// Turnstile gate. Open when the secret isn't set, so the Worker keeps working
// before the keys exist and the frontend only renders the widget when it was
// given a sitekey — the control can be switched on without a lockstep deploy.
async function turnstileOk(env, request, token) {
  if (!env.TURNSTILE_SECRET) return true;
  if (!token) return false;
  const form = new FormData();
  form.append("secret", env.TURNSTILE_SECRET);
  form.append("response", String(token).slice(0, 2048));
  form.append("remoteip", request.headers.get("CF-Connecting-IP") || "");
  const res = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                          { method: "POST", body: form });
  const data = await res.json().catch(() => ({}));
  if (!data.success) console.log("turnstile_fail", { codes: data["error-codes"] });
  return !!data.success;
}

// Account-wide ceiling on LLM spend for the day. The per-IP limit bounds one
// visitor; this bounds the bill when many of them (or one of them behind many
// addresses) show up at once.
async function budgetOk(env, n) {
  const cap = parseInt(env.DAILY_LLM_BUDGET || "0", 10);
  if (!cap) return true;
  const key = `budget:${new Date().toISOString().slice(0, 10)}`;
  const used = parseInt((await env.MERCHANT_CACHE.get(key)) || "0", 10);
  if (used >= cap) { console.log("budget_exhausted", { used, cap }); return false; }
  await env.MERCHANT_CACHE.put(key, String(used + n), { expirationTtl: 90000 });
  return true;
}

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
  async fetch(request, env, ctx) {
    try {
      return await handle(request, env, ctx);
    } catch (e) {
      console.error("unhandled", { path: new URL(request.url).pathname, error: String(e.message || e).slice(0, 300) });
      return new Response(JSON.stringify({ error: String(e.message || e).slice(0, 300) }),
        { status: 500, headers: { ...JSON_HEADERS, ...cors(env) } });
    }
  },
};

async function handle(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(env) });

    // POST /api/e {s, p, r, u, v, e: [{n, t, q, ...props}]} -> 204
    // Always 204, even on garbage input: analytics must never surface an error
    // to the page, and a failed write is a lost row, not a broken app.
    if (url.pathname === "/api/e" && request.method === "POST") {
      const ok = new Response(null, { status: 204, headers: cors(env) });
      if (BOT_RE.test(request.headers.get("User-Agent") || "")) return ok;
      let body;
      try { body = await request.json(); } catch (e) { return ok; }
      const evs = Array.isArray(body && body.e) ? body.e.slice(0, MAX_EVENTS) : [];
      if (!evs.length) return ok;
      const now = Date.now();
      const day = new Date(now).toISOString().slice(0, 10);
      const base = {
        ts: now, day,
        visitor: await visitorHash(request, env, day),
        uid: String(body.i || "").slice(0, 64),
        session: String(body.s || "").slice(0, 32),
        page: String(body.p || "").slice(0, 24),
        ref: String(body.r || "").slice(0, 80),
        country: request.headers.get("CF-IPCountry") || "",
        vw: Math.min(+body.v || 0, 20000),
        is_returning: body.u ? 1 : 0,
      };
      const rows = evs.map((e) => toRow(e, base));
      ctx.waitUntil(writeEvents(env, rows).catch((e) =>
        console.log("analytics_write_failed", { error: String(e.message || e).slice(0, 120), n: rows.length })));
      return ok;
    }

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
      const { merchants = [], turnstile = null } = await request.json();
      if (!(await turnstileOk(env, request, turnstile))) {
        return new Response(JSON.stringify({ error: "verification failed — reload and try again" }),
          { status: 403, headers: { ...JSON_HEADERS, ...cors(env) } });
      }
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
      if (toClassify.length && !(await budgetOk(env, toClassify.length))) {
        return new Response(JSON.stringify({ error: "the shared classifier has hit today's budget — try again tomorrow" }),
          { status: 429, headers: { ...JSON_HEADERS, ...cors(env) } });
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
