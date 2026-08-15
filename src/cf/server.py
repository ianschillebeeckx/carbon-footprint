"""Local web server: serves the side-by-side page and handles Monarch
login/fetch from the page itself. Binds to 127.0.0.1 only — credentials go
straight from the form to this process to Monarch, and are never stored."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import aggregate, csv_import, fetch
from .fields import CALC_KEYS, CALC_MODE_FLAGS

TEMPLATE = Path("site/template.html")
VALUES_FILE = Path("data/values.json")
V2_TEMPLATE = Path("site/v2-template.html")
V2_FILE = Path("data/v2_classified.json")
TRAVEL_FILE = Path("data/v2_travel.json")
HOME_FILE = Path("data/v2_home.json")
FOOD_FILE = Path("data/v2_food.json")
OFFSETS_FILE = Path("data/v2_offsets.json")

# The CoolClimate calculator app's origin — the only site allowed to fetch
# /api/values (used by the prefill bookmarklet running on that page).
CALC_ORIGIN = "https://coolclimate-calculator-ui.firebaseapp.com"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _origin_ok(self):
        # Reject cross-origin POSTs from non-local pages.
        origin = self.headers.get("Origin")
        return not origin or origin.startswith(("http://127.0.0.1:", "http://localhost:"))

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            html = TEMPLATE.read_text()
            values = VALUES_FILE.read_text() if VALUES_FILE.exists() else "null"
            html = html.replace("/*__VALUES__*/null", values)
            html = html.replace("/*__SERVED__*/false", "true")
            self._send(200, html.encode(), "text/html; charset=utf-8")
        elif path == "/v2":
            html = V2_TEMPLATE.read_text()
            html = html.replace("/*__V2DATA__*/null", V2_FILE.read_text() if V2_FILE.exists() else "null")
            from . import classify
            html = html.replace("/*__NAICS_OPTIONS__*/null", json.dumps(classify.naics_options()))
            html = html.replace("/*__CAT_DEFAULTS__*/null", json.dumps(classify.default_naics()))
            html = html.replace("/*__BASKET_OPTIONS__*/null", json.dumps(classify.basket_options()))
            idx_cats = json.loads(Path("data/naics_index.json").read_text())["categories"]
            html = html.replace("/*__CATS__*/null", json.dumps(idx_cats))
            html = html.replace("/*__TRAVEL__*/null", TRAVEL_FILE.read_text() if TRAVEL_FILE.exists() else "null")
            html = html.replace("/*__HOME__*/null", HOME_FILE.read_text() if HOME_FILE.exists() else "null")
            html = html.replace("/*__FOOD__*/null", FOOD_FILE.read_text() if FOOD_FILE.exists() else "null")
            html = html.replace("/*__OFFSETS__*/null", OFFSETS_FILE.read_text() if OFFSETS_FILE.exists() else "null")
            self._send(200, html.encode(), "text/html; charset=utf-8")
        elif path == "/data/zip2co2.json":
            p = Path("web/data/zip2co2.json")
            if not p.exists():
                return self._send(404, {"error": "run scripts/build_zip2co2_web.py"})
            self._send(200, p.read_bytes(), "application/json")
        elif path == "/api/status":
            self._send(200, {"has_session": fetch.has_session(), "has_data": VALUES_FILE.exists()})
        elif path == "/api/values":
            if not VALUES_FILE.exists():
                return self._send(404, {"error": "no data yet"})
            values = json.loads(VALUES_FILE.read_text())
            inputs = dict(CALC_MODE_FLAGS)
            for field, calc_key in CALC_KEYS.items():
                inputs[calc_key] = round(values["fields"][field]["monthly_avg"])
            self._send(200, {"inputs": inputs, "window": values["window"]},
                       extra={"Access-Control-Allow-Origin": CALC_ORIGIN})
        else:
            self._send(404, {"error": "not found"})

    def do_OPTIONS(self):
        # CORS preflight for the prefill bookmarklet (public site -> localhost
        # needs Private Network Access approval in Chrome).
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", CALC_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if not self._origin_ok():
            return self._send(403, {"error": "forbidden"})
        try:
            if self.path == "/api/login":
                body = self._json_body()
                if body.get("token"):
                    result = fetch.web_token_login(body["token"])
                else:
                    result = fetch.web_login(
                        body.get("email", ""), body.get("password", ""), body.get("mfa_code") or None
                    )
                self._send(200 if result["ok"] else 401, result)
            elif self.path == "/api/v2/upload":
                from . import classify
                n = int(self.headers.get("Content-Length") or 0)
                text = self.rfile.read(n).decode("utf-8-sig")
                tmp = Path("data/_v2_upload.csv")
                tmp.write_text(text)
                try:
                    classify.run(str(tmp))
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                self._send(200, {"ok": True})
            elif self.path in ("/api/v2/travel", "/api/v2/home", "/api/v2/food", "/api/v2/offsets"):
                body = self._json_body()
                f = {"travel": TRAVEL_FILE, "home": HOME_FILE, "food": FOOD_FILE,
                     "offsets": OFFSETS_FILE}[self.path.rsplit("/", 1)[1]]
                f.parent.mkdir(exist_ok=True)
                f.write_text(json.dumps(body, indent=1))
                self._send(200, {"ok": True})
            elif self.path == "/api/v2/reset":
                # Full reset: drop all classified transactions, every merchant
                # rule (including manual/confirmed corrections), and all
                # travel/home/food/offsets inputs.
                from . import classify
                for f in (V2_FILE, classify.CACHE_FILE, TRAVEL_FILE, HOME_FILE, FOOD_FILE, OFFSETS_FILE):
                    if f.exists():
                        f.unlink()
                self._send(200, {"ok": True})
            elif self.path == "/api/v2/correct":
                from . import classify
                body = self._json_body()
                try:
                    result = classify.apply_correction(
                        body.get("merchant", ""), body.get("category_hint", ""),
                        naics=body.get("naics"), mix=body.get("mix"),
                        basket=body.get("basket"),
                        category=body.get("category"),
                        all_categories=bool(body.get("all_categories")),
                        source="confirmed" if body.get("confirm") else "manual")
                except (ValueError, KeyError, TypeError) as e:
                    return self._send(400, {"error": str(e)})
                self._send(200, result)
            elif self.path == "/api/upload":
                n = int(self.headers.get("Content-Length") or 0)
                text = self.rfile.read(n).decode("utf-8-sig")
                try:
                    data = csv_import.parse(text)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                csv_import.DATA_DIR.mkdir(exist_ok=True)
                csv_import.TRANSACTIONS_FILE.write_text(json.dumps(data, indent=1))
                aggregate.run()
                self._send(200, {"ok": True, "count": len(data["transactions"])})
            elif self.path == "/api/refresh":
                months = int(self._json_body().get("months") or 12)
                mm = fetch.session_client()
                if mm is None:
                    return self._send(401, {"error": "no_session"})
                try:
                    fetch.run_with(mm, months)
                except fetch.AUTH_ERRORS:
                    return self._send(401, {"error": "session_expired"})
                aggregate.run()
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:  # surface anything else to the page
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):
        # Default logging shows only the request line + status (no bodies,
        # so no credentials); useful for debugging the page.
        super().log_message(fmt, *args)


def run(port: int = 8742) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving on http://127.0.0.1:{port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
