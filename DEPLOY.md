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
   `web/` app via its `[assets]` block (uncomment in wrangler.toml once
   `web/` lands), same origin as the API.

## Cost expectations

- Cloudflare: free tier covers ~100k requests/day; KV free tier far exceeds
  a merchant cache's needs. Paid plan ($5/mo) only if limits are ever hit.
- Anthropic: one upload with ~40 uncached merchants ≈ one Haiku batch
  (well under a cent). The shared cache makes repeat merchants free.

## Abuse controls in the Worker

- Per-IP daily classify limit (`DAILY_IP_LIMIT`, default 20).
- CORS locked to the Pages origin.
- Batch caps (120 merchants/request, 40 per LLM call).
- Consider adding Turnstile on the upload action if the public URL spreads.
