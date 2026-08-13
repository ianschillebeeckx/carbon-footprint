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

// Batch prompt mirroring src/cf/classify.py's rules ("map what was bought").
function buildPrompt(items) {
  const lines = items.map((it, i) =>
    `${i}. "${it.merchant}"${it.hint ? ` (statement category: ${it.hint})` : ""}`);
  return (
    "Assign each consumer credit-card merchant below to the single best NAICS code " +
    "for WHAT WAS BOUGHT, not where it was bought. Use 2022-revision codes only " +
    "(apparel mfg is 315250 not 315220; clothing stores 458110 not 448140; streaming 516210).\n" +
    "Rules:\n" +
    "- Goods from a single-category brand: the commodity/manufacturing code (sectors 11-33).\n" +
    "- Multi-category stores (home centers, pharmacies, department stores, warehouse clubs, " +
    "online marketplaces): the retail store code.\n" +
    "- Services: the service's own code (51-81). Restaurants/food/travel/utilities keep their usual codes.\n" +
    "- Health insurance premiums fund healthcare delivery: 622110, not 524114.\n" +
    "- Digital subscriptions: 513210 software/SaaS, 516210 streaming, 518210 cloud.\n" +
    "- Pure money movement (transfers, card payments, income): null.\n\n" +
    lines.join("\n") +
    '\n\nReturn ONLY a JSON array, one object per item, in order: ' +
    '[{"i": 0, "naics": "722515", "confidence": 0.95}, ...] ' +
    "(naics null for non-purchases; confidence 0-1)."
  );
}

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
      messages: [{ role: "user", content: buildPrompt(items) }],
    }),
  });
  if (!res.ok) throw new Error(`anthropic ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const data = await res.json();
  const text = data.content.find((b) => b.type === "text")?.text || "";
  const m = text.match(/\[.*\]/s);
  if (!m) throw new Error("no JSON array in model output");
  return JSON.parse(m[0]);
}

export default {
  async fetch(request, env) {
    try {
      return await handle(request, env);
    } catch (e) {
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
      return new Response(JSON.stringify({ found }), { headers: { ...JSON_HEADERS, ...cors(env) } });
    }

    // POST /api/classify {merchants: [{merchant, hint}]} -> {assignments: {key: {naics, confidence, source}}}
    if (url.pathname === "/api/classify" && request.method === "POST") {
      if (!env.ANTHROPIC_API_KEY) {
        return new Response(JSON.stringify({ error: "ANTHROPIC_API_KEY secret not configured" }),
          { status: 503, headers: { ...JSON_HEADERS, ...cors(env) } });
      }
      const ip = request.headers.get("CF-Connecting-IP") || "unknown";
      if (!(await ipAllowed(env, ip))) {
        return new Response(JSON.stringify({ error: "daily limit reached" }),
          { status: 429, headers: { ...JSON_HEADERS, ...cors(env) } });
      }
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
        const out = await classifyWithLLM(env, batch);
        for (const r of out) {
          const m = batch[r.i];
          if (!m) continue;
          const a = { naics: r.naics ? String(r.naics) : null,
                      confidence: +r.confidence || 0.5, source: "llm" };
          assignments[`${m.merchant}|${m.hint || ""}`] = a;
          await env.MERCHANT_CACHE.put(cacheKey(m.merchant, m.hint), JSON.stringify(a));
        }
      }
      return new Response(JSON.stringify({ assignments }), { headers: { ...JSON_HEADERS, ...cors(env) } });
    }

    return new Response(JSON.stringify({ error: "not found" }),
      { status: 404, headers: { ...JSON_HEADERS, ...cors(env) } });
}
