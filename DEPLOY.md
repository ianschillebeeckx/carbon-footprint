# Deploying to Cloudflare

Architecture: one git-connected **Worker** (`worker/`, build path `worker`)
serving the static frontend (all transaction data stays in the browser —
IndexedDB) and holding
the only two server-side jobs: the shared merchant→NAICS cache (KV) and a
rate-limited Anthropic classification proxy. No user financial data is ever
stored server-side; the Worker only sees normalized merchant names + category
hints.

## One-time setup

1. **Connect GitHub**: Cloudflare dashboard → Workers & Pages → Create →
   *Connect to Git* → authorize the `carbon-footprint` repo.
2. **Worker** (`worker/`):
   - Create the KV namespace: Storage & Databases → KV → create
     `MERCHANT_CACHE`; paste its id into `worker/wrangler.toml`.
   - Create the Worker from Git (root directory `worker/`).
   - Add the secret: Worker → Settings → Variables → `ANTHROPIC_API_KEY`
     (from console.anthropic.com — separate billing from a Claude
     subscription; ~$5 credit goes a long way on Haiku).
3. **Domain**: after the first green deploy, Worker -> Settings ->
   Domains & Routes -> add custom domain `carbon.outis.cc` (the zone is
   already in the account; TLS/DNS are automatic).
4. **Frontend**: no separate Pages project — the Worker serves the static
   `web/` app via its `[assets]` block, same origin as the API. `web/_headers`
   carries the CSP and cache headers and costs no Worker invocation.
5. **Analytics** (`/api/e` -> D1; see ANALYTICS.md). The binding ships
   commented out, because an unknown `database_id` fails the deploy and this
   Worker deploys on push — until it is uncommented, `/api/e` accepts events
   and drops them, which is a working site with analytics dark.
   - `npx wrangler d1 create cf_analytics`, then paste the id into
     `worker/wrangler.toml` and uncomment the `[[d1_databases]]` block.
   - `npx wrangler d1 execute cf_analytics --remote --file worker/schema.sql`
   - Add the secret `ANALYTICS_SALT` (any long random string). Without it the
     visitor hash still rotates daily but is guessable from IP + UA.
6. **Turnstile** (optional, bot check on upload): Cloudflare dashboard ->
   Turnstile -> add `carbon.outis.cc`. Put the **secret** in the Worker secret
   `TURNSTILE_SECRET` and the **sitekey** in `TURNSTILE_SITEKEY` at the top of
   `scripts/build_web.py`, then rebuild. The two halves are independent: with
   neither set nothing changes, with only the sitekey set a token is sent and
   ignored, with only the secret set every upload is rejected — so **set the
   sitekey and rebuild before setting the secret.**

## Page map

| URL | File | Built by |
|---|---|---|
| `/` | `web/index.html` | `build_methodology.build_landing()` |
| `/app` | `web/app.html` | `scripts/build_web.py` |
| `/methodology.html` | `web/methodology.html` | `build_methodology()` |
| `/naics.html` | `web/naics.html` | `build_naics()` |
| `/privacy.html` | `web/privacy.html` | `build_privacy()` |

All five are generated. Run `.venv/bin/python scripts/build_web.py` — it builds
the app and then calls the other four — and commit the results.

## Cost expectations

- Cloudflare: free tier covers ~100k requests/day; KV free tier far exceeds
  a merchant cache's needs. Paid plan ($5/mo) only if limits are ever hit.
- Anthropic: one upload with ~40 uncached merchants ≈ one Haiku batch
  (well under a cent). The shared cache makes repeat merchants free.

## Abuse controls in the Worker

- Per-IP daily classify limit (`DAILY_IP_LIMIT`, default 20) — bounds one
  visitor.
- Account-wide daily LLM ceiling (`DAILY_LLM_BUDGET`) — bounds the bill when
  many visitors, or one visitor behind many addresses, arrive at once. Past the
  cap `/api/classify` returns 429 and no LLM call is made.
- Cloudflare Turnstile on the classify call, once its two halves are configured
  (step 6 above).
- CORS locked to `ALLOWED_ORIGIN`.
- Batch caps (120 merchants/request, 40 per LLM call).
