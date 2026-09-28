# Product analytics

First-party, cookieless, same-origin. The browser posts batched events to
`/api/e`; the Worker adds a daily-rotating visitor hash and writes to D1.
Nothing a user typed is ever an argument to `track()`.

- Client: `scripts/analytics.py` (one tracker, injected into all five pages)
- Ingest + sinks: `worker/src/index.js` (`/api/e`, `writeEvents`)
- Table: `worker/schema.sql`
- What users are told: `site/privacy-template.html`

## Why not an off-the-shelf tool

The product promise is that your financial data stays on your device. A
third-party analytics script contradicts that. So the tracker is ours: same
origin, no third party, nothing shared with anyone.

## Two identities, for two different questions

| | `uid` | `visitor` |
|---|---|---|
| Where it lives | `localStorage.cf_uid`, client | derived per request, server |
| Lifetime | 180 days from creation, not renewed | one site day (US Pacific) |
| Answers | retention, cohorts, repeat visits | daily reach |
| Missing for | GPC, private browsing, blocked storage | nobody |

`uid` is a real first-party identifier, not a hash pretending otherwise. It was
chosen over stretching the visitor hash to a longer period because that hash
degrades badly as the window grows: Chrome ships a major version roughly every
four weeks and the version is in the UA string, so a month-long hash silently
re-identifies a large share of users; network switching fragments one person
into many; and NAT merges many people into one. Those errors are negligible
over a day and uncorrectable over a month, in both directions at once.

**Use `uid` for retention and `visitor` for reach.** They disagree by design —
`uid` undercounts (anyone opting out or clearing storage), `visitor` both over-
and under-counts at the margins. Neither is ever derived from anything typed.

Two escape hatches, both documented on the privacy page: Global Privacy Control
suppresses `uid` while aggregate counts continue through `visitor`, and
`localStorage.cf_no_analytics` stops the tracker entirely.

## Schema

`events`, one flat table — see `worker/schema.sql`.

| Column | Meaning |
|---|---|
| `ts` / `day` | Server receipt time (epoch ms, UTC); `day` is `YYYY-MM-DD` in **US Pacific**, not UTC — midnight UTC is 5pm Pacific and would split peak evening usage across two reporting days |
| `visitor` | `SHA-256(ip + ua + day + salt)`, 16 hex. Rotates daily. IP never stored |
| `uid` | First-party localStorage id, 180-day expiry. The retention key. Empty when suppressed |
| `session` | Per page load, generated in the tab's memory, never written to the device |
| `seq` | Event order within a session |
| `name` | Event name (below) |
| `page` | `landing` · `app` · `methodology` · `naics` · `privacy` |
| `ref` | Referrer **host** only |
| `country` | `CF-IPCountry` |
| `vw` | Viewport width px — the mobile/desktop cut |
| `is_returning` | 1 when this browser already held `cf_*` state (`returning` is a SQLite reserved word) |
| `props` | JSON; every event's own fields. `json_extract(props,'$.tab')` |

Every event also carries `props.t`: milliseconds since page load.

## Events

| Event | Page | Key props |
|---|---|---|
| `page_view` | all | `start` (the `?start=` the app was opened with), `has_data` |
| `cta_click` | landing | `route` = `quick`\|`full`, `where` = `hero`\|`routes` |
| `splash_view` | app | `at` = `first_run`\|`about` |
| `route_choose` | app | `route`, `at` = `landing`\|`splash` |
| `tab_view` | app | `tab`, `from`, `dwell` (seconds on the previous tab) |
| `input_edit` | app | `tab` — fires once per tab per session, on first actual edit |
| `upload_start` | app | `kb` |
| `upload_done` | app | `txns`, `merchants`, `cached`, `llm`, `months`, `ms` |
| `upload_fail` | app | `reason`, `ms` |
| `merchant_correct` | app | `kind` = `confirm`\|`split`\|`basket`\|`recode`, `scope` |
| `tip_open` | app | `tip`, `tab` |
| `doc_open` | app, landing | `doc`, `from` |
| `naics_search` | naics | `q`, `hits` |
| `session_end` | all | `dur`, `filled`, `n_filled`, `txns` |
| `error` | all | `msg`, `src`, `line` |

`session_end` is the one to trust. Because all state lives in `localStorage`,
the client computes the visitor's exact funnel position at session end and sends
it as one row — robust to dropped beacons and to events nobody thought to add.
Prefer it over reconstructing progress from a click stream.

**It can fire more than once per session**, on purpose: it goes out on every
tab-away, because firing once would lose everything the visitor did afterwards
and unload events are too unreliable to bet the funnel on. Later rows supersede
earlier ones, so every query below that aggregates `session_end` first reduces
to the highest `seq` per session:

```sql
WITH last_end AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY session ORDER BY seq DESC) AS rn
  FROM events WHERE name = 'session_end'
)
-- ... then: FROM last_end WHERE rn = 1
```

## Querying

```bash
npx wrangler d1 execute cf_analytics --remote --json \
  --command "SELECT ..." > out.json
```

**The acquisition funnel, by day.** Each step is a count of distinct sessions.

```sql
SELECT day,
  COUNT(DISTINCT CASE WHEN name='page_view'  AND page='landing' THEN session END) AS landed,
  COUNT(DISTINCT CASE WHEN name='cta_click'                     THEN session END) AS clicked_cta,
  COUNT(DISTINCT CASE WHEN name='page_view'  AND page='app'     THEN session END) AS opened_app,
  COUNT(DISTINCT CASE WHEN name='input_edit'                    THEN session END) AS started_input,
  COUNT(DISTINCT CASE WHEN name='tab_view' AND json_extract(props,'$.tab')='total'
                                                                THEN session END) AS saw_total,
  COUNT(DISTINCT CASE WHEN name='session_end'
                       AND json_extract(props,'$.n_filled') >= 2 THEN session END) AS activated
FROM events GROUP BY day ORDER BY day DESC;
```

(`activated` is safe without the CTE because it counts distinct sessions, not
rows — duplicate `session_end` rows collapse.)

Note `opened_app` and `landed` are different sessions — a page load is a
session, so the landing→app step is a *drop*, not a join. To follow one person
across that boundary, join on `visitor` within the same `day`.

**Does the quick route or the full route activate better?** This is the A/B
that already exists in the product, for free.

```sql
WITH last_end AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY session ORDER BY seq DESC) AS rn
  FROM events WHERE name = 'session_end'
)
SELECT json_extract(r.props,'$.route') AS route,
       COUNT(*)                                          AS sessions,
       ROUND(AVG(json_extract(e.props,'$.n_filled')), 2) AS avg_tabs_filled,
       ROUND(AVG(json_extract(e.props,'$.dur')))         AS avg_seconds,
       SUM(json_extract(e.props,'$.n_filled') >= 2)      AS activated
FROM events r
JOIN last_end e ON e.session = r.session AND e.rn = 1
WHERE r.name = 'route_choose'
GROUP BY route;
```

**Where the upload step actually breaks.** The highest-value query on the
page: a stranger who hits a wall here leaves rather than reporting it.

```sql
SELECT json_extract(props,'$.reason') AS reason, COUNT(*) AS n
FROM events WHERE name='upload_fail'
GROUP BY reason ORDER BY n DESC;
```

**Which tab loses people.** A short dwell followed by a switch away is
abandonment; a long dwell with no `input_edit` is confusion.

```sql
SELECT json_extract(props,'$.from') AS tab,
       COUNT(*)                                     AS exits,
       ROUND(AVG(json_extract(props,'$.dwell')), 1) AS avg_dwell_s
FROM events WHERE name='tab_view'
GROUP BY tab ORDER BY exits DESC;
```

**Mobile.** The app is a dense six-tab tool; if phone sessions activate far
below desktop, that is a product finding, not a metric.

```sql
WITH last_end AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY session ORDER BY seq DESC) AS rn
  FROM events WHERE name = 'session_end'
)
SELECT CASE WHEN vw < 700 THEN 'phone' WHEN vw < 1100 THEN 'tablet' ELSE 'desktop' END AS device,
       COUNT(*)                                        AS sessions,
       ROUND(AVG(json_extract(props,'$.n_filled')), 2) AS avg_tabs_filled
FROM last_end WHERE rn = 1
GROUP BY device;
```

**Errors, newest first.** Should be empty. It will not be.

```sql
SELECT day, json_extract(props,'$.msg') AS msg, json_extract(props,'$.src') AS src,
       json_extract(props,'$.line') AS line, COUNT(*) AS n
FROM events WHERE name='error'
GROUP BY msg, src, line ORDER BY day DESC, n DESC;
```

**How much review the classifier costs.** Corrections per session with an
upload is the tax the "complete picture" route charges.

```sql
SELECT COUNT(DISTINCT session) AS sessions_correcting,
       COUNT(*)                AS corrections,
       json_extract(props,'$.kind') AS kind
FROM events WHERE name='merchant_correct' GROUP BY kind;
```

**Retention — of the browsers first seen on day D, how many came back?**
This is the query the `uid` exists for, and the most direct evidence available
that the thing is worth building on.

```sql
WITH first_seen AS (
  SELECT uid, MIN(day) AS cohort FROM events WHERE uid != '' GROUP BY uid
),
activity AS (
  SELECT DISTINCT e.uid, f.cohort, e.day,
         CAST(julianday(e.day) - julianday(f.cohort) AS INTEGER) AS day_n
  FROM events e JOIN first_seen f ON f.uid = e.uid
  WHERE e.uid != ''
)
SELECT cohort,
       COUNT(DISTINCT CASE WHEN day_n = 0 THEN uid END) AS cohort_size,
       COUNT(DISTINCT CASE WHEN day_n BETWEEN 1 AND 7  THEN uid END) AS returned_d1_7,
       COUNT(DISTINCT CASE WHEN day_n BETWEEN 8 AND 30 THEN uid END) AS returned_d8_30
FROM activity GROUP BY cohort ORDER BY cohort DESC;
```

**Do people who return actually get further?** If returners do not fill in more
than first-timers, the app is not holding anyone — which is worth knowing before
building anything new.

```sql
WITH last_end AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY session ORDER BY seq DESC) AS rn
  FROM events WHERE name = 'session_end'
),
visit_no AS (
  SELECT uid, day, DENSE_RANK() OVER (PARTITION BY uid ORDER BY day) AS nth_day
  FROM (SELECT DISTINCT uid, day FROM events WHERE uid != '')
)
SELECT CASE WHEN v.nth_day = 1 THEN 'first visit' ELSE 'returned' END AS visit,
       COUNT(*)                                          AS sessions,
       ROUND(AVG(json_extract(e.props,'$.n_filled')), 2) AS avg_tabs_filled
FROM last_end e
JOIN visit_no v ON v.uid = e.uid AND v.day = e.day
WHERE e.rn = 1 AND e.uid != ''
GROUP BY visit;
```

**Traffic sources.**

```sql
SELECT COALESCE(NULLIF(ref,''),'(direct)') AS source, country,
       COUNT(DISTINCT session) AS sessions, COUNT(DISTINCT visitor) AS daily_visitors
FROM events WHERE name='page_view' AND day >= date('now','-30 day')
GROUP BY source, country ORDER BY sessions DESC LIMIT 40;
```

## Operating notes

- **Bots** are dropped at the Worker by user-agent, before any write.
- **Failure is silent by design.** `/api/e` always returns 204 and the write is
  `waitUntil`-ed: a dropped row must never surface an error to the page.
- **Row budget.** D1's free tier allows roughly 100k row writes/day; at ~30
  events per session that is ~3,000 sessions/day. If that becomes the
  constraint, uncomment the `[[analytics_engine_datasets]]` block in
  `worker/wrangler.toml` — the Worker writes to whichever sinks are bound, and
  no code changes. **When querying Analytics Engine, the event count is
  `SUM(_sample_interval)`, never `COUNT(*)`** — it samples at volume, and AE
  retains only ~90 days.
- **Rotating `ANALYTICS_SALT`** breaks the link between past and future visitor
  hashes. That is a feature, not an outage.
