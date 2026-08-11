"""Pull transactions + categories from Monarch Money and cache them locally.

Used two ways:
- CLI (`cf fetch`): interactive login in the terminal on first run.
- Web (`cf serve`): login form on the page posts to the local server,
  which calls web_login()/run_with() here.

The session is saved (encrypted) under .mm/ and reused either way.
"""

import asyncio
import datetime as dt
import json
from pathlib import Path

from monarchmoney import (
    AuthenticationError,
    InvalidMFAError,
    LoginFailedException,
    MFARequiredError,
    MonarchMoney,
    RateLimitError,
    RequireMFAException,
    SessionExpiredError,
)
from monarchmoney.monarchmoney import MonarchMoneyEndpoints

# Monarch moved from api.monarchmoney.com to api.monarch.com; the old host
# 301s, which turns aiohttp POSTs into GETs and breaks GraphQL.
MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"

DATA_DIR = Path("data")
TRANSACTIONS_FILE = DATA_DIR / "transactions.json"
SESSION_FILE = Path(".mm/mm_session.pickle")
PAGE_SIZE = 500

AUTH_ERRORS = (AuthenticationError, LoginFailedException, SessionExpiredError)


def has_session() -> bool:
    return SESSION_FILE.exists()


def session_client() -> MonarchMoney | None:
    """Client restored from the saved session, or None if there isn't one."""
    if not has_session():
        return None
    mm = MonarchMoney()
    try:
        mm.load_session()
        return mm
    except Exception:
        return None


def web_login(email: str, password: str, mfa_code: str | None = None) -> dict:
    """Login for the web flow. Returns {ok}, {ok, mfa_required} or {ok, error}."""

    async def go():
        mm = MonarchMoney()
        try:
            if mfa_code:
                await mm.multi_factor_authenticate(email, password, mfa_code)
                mm.save_session()
            else:
                await mm.login(email, password, use_saved_session=False, save_session=True)
            return {"ok": True}
        except (RequireMFAException, MFARequiredError):
            return {"ok": False, "mfa_required": True}
        except RateLimitError:
            return {
                "ok": False,
                "error": "Monarch is rate-limiting logins from this machine — wait 15-30 minutes and try again",
            }
        except (InvalidMFAError, *AUTH_ERRORS) as e:
            # Monarch returns HTTP 404 for bad credentials, which the library
            # misreads as "endpoint missing" and reports as a GraphQL failure.
            err = str(e)
            if "GraphQL login" in err or "session token" in err:
                err = "Invalid email or password"
            return {"ok": False, "error": err}

    return asyncio.run(go())


def web_token_login(token: str) -> dict:
    """Login with a session token copied from the Monarch web app
    (for accounts that sign in via Google and have no password)."""

    async def go():
        # Accept the raw Authorization header value ("Token abc...") too.
        cleaned = token.strip()
        if cleaned.lower().startswith("token "):
            cleaned = cleaned[6:].strip()
        mm = MonarchMoney(token=cleaned)
        try:
            await mm.get_subscription_details()  # cheap call to validate the token
        except Exception:
            return {"ok": False, "error": "Token didn't work — copy it again from a fresh Monarch tab"}
        mm.save_session()
        return {"ok": True}

    return asyncio.run(go())


async def _pull(mm: MonarchMoney, months: int) -> dict:
    end = dt.date.today()
    start = end - dt.timedelta(days=round(months * 30.44))

    results = []
    offset = 0
    total = None
    while True:
        page = await mm.get_transactions(
            limit=PAGE_SIZE,
            offset=offset,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        block = page["allTransactions"]
        total = block["totalCount"]
        results.extend(block["results"])
        offset += PAGE_SIZE
        print(f"  fetched {min(offset, total)}/{total} transactions")
        if offset >= total:
            break

    categories = await mm.get_transaction_categories()
    return {
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "months": months,
        "categories": [
            {"name": c["name"], "group_type": (c.get("group") or {}).get("type")}
            for c in categories.get("categories", [])
        ],
        "transactions": [
            {
                "date": t["date"],
                "amount": t["amount"],
                "merchant": (t.get("merchant") or {}).get("name") or t.get("plaidName") or "",
                "category": (t.get("category") or {}).get("name") or "Uncategorized",
                "group_type": ((t.get("category") or {}).get("group") or {}).get("type"),
                "pending": t.get("pending", False),
                "hidden": t.get("hideFromReports", False),
                "notes": t.get("notes") or "",
            }
            for t in results
        ],
    }


def run_with(mm: MonarchMoney, months: int = 12) -> dict:
    data = asyncio.run(_pull(mm, months))
    DATA_DIR.mkdir(exist_ok=True)
    TRANSACTIONS_FILE.write_text(json.dumps(data, indent=1))
    n = len(data["transactions"])
    print(f"Saved {n} transactions ({data['start_date']} to {data['end_date']}) -> {TRANSACTIONS_FILE}")
    return data


def run(months: int = 12) -> None:
    """CLI entry: saved session if valid, else interactive terminal login."""
    mm = session_client()
    if mm is None:
        mm = MonarchMoney()
        asyncio.run(mm.interactive_login())
    data = run_with(mm, months)
    cats = sorted({t["category"] for t in data["transactions"]})
    print(f"{len(cats)} categories in use: {', '.join(cats)}")
