-- D1 event store for first-party product analytics.
--
-- One flat table on purpose: every funnel question is a GROUP BY over this,
-- and a flat shape ports to Workers Analytics Engine unchanged if volume ever
-- outgrows D1's row-write budget (see worker/src/index.js, writeEvents).
--
-- What is deliberately NOT here: IP addresses, user agents, URLs with query
-- strings, merchant names, amounts, or anything a user typed. `visitor` is a
-- salted hash that rotates at midnight UTC, so it identifies a person within a
-- day and is uncorrelatable across days by construction.
--
-- Apply:  npx wrangler d1 execute cf_analytics --remote --file worker/schema.sql

CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        INTEGER NOT NULL,   -- server receipt, epoch ms (client clocks lie)
  day       TEXT    NOT NULL,   -- YYYY-MM-DD UTC, the natural grouping key
  visitor   TEXT    NOT NULL,   -- SHA-256(ip + ua + day + salt), 16 hex chars;
                                --   daily reach, cannot be joined across days
  uid       TEXT,               -- first-party localStorage id, 180-day expiry;
                                --   the retention key. Empty for Global Privacy
                                --   Control, private browsing, blocked storage
  session   TEXT    NOT NULL,   -- per page-load, client-generated, memory only
  seq       INTEGER NOT NULL,   -- event order within the session
  name      TEXT    NOT NULL,
  page      TEXT,               -- landing | app | methodology | naics | privacy
  ref       TEXT,               -- referrer HOST only, never the full URL
  country   TEXT,               -- CF-IPCountry (two letters)
  vw        INTEGER,            -- viewport width px — the mobile/desktop cut
  is_returning INTEGER,         -- 1 when this browser already held app state
                                -- (not `returning`: that is a reserved word
                                --  in SQLite and the CREATE would not parse)
  props     TEXT                -- JSON; query with json_extract(props,'$.tab')
);

-- Funnel queries group by day and event name; retention joins visitor to day;
-- session reconstruction walks one session in seq order.
CREATE INDEX IF NOT EXISTS ix_events_day_name ON events (day, name);
CREATE INDEX IF NOT EXISTS ix_events_session  ON events (session, seq);
CREATE INDEX IF NOT EXISTS ix_events_visitor  ON events (visitor, day);
CREATE INDEX IF NOT EXISTS ix_events_uid      ON events (uid, day);
