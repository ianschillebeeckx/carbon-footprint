"""The client-side analytics tracker, injected into every built page.

One implementation, five pages (landing, app, methodology, naics, privacy), so
the event schema can't drift between them. Each template carries a
`<!--ANALYTICS-->` marker in its <head>; the builders replace it with
`tracker(page)` and the local Python server leaves it as an inert comment.

Templates also carry a `window.track = function () {};` no-op stub *above* the
marker. That stub is load-bearing: the local server serves the template with
the marker unreplaced, and an undefined `track()` at a call site would throw —
which in this app has already been shown to silently kill every autosave that
runs after it (see the ReferenceError in git history). The stub makes an
un-injected page degrade to doing nothing instead of breaking.

Privacy shape, mirrored in site/privacy-template.html and the `data` section of
the assumptions registry. No cookie and no third-party request. The browser
sends an event name, a per-page-load session id held only in memory, a referrer
host, a viewport width, and a first-party identifier in localStorage that
expires 180 days after it is created. The Worker adds a visitor hash that
rotates daily, which still carries anyone the identifier does not cover.
Nothing a user typed is ever an argument to track().

The identifier exists to make retention measurable — whether people come back
is the clearest read on whether the thing is useful — and it is a real
identifier, not a hash pretending otherwise. Two escape hatches: Global Privacy
Control suppresses it (aggregate counts continue through the server hash), and
`localStorage.cf_no_analytics` stops the tracker outright.
"""

import json

# Placeholders: __PAGE__ (string). Endpoint and batching are operational config
# (worker/wrangler.toml vars), not modelling constants, so they live here and
# not in the assumptions registry — same call as DAILY_IP_LIMIT.
TRACKER_JS = r"""
(function () {
  "use strict";
  var EP = "/api/e", MAX_Q = 40, FLUSH_MS = 15000, PAGE = __PAGE__;
  var UID_KEY = "cf_uid", OPTOUT_KEY = "cf_no_analytics", UID_TTL_DAYS = 180;
  var t0 = Date.now();
  // Session id: per page load, held in a closure. Never written to the device,
  // so it is not storage and not an identifier that outlives the tab.
  var sid = Math.random().toString(36).slice(2, 10) + t0.toString(36);
  var q = [], seq = 0, timer = null, dead = false;

  // Full opt-out: localStorage.setItem("cf_no_analytics", "1") in the console
  // stops the tracker entirely. Documented on the privacy page.
  function optedOut() {
    try { return !!localStorage.getItem(OPTOUT_KEY); } catch (e) { return false; }
  }

  // Durable first-party identifier, so retention is measurable per browser
  // rather than inferred from a server-side hash that fragments on every
  // network change and merges everyone behind one NAT.
  //
  // The expiry is fixed from creation, not refreshed on use: an active visitor
  // is re-identified after UID_TTL_DAYS rather than tracked indefinitely, which
  // caps how long any one browser stays linkable and bounds cohort length to
  // something an analysis would actually use.
  //
  // Global Privacy Control is a machine-readable objection, so those visitors
  // never get an identifier written or read. They still count toward daily
  // uniques through the server-side rotating hash, which is unchanged — the
  // aggregate numbers stay right, only the per-browser linkage is dropped.
  function uid() {
    try { if (navigator.globalPrivacyControl) return ""; } catch (e) {}
    try {
      var raw = localStorage.getItem(UID_KEY);
      var o = raw ? JSON.parse(raw) : null;
      if (o && o.v && +o.exp > Date.now()) return o.v;
      var v;
      try { v = crypto.randomUUID(); }
      catch (e) { v = Date.now().toString(36) + Math.random().toString(36).slice(2, 12); }
      localStorage.setItem(UID_KEY, JSON.stringify({v: v, exp: Date.now() + UID_TTL_DAYS * 864e5}));
      return v;
    } catch (e) { return ""; }   // private browsing, blocked storage, full quota
  }

  // "Returning" without storing anything new: the app already keeps the user's
  // own figures in localStorage because that IS the product. Their presence is
  // a free new-vs-returning signal and costs no extra identifier.
  function returning() {
    try {
      var k = ["cf_data", "cf_travel", "cf_home", "cf_food", "cf_offsets"], i;
      for (i = 0; i < k.length; i++) if (localStorage.getItem(k[i])) return 1;
    } catch (e) {}
    return 0;
  }
  // Host only. A full referrer URL can carry a search query or a session token.
  function refHost() {
    try { return document.referrer ? new URL(document.referrer).host.slice(0, 80) : ""; }
    catch (e) { return ""; }
  }
  // Before anything touches storage: an opted-out visitor must not have an
  // identifier written for them, so this gate precedes uid().
  if (optedOut()) return;   // leaves window.track as the page's no-op stub

  var REF = refHost(), RET = returning(), UID = uid();

  function send(beacon) {
    if (!q.length || dead) return;
    var body;
    try {
      body = JSON.stringify({s: sid, p: PAGE, r: REF, u: RET, i: UID,
                             v: window.innerWidth || 0, e: q});
    } catch (e) { q = []; return; }
    q = [];
    try {
      if (beacon && navigator.sendBeacon) {
        navigator.sendBeacon(EP, new Blob([body], {type: "application/json"}));
      } else {
        fetch(EP, {method: "POST", body: body, keepalive: true,
                   headers: {"Content-Type": "application/json"}}).catch(function () {});
      }
    } catch (e) {}
  }

  window.track = function (name, props) {
    try {
      if (dead) return;
      var ev = {n: String(name).slice(0, 40), t: Date.now() - t0, q: seq++};
      if (props) for (var k in props) if (Object.prototype.hasOwnProperty.call(props, k)) ev[k] = props[k];
      q.push(ev);
      if (q.length >= MAX_Q) send(false);
      else if (!timer) timer = setTimeout(function () { timer = null; send(false); }, FLUSH_MS);
    } catch (e) {}
  };

  // Flush on the way out. visibilitychange is the only event that fires
  // reliably on mobile backgrounding; pagehide covers bfcache navigation.
  //
  // A session that is backgrounded and resumed emits session_end more than
  // once, on purpose: the alternative is firing once and losing everything a
  // visitor did after the first tab-away, and unload events are too unreliable
  // to bet the whole funnel position on. Later rows supersede earlier ones, so
  // any query that aggregates session_end must take the highest seq per
  // session — ANALYTICS.md does this in a `last_end` CTE.
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") { window.track("session_end", sessionShape()); send(true); }
  });
  window.addEventListener("pagehide", function () { send(true); });

  // Funnel position computed from state rather than reconstructed from clicks:
  // one row that says exactly how far this person got, robust to dropped
  // beacons and to events we forgot to add.
  function sessionShape() {
    var out = {dur: Math.round((Date.now() - t0) / 1000)};
    try {
      var filled = [], t = JSON.parse(localStorage.getItem("cf_travel") || "null");
      if (t && ((t.vehicles || []).some(function (v) { return +v.miles > 0; }) ||
                (t.air && (+t.air.short || +t.air.medium || +t.air.long)))) filled.push("travel");
      var h = JSON.parse(localStorage.getItem("cf_home") || "null");
      if (h && (+(h.elec || {}).kwh > 0 || +(h.elec || {}).dollars > 0)) filled.push("home");
      if (localStorage.getItem("cf_food")) filled.push("food");
      if (localStorage.getItem("cf_offsets")) filled.push("offsets");
      var d = JSON.parse(localStorage.getItem("cf_data") || "null");
      if (d && d.transactions && d.transactions.length) {
        filled.push("gs");
        out.txns = d.transactions.length;
      }
      out.filled = filled.join(",");
      out.n_filled = filled.length;
    } catch (e) {}
    return out;
  }

  // Error reporting rides the same pipe. This is the highest-value signal on a
  // public launch: a thrown exception here has already been shown to stop every
  // autosave in the app with no visible symptom, and a stranger will simply
  // leave rather than report it.
  window.addEventListener("error", function (e) {
    try {
      window.track("error", {msg: String(e.message || "").slice(0, 160),
                             src: String(e.filename || "").split("/").pop().slice(0, 40),
                             line: e.lineno || 0});
    } catch (x) {}
  });
  window.addEventListener("unhandledrejection", function (e) {
    try {
      var r = e.reason;
      window.track("error", {msg: ("unhandled: " + ((r && r.message) || r)).slice(0, 160)});
    } catch (x) {}
  });
})();
"""


def tracker(page: str) -> str:
    """The <script> block for one page, ready to substitute for <!--ANALYTICS-->."""
    js = TRACKER_JS.replace("__PAGE__", json.dumps(page))
    return f"<script>{js}</script>"
