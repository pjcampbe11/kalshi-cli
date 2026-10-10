#!/usr/bin/env python3
"""
kalshi_scan.py - browse and screen Kalshi markets from the command line.

WHAT THIS SCRIPT DOES
  * Pulls every open market from Kalshi's API and lets you slice it:
    top 5 / 10 / 20 / 100 by category, by price, by volume, by keyword,
    by how soon it closes, by how much it moved today, and more.
  * Prints each market's ticker and its kalshi.com link, so you find the
    market here and then place the trade in the Kalshi app.
  * Shows one market in detail (rules, both sides' prices, order book,
    fee, break-even), lists every market in an event, and can watch prices.

WHAT THIS SCRIPT DOES NOT DO
  * It never places, changes or cancels an order. Every call is a GET.

REQUIREMENTS
  * Python 3.9+. Standard library only.
  * No API key is needed for market data. If you set the two environment
    variables below, requests are signed with your key and the `account`
    command works. That part needs:  pip install cryptography

API KEY AS ENVIRONMENT VARIABLES (Windows PowerShell)
  For this window only:
      $env:KALSHI_API_KEY_ID = "your-key-id"
      $env:KALSHI_PRIVATE_KEY_PATH = "C:\\path\\to\\kalshi-key.pem"
  Permanently (open a new window afterwards):
      setx KALSHI_API_KEY_ID "your-key-id"
      setx KALSHI_PRIVATE_KEY_PATH "C:\\path\\to\\kalshi-key.pem"

EXAMPLES
  python kalshi_scan.py categories
  python kalshi_scan.py top --category economics --top 10
  python kalshi_scan.py top --all --under 10 --top 20
  python kalshi_scan.py top --search fed rate --top 5
  python kalshi_scan.py top --preset movers --category politics sports
  python kalshi_scan.py top --preset closing-soon --min-volume-24h 1000 --open 3
  python kalshi_scan.py show KXFED-27APR-T5.00
  python kalshi_scan.py event KXFED-27APR
  python kalshi_scan.py watch KXFED-27APR-T5.00 --every 60
"""
import argparse
import base64
import csv
import datetime as dt
import json
import math
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

# API root. Override with the KALSHI_API_BASE environment variable if Kalshi moves it.
BASE = os.environ.get("KALSHI_API_BASE", "https://external-api.kalshi.com/trade-api/v2")

# Where the downloaded market list is kept between runs, so the second query
# is instant instead of another full download.
CACHE_FILE = os.path.join(tempfile.gettempdir(), "kalshi_scan_cache.json")

# Short names you type -> the category names Kalshi uses.
# `python kalshi_scan.py categories` shows the live list with counts.
CATEGORIES = {
    "politics": "Politics",
    "elections": "Elections",
    "sports": "Sports",
    "economics": "Economics",
    "financials": "Financials",
    "crypto": "Crypto",
    "commodities": "Commodities",
    "companies": "Companies",
    "business": "Business",
    "entertainment": "Entertainment",
    "tech": "Science and Technology",
    "ai": "AI",
    "weather": "Climate and Weather",
    "health": "Health",
    "world": "World",
    "social": "Social",
    "mentions": "Mentions",
    "transportation": "Transportation",
}

# Ways to order the results. Each entry is (description, key function, reverse?).
# The key functions read the flat market dicts built in flatten().
SORTS = {
    "hot":          ("most contracts traded in the last 24 hours", lambda m: m["vol24"], True),
    "volume":       ("most contracts traded all time", lambda m: m["vol"], True),
    "interest":     ("most contracts currently held (open interest)", lambda m: m["oi"], True),
    "movers":       ("biggest price change since yesterday, up or down", lambda m: abs(m["change"] or 0), True),
    "gainers":      ("biggest rise since yesterday", lambda m: m["change"] or -999, True),
    "losers":       ("biggest fall since yesterday", lambda m: m["change"] if m["change"] is not None else 999, False),
    "closing":      ("closing soonest", lambda m: m["hours_left"] if m["hours_left"] is not None else 9e9, False),
    "newest":       ("opened most recently", lambda m: m["open_time"] or "", True),
    "cheapest":     ("lowest price to buy", lambda m: m["buy_price"] if m["buy_price"] is not None else 999, False),
    "priciest":     ("highest price to buy", lambda m: m["buy_price"] or -1, True),
    "wide-spread":  ("widest gap between bid and ask (room to post your own price)", lambda m: m["spread"] if m["spread"] is not None else -1, True),
    "tight-spread": ("narrowest gap between bid and ask", lambda m: m["spread"] if m["spread"] is not None else 999, False),
}

# Presets are named bundles of filters. Anything you pass on the command line
# wins over the preset, so `--preset longshots --top 50` works as expected.
PRESETS = {
    "hot":          dict(sort="hot"),
    "longshots":    dict(max_price=10, sort="hot"),                 # contracts under 10 cents
    "favorites":    dict(min_price=85, sort="hot"),                 # contracts at 85 cents and up
    "coinflips":    dict(min_price=40, max_price=60, sort="hot"),
    "movers":       dict(sort="movers", min_volume_24h=100),
    "closing-soon": dict(closes_within=2, sort="closing"),
    "new":          dict(opened_within=2, sort="newest"),
    # A gap of 5 to 20 cents with real trading: room to post a limit order inside the spread.
    "wide":         dict(min_spread=5, max_spread=20, min_volume_24h=500, sort="hot"),
    "tight":        dict(max_spread=2, min_volume_24h=100, sort="hot"),
    # Markets that fit the guide's rules: 15 cents or more, favourite side
    # between 60 and 95, resolving within 30 days, with real trading activity.
    # Sports and crypto are left out because the guide says to avoid live sports
    # and short-term crypto markets.
    "guide":        dict(min_price=60, max_price=95, closes_within=30, min_volume_24h=500, sort="hot",
                         skip_category=["sports", "crypto"]),
}


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

def cents(text):
    """API prices are strings like '0.6200'. Return cents (62.0), or None when there is no price."""
    try:
        value = float(text) * 100
    except (TypeError, ValueError):
        return None
    return round(value, 2) if value > 0 else None      # 0.0000 means nobody is offering


def num(text):
    """Contract counts are strings like '119816.03'. Return a float, 0 if missing."""
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def parse_time(text):
    """Turn '2026-10-13T00:30:00Z' into an aware datetime, or None."""
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def time_left(hours):
    """Human-readable time until close: 45m, 7h, 3d, 5mo, 2y."""
    if hours is None:
        return "?"
    if hours < 0:
        return "closed"
    if hours < 1:
        return f"{int(hours * 60)}m"
    if hours < 48:
        return f"{int(hours)}h"
    days = hours / 24
    if days < 60:
        return f"{int(days)}d"
    if days < 730:
        return f"{int(days / 30)}mo"
    return f"{int(days / 365)}y"


def short_number(value):
    """12345 -> 12.3k, 2500000 -> 2.5m. Keeps the table narrow."""
    if value >= 1e6:
        return f"{value / 1e6:.1f}m"
    if value >= 1e3:
        return f"{value / 1e3:.1f}k"
    return f"{value:.0f}"


def price_text(value):
    """62.0 -> '62', 0.5 -> '0.5', None -> '-'."""
    return "-" if value is None else f"{value:g}"


def market_url(series_ticker, event_ticker):
    """
    The kalshi.com page for an event. The middle part of the address is a
    title slug that Kalshi ignores, so any placeholder works; the series and
    event tickers are what identify the page.
    """
    return f"https://kalshi.com/markets/{series_ticker.lower()}/x/{event_ticker.lower()}"


def taker_fee_cents(price_cents):
    """Standard market-order fee per contract, in cents: 7 x P x (1 - P), P in dollars."""
    p = price_cents / 100.0
    return 7.0 * p * (1.0 - p)


# ----------------------------------------------------------------------------
# Talking to the API
# ----------------------------------------------------------------------------

_last_call = 0.0          # time of the previous request, for pacing
_auth = None              # (key_id, private_key) once loaded, False if not available


def load_auth(required=False):
    """
    Read the API key from the environment. Returns (key_id, private_key) or None.
    Market data does not need a key, so by default a missing key is fine.
    """
    global _auth
    if _auth is not None:
        return _auth or None
    key_id = os.environ.get("KALSHI_API_KEY_ID")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    _auth = False
    if not key_id or not key_path:
        if required:
            sys.exit("This command needs your API key. Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH "
                     "(see the top of this file for the PowerShell lines).")
        return None
    try:
        from cryptography.hazmat.primitives import serialization
        with open(os.path.expanduser(key_path), "rb") as fh:
            _auth = (key_id, serialization.load_pem_private_key(fh.read(), password=None))
    except ImportError:
        if required:
            sys.exit("Signing requests needs the cryptography package:  pip install cryptography")
    except OSError as err:
        if required:
            sys.exit(f"Could not read the key file: {err}")
    return _auth or None


def sign_headers(key_id, private_key, method, url_without_query):
    """
    Kalshi signs the string  <milliseconds><METHOD><path from /trade-api/v2, no query>
    with your private key (RSA-PSS + SHA-256, or Ed25519) and sends it base64-encoded.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    ts = str(int(time.time() * 1000))
    path = urllib.parse.urlparse(url_without_query).path
    message = f"{ts}{method}{path}".encode("utf-8")
    if isinstance(private_key, Ed25519PrivateKey):
        raw = private_key.sign(message)
    else:
        raw = private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
    return {"KALSHI-ACCESS-KEY": key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(raw).decode("utf-8")}


def api_get(endpoint, params=None, need_auth=False):
    """
    GET one endpoint and return the parsed JSON.
      * Paces itself (4 requests a second at most) so Kalshi does not rate-limit us.
      * If Kalshi answers 429 (too many requests) it waits and tries again.
      * Signs the request when an API key is set in the environment.
    """
    global _last_call
    url = BASE + endpoint
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    auth = load_auth(required=need_auth)
    for attempt in range(6):
        wait = 0.25 - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        headers = {"Accept": "application/json", "User-Agent": "kalshi-scan/1.0"}
        if auth:
            headers.update(sign_headers(auth[0], auth[1], "GET", url))
        request = urllib.request.Request(url + ("?" + query if query else ""), headers=headers)
        _last_call = time.time()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code == 429:                        # slow down, then retry
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    sys.exit("Kalshi kept rate-limiting the request. Wait a minute and try again.")


# ----------------------------------------------------------------------------
# Downloading and caching the full market list
# ----------------------------------------------------------------------------

def flatten(event, market):
    """
    Boil one API market down to the handful of fields the screener uses.
    Prices are in cents. `yes_ask` / `no_ask` are what it costs to BUY that side.
    """
    yes_bid, yes_ask = cents(market.get("yes_bid_dollars")), cents(market.get("yes_ask_dollars"))
    no_bid, no_ask = cents(market.get("no_bid_dollars")), cents(market.get("no_ask_dollars"))
    last, prev = cents(market.get("last_price_dollars")), cents(market.get("previous_price_dollars"))
    # An ask of 100 cents is not a real offer (you would pay $1 to win $1).
    if yes_ask is not None and yes_ask >= 100:
        yes_ask = None
    if no_ask is not None and no_ask >= 100:
        no_ask = None
    # The event title is the question; the market's own label says which outcome.
    # When an event has several markets (one per team, per rate level...), the
    # label is the only thing telling them apart, so always show it then.
    label = market.get("yes_sub_title") or ""
    title = event.get("title") or market.get("title") or ""
    several = len(event.get("markets") or []) > 1
    if label and (several or label.lower() not in title.lower()):
        title = f"{title} [{label}]"
    return {
        "ticker": market.get("ticker", ""),
        "event": event.get("event_ticker", ""),
        "series": event.get("series_ticker", ""),
        "category": event.get("category") or "",
        "title": title,
        "yes_bid": yes_bid, "yes_ask": yes_ask, "no_bid": no_bid, "no_ask": no_ask,
        "last": last,
        "change": round(last - prev, 2) if last is not None and prev is not None else None,
        "spread": round(yes_ask - yes_bid, 2) if yes_ask is not None and yes_bid is not None else None,
        "vol24": num(market.get("volume_24h_fp")),
        "vol": num(market.get("volume_fp")),
        "oi": num(market.get("open_interest_fp")),
        "close_time": market.get("close_time") or "",
        "open_time": market.get("open_time") or "",
        "url": market_url(event.get("series_ticker", ""), event.get("event_ticker", "")),
    }


def download_markets():
    """Page through every open event (each comes with its markets) and return flat rows."""
    rows, cursor, pages = [], "", 0
    print("Downloading every open market from Kalshi (takes 1 to 2 minutes, then it is saved for reuse)",
          end="", file=sys.stderr, flush=True)
    while True:
        page = api_get("/events", {"status": "open", "with_nested_markets": "true", "limit": 200, "cursor": cursor})
        for event in page.get("events", []):
            for market in event.get("markets") or []:
                if market.get("status") not in (None, "active", "open"):
                    continue                      # skip markets that are paused or already closed
                rows.append(flatten(event, market))
        pages += 1
        print(".", end="", file=sys.stderr, flush=True)
        cursor = page.get("cursor") or ""
        if not cursor or not page.get("events"):
            break
    print(f" {len(rows):,} markets.", file=sys.stderr)
    return rows


def load_markets(max_age_minutes, refresh=False):
    """Return the market list, from the cache file if it is fresh enough, else from Kalshi."""
    if not refresh and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as fh:
                cached = json.load(fh)
            age = (time.time() - cached["fetched_at"]) / 60.0
            if age <= max_age_minutes:
                print(f"(prices as of {age:.0f} min ago; add --refresh before the command for live prices, "
                      f"or use `show TICKER`)", file=sys.stderr)
                return cached["rows"]
        except (OSError, ValueError, KeyError):
            pass                                   # unreadable cache: just download again
    rows = download_markets()
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": time.time(), "rows": rows}, fh)
    except OSError:
        pass                                       # caching is a convenience, never fatal
    return rows


def add_clock_fields(rows):
    """Work out hours until close and hours since open, fresh on every run."""
    now = dt.datetime.now(dt.timezone.utc)
    for m in rows:
        closes, opened = parse_time(m["close_time"]), parse_time(m["open_time"])
        m["hours_left"] = (closes - now).total_seconds() / 3600 if closes else None
        m["hours_open"] = (now - opened).total_seconds() / 3600 if opened else None
    return rows


# ----------------------------------------------------------------------------
# Filtering
# ----------------------------------------------------------------------------

def pick_side(m, side, lo, hi):
    """
    Decide which side of the market matches the price filter and record it as
    m['buy_side'] / m['buy_price'] (the price you would pay right now).
      side = 'yes' or 'no': only that side is considered.
      side = 'either': YES is tried first, then NO.
    Returns False when no side has an offer inside [lo, hi].
    """
    for name in (("yes", "no") if side == "either" else (side,)):
        price = m[name + "_ask"]
        if price is None:
            continue
        if (lo is None or price >= lo) and (hi is None or price <= hi):
            m["buy_side"], m["buy_price"] = name.upper(), price
            return True
    return False


def apply_filters(rows, a):
    """Run every filter the user asked for and return the surviving markets."""
    wanted = {CATEGORIES[c].lower() for c in (a.category or [])}
    wanted |= {c.lower() for c in (a.category_name or [])}
    skipped = {CATEGORIES[c].lower() for c in (a.skip_category or [])}
    words = [w.lower() for w in (a.search or [])]
    banned = [w.lower() for w in (a.exclude or [])]
    has_price_filter = a.min_price is not None or a.max_price is not None
    out = []
    for m in rows:
        if wanted and m["category"].lower() not in wanted:
            continue
        if m["category"].lower() in skipped:
            continue
        haystack = f"{m['title']} {m['ticker']} {m['category']}".lower()
        if words:
            hits = [w in haystack for w in words]
            if not (any(hits) if a.any_word else all(hits)):
                continue
        if any(w in haystack for w in banned):
            continue
        if a.series and m["series"].upper() not in {s.upper() for s in a.series}:
            continue
        # --- price: which side can be bought inside the range? ---
        if not pick_side(m, a.side, a.min_price, a.max_price):
            if has_price_filter or a.side != "either":
                continue
            m["buy_side"], m["buy_price"] = "", None       # no offers at all; keep it, show blanks
        # --- activity ---
        if m["vol24"] < a.min_volume_24h or m["vol"] < a.min_volume or m["oi"] < a.min_interest:
            continue
        # --- spread ---
        if a.max_spread is not None and (m["spread"] is None or m["spread"] > a.max_spread):
            continue
        if a.min_spread is not None and (m["spread"] is None or m["spread"] < a.min_spread):
            continue
        # --- timing ---
        if m["hours_left"] is not None and m["hours_left"] < 0:
            continue
        if a.closes_within is not None and (m["hours_left"] is None or m["hours_left"] > a.closes_within * 24):
            continue
        if a.closes_after is not None and (m["hours_left"] is None or m["hours_left"] < a.closes_after * 24):
            continue
        if a.opened_within is not None and (m["hours_open"] is None or m["hours_open"] > a.opened_within * 24):
            continue
        # --- movement ---
        if a.min_move is not None and (m["change"] is None or abs(m["change"]) < a.min_move):
            continue
        out.append(m)
    return out


def one_per_event(rows):
    """Keep only the first (best-ranked) market from each event, so one event cannot fill the list."""
    seen, out = set(), []
    for m in rows:
        if m["event"] not in seen:
            seen.add(m["event"])
            out.append(m)
    return out


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------

def print_table(rows, show_links=True):
    """Fixed-width table sized to the terminal. Each market gets a second line with its link."""
    width = shutil.get_terminal_size((120, 30)).columns
    fixed = 4 + 11 + 10 + 5 + 6 + 7 + 9 + 9 + 6 + 2          # every column except the title
    title_w = max(30, min(90, width - fixed - 1))
    head = (f"{'#':>3} {'BUY':<10} {'YES b/a':<10}{'CHG':>5} {'SPRD':>5} {'PAYS':>6} "
            f"{'24H VOL':>8} {'OPEN INT':>8} {'ENDS':>5}  {'MARKET'}")
    print(head)
    print("-" * min(width - 1, len(head) + title_w - 6))
    for i, m in enumerate(rows, 1):
        buy = f"{m['buy_side']} {price_text(m['buy_price'])}c" if m.get("buy_price") is not None else "-"
        book = f"{price_text(m['yes_bid'])}/{price_text(m['yes_ask'])}"
        change = "" if not m["change"] else f"{m['change']:+g}"
        # PAYS = what $1 becomes if this side wins, before fees. 10c -> 10.0x
        pays = f"{100 / m['buy_price']:.1f}x" if m.get("buy_price") else "-"
        title = m["title"] if len(m["title"]) <= title_w else m["title"][:title_w - 3] + "..."
        print(f"{i:>3} {buy:<10} {book:<10}{change:>5} {price_text(m['spread']):>5} {pays:>6} "
              f"{short_number(m['vol24']):>8} {short_number(m['oi']):>8} {time_left(m['hours_left']):>5}  {title}")
        if show_links:
            print(f"{'':>4}{m['ticker']}  [{m['category']}]  {m['url']}")


def write_csv(rows, path):
    """Save the result set as a CSV you can open in Excel."""
    cols = ["ticker", "event", "series", "category", "title", "buy_side", "buy_price", "yes_bid", "yes_ask",
            "no_bid", "no_ask", "last", "change", "spread", "vol24", "vol", "oi", "close_time", "url"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} rows to {path}")


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

def cmd_categories(a):
    """List every category that has open markets, with counts and the short name to type."""
    rows = add_clock_fields(load_markets(a.cache_minutes, a.refresh))
    stats = {}
    for m in rows:
        s = stats.setdefault(m["category"], {"markets": 0, "events": set(), "vol24": 0.0})
        s["markets"] += 1
        s["events"].add(m["event"])
        s["vol24"] += m["vol24"]
    short = {v: k for k, v in CATEGORIES.items()}
    print(f"{'TYPE THIS':<16} {'CATEGORY':<26} {'EVENTS':>7} {'MARKETS':>8} {'24H VOLUME':>11}")
    print("-" * 72)
    for name, s in sorted(stats.items(), key=lambda kv: -kv[1]["vol24"]):
        flag = short.get(name) or f'--category-name "{name}"'
        print(f"{flag:<16} {name:<26} {len(s['events']):>7,} {s['markets']:>8,} {short_number(s['vol24']):>11}")
    print(f"\nExample:  python kalshi_scan.py top --category {next(iter(short.values()))} --top 10")


def cmd_top(a):
    """The screener: filter, sort, cut to the top N, print."""
    # 1. A preset fills in any option the user left at its default.
    if a.preset:
        for key, value in PRESETS[a.preset].items():
            if getattr(a, key) in (None, 0, 0.0) or (key == "sort" and a.sort is None):
                setattr(a, key, value)
    if a.under is not None:
        a.max_price = a.under if a.max_price is None else min(a.max_price, a.under)
    if a.over is not None:
        a.min_price = a.over if a.min_price is None else max(a.min_price, a.over)
    a.sort = a.sort or "hot"

    # 2. Load, filter, sort.
    rows = add_clock_fields(load_markets(a.cache_minutes, a.refresh))
    found = apply_filters(rows, a)
    _, key, reverse = SORTS[a.sort]
    found.sort(key=key, reverse=reverse)
    if a.one_per_event:
        found = one_per_event(found)
    total = len(found)
    found = found[: a.top]

    # 3. Output.
    if a.json:
        print(json.dumps(found, indent=2))
    else:
        scope = ", ".join(CATEGORIES[c] for c in a.category) if a.category else "all categories"
        print(f"\nTop {len(found)} of {total:,} matches in {scope}, sorted by: {SORTS[a.sort][0]}\n")
        if found:
            print_table(found, show_links=not a.no_links)
        else:
            print("Nothing matched. Loosen a filter, or run `categories` to see what is open.")
        if a.max_price is not None and a.max_price < 15 and found:
            print("\nNote: contracts under 15 cents win less often than their price implies. "
                  "The guide says to skip them.")
    if a.csv:
        write_csv(found, a.csv)
    for m in found[: a.open]:
        webbrowser.open(m["url"])


def cmd_show(a):
    """Everything about one market: prices on both sides, fee, break-even, rules, order book, link."""
    market = api_get("/markets/" + urllib.parse.quote(a.ticker.upper()))["market"]
    try:
        event = api_get("/events/" + urllib.parse.quote(market["event_ticker"]))["event"]
    except Exception:
        event = {"event_ticker": market.get("event_ticker", ""), "series_ticker": market.get("event_ticker", "").split("-")[0]}
    m = add_clock_fields([flatten(event, market)])[0]
    print(f"\n{m['title']}")
    print(f"{m['ticker']}   [{m['category']}]   status: {market.get('status')}   ends in {time_left(m['hours_left'])} ({m['close_time']})")
    print(f"Link: {m['url']}\n")
    for side in ("yes", "no"):
        ask, bid = m[side + "_ask"], m[side + "_bid"]
        if ask is None:
            print(f"  {side.upper():<3} bid {price_text(bid)}c / ask -   (nobody is selling this side right now)")
            continue
        fee = taker_fee_cents(ask)
        print(f"  {side.upper():<3} bid {price_text(bid)}c / ask {price_text(ask)}c   pays {100 / ask:.2f}x   "
              f"market-order fee {fee:.2f}c   you need it to be {ask + fee:.1f}%+ likely to break even")
    moved = "no change" if not m["change"] else f"{m['change']:+g} since yesterday"
    print(f"\n  last trade {price_text(m['last'])}c ({moved})"
          f"   spread {price_text(m['spread'])}c   24h volume {short_number(m['vol24'])}   open interest {short_number(m['oi'])}")
    if market.get("rules_primary"):
        print("\nRULES: " + market["rules_primary"])
    if a.full_rules and market.get("rules_secondary"):
        print("\nMORE:  " + market["rules_secondary"])
    # The order book lists resting bids on each side. Kalshi may require a key for it.
    try:
        book = api_get(f"/markets/{urllib.parse.quote(a.ticker.upper())}/orderbook", {"depth": a.depth})["orderbook_fp"]
        print(f"\nORDER BOOK (best {a.depth} bids per side: price x contracts)")
        for side in ("yes", "no"):
            levels = sorted(book.get(side + "_dollars") or [], key=lambda lv: -float(lv[0]))[: a.depth]
            text = "   ".join(f"{float(p) * 100:g}c x {short_number(float(q))}" for p, q in levels) or "(empty)"
            print(f"  {side.upper():<3} {text}")
    except urllib.error.HTTPError as err:
        print(f"\n(order book not available without an API key: HTTP {err.code})")
    except Exception:
        pass
    if a.open:
        webbrowser.open(m["url"])


def cmd_event(a):
    """List every market inside one event and add up the prices."""
    event = api_get("/events/" + urllib.parse.quote(a.event_ticker.upper()), {"with_nested_markets": "true"})["event"]
    rows = add_clock_fields([flatten(event, mk) for mk in event.get("markets") or []])
    for m in rows:
        pick_side(m, "yes", None, None) or m.update(buy_side="", buy_price=None)
    rows.sort(key=lambda m: -(m["yes_ask"] or m["last"] or 0))
    print(f"\n{event.get('title')}   [{event.get('category')}]")
    print(f"Link: {market_url(event.get('series_ticker', ''), event.get('event_ticker', ''))}\n")
    print_table(rows, show_links=False)
    print("\nTickers: " + "  ".join(m["ticker"] for m in rows))
    # When exactly one outcome can win, the YES prices should add up to about 100.
    # Asks adding to well over 100 is the cost of buying every outcome; bids adding
    # to over 100 would mean the market is paying you to sell every outcome.
    if event.get("mutually_exclusive"):
        asks = [m["yes_ask"] for m in rows if m["yes_ask"] is not None]
        bids = [m["yes_bid"] for m in rows if m["yes_bid"] is not None]
        print(f"\nOnly one outcome can win. YES asks add up to {sum(asks):g}c across {len(asks)} outcomes; "
              f"YES bids add up to {sum(bids):g}c. Anything far from 100 means thin or stale prices.")


def cmd_watch(a):
    """Re-check a few markets on a timer and print a line whenever a price moves."""
    every = max(10, a.every)
    previous = {}
    print(f"Watching {len(a.tickers)} market(s) every {every}s. Ctrl+C to stop.")
    try:
        for _ in range(a.count) if a.count else iter(int, 1):
            for ticker in a.tickers:
                market = api_get("/markets/" + urllib.parse.quote(ticker.upper()))["market"]
                now = (cents(market.get("yes_bid_dollars")), cents(market.get("yes_ask_dollars")),
                       cents(market.get("last_price_dollars")))
                if previous.get(ticker) != now:
                    stamp = dt.datetime.now().strftime("%H:%M:%S")
                    print(f"{stamp}  {ticker.upper():<28} YES bid {price_text(now[0])}c  ask {price_text(now[1])}c  "
                          f"last {price_text(now[2])}c" + ("" if ticker not in previous else "   <- changed"))
                    previous[ticker] = now
            time.sleep(every)
    except KeyboardInterrupt:
        print("\nStopped.")


def cmd_open(a):
    """Open a market's kalshi.com page in your browser."""
    market = api_get("/markets/" + urllib.parse.quote(a.ticker.upper()))["market"]
    event_ticker = market["event_ticker"]
    try:
        series = api_get("/events/" + urllib.parse.quote(event_ticker))["event"]["series_ticker"]
    except Exception:
        series = event_ticker.split("-")[0]
    url = market_url(series, event_ticker)
    print(url)
    webbrowser.open(url)


def cmd_account(a):
    """Your cash balance and open positions (needs the API key environment variables)."""
    balance = api_get("/portfolio/balance", need_auth=True)
    print(f"Cash balance: ${num(balance.get('balance')) / 100:,.2f}")
    data = api_get("/portfolio/positions", {"limit": 200}, need_auth=True)
    positions = data.get("market_positions") or []
    if not positions:
        print("No open positions.")
    for p in positions:
        # Field names differ between API versions, so print whatever is there.
        details = "  ".join(f"{k}={v}" for k, v in p.items() if k != "ticker" and v not in (None, "", 0, "0", "0.00", "0.0000"))
        print(f"  {p.get('ticker', '?'):<30} {details}")


# ----------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        description="Browse and screen Kalshi markets. Read-only: never places orders.",
        epilog="Run `kalshi_scan.py top -h` for every filter.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--refresh", action="store_true", help="re-download the market list instead of using the saved copy")
    p.add_argument("--cache-minutes", type=float, default=30, help="how long a saved market list stays good (default 30)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("categories", help="list categories with open-market counts").set_defaults(func=cmd_categories)

    t = sub.add_parser(
        "top", help="screen markets: top N by category, price, volume, keyword and more",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="SORTS\n" + "\n".join(f"  {k:<13} {v[0]}" for k, v in SORTS.items())
               + "\n\nPRESETS\n" + "\n".join(f"  {k:<13} {' '.join(f'--{x.replace(chr(95), chr(45))} {chr(32).join(y) if isinstance(y, list) else y}' for x, y in v.items())}"
                                             for k, v in PRESETS.items()))
    g = t.add_argument_group("what to look at")
    g.add_argument("--category", "-c", nargs="+", choices=sorted(CATEGORIES), metavar="NAME",
                   help="one or more of: " + ", ".join(sorted(CATEGORIES)))
    g.add_argument("--category-name", nargs="+", metavar="TEXT", help="exact Kalshi category name, for one not in the list above")
    g.add_argument("--skip-category", nargs="+", choices=sorted(CATEGORIES), metavar="NAME",
                   help="leave these categories out, e.g. --skip-category sports crypto")
    g.add_argument("--all", action="store_true", help="every category (this is the default when --category is not given)")
    g.add_argument("--search", "-s", nargs="+", metavar="WORD", help="keywords; a market must contain all of them")
    g.add_argument("--any-word", action="store_true", help="with --search: match any keyword instead of all")
    g.add_argument("--exclude", "-x", nargs="+", metavar="WORD", help="drop markets containing any of these words")
    g.add_argument("--series", nargs="+", metavar="TICKER", help="only these series tickers, e.g. KXFED")
    g = t.add_argument_group("price (cents, what it costs to buy right now)")
    g.add_argument("--under", type=float, metavar="CENTS", help="price at or below this, e.g. --under 10")
    g.add_argument("--over", type=float, metavar="CENTS", help="price at or above this")
    g.add_argument("--min-price", type=float, metavar="CENTS")
    g.add_argument("--max-price", type=float, metavar="CENTS")
    g.add_argument("--side", choices=["yes", "no", "either"], default="either",
                   help="which contract the price filter applies to (default: either)")
    g = t.add_argument_group("activity")
    g.add_argument("--min-volume-24h", type=float, default=0, metavar="N", help="at least N contracts traded in the last 24h")
    g.add_argument("--min-volume", type=float, default=0, metavar="N", help="at least N contracts traded all time")
    g.add_argument("--min-interest", type=float, default=0, metavar="N", help="at least N contracts currently held")
    g.add_argument("--min-move", type=float, metavar="CENTS", help="price moved at least this much since yesterday")
    g.add_argument("--max-spread", type=float, metavar="CENTS", help="bid-ask gap no wider than this")
    g.add_argument("--min-spread", type=float, metavar="CENTS", help="bid-ask gap at least this wide")
    g = t.add_argument_group("timing")
    g.add_argument("--closes-within", type=float, metavar="DAYS", help="closes in the next N days (0.5 = 12 hours)")
    g.add_argument("--closes-after", type=float, metavar="DAYS", help="closes more than N days from now")
    g.add_argument("--opened-within", type=float, metavar="DAYS", help="opened in the last N days")
    g = t.add_argument_group("ranking and output")
    g.add_argument("--preset", "-p", choices=sorted(PRESETS), help="a ready-made filter bundle (listed below)")
    g.add_argument("--sort", choices=sorted(SORTS), help="how to rank (default: hot)")
    g.add_argument("--top", "-n", type=int, default=10, metavar="N", help="how many to show: 5, 10, 20, 100... (default 10)")
    g.add_argument("--one-per-event", action="store_true", help="show only the best market from each event")
    g.add_argument("--no-links", action="store_true", help="hide the ticker/link line under each market")
    g.add_argument("--json", action="store_true", help="print JSON instead of a table")
    g.add_argument("--csv", metavar="FILE", help="also save the results to a CSV file")
    g.add_argument("--open", type=int, default=0, metavar="N", help="open the first N results in your browser")
    t.set_defaults(func=cmd_top)

    s = sub.add_parser("show", help="one market in detail: prices, fee, break-even, rules, order book, link")
    s.add_argument("ticker")
    s.add_argument("--depth", type=int, default=5, help="order book levels per side (default 5)")
    s.add_argument("--full-rules", action="store_true", help="also print the secondary rules")
    s.add_argument("--open", action="store_true", help="open the page in your browser")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("event", help="every market inside one event, with the prices added up")
    s.add_argument("event_ticker")
    s.set_defaults(func=cmd_event)

    s = sub.add_parser("watch", help="re-check markets on a timer and print price changes")
    s.add_argument("tickers", nargs="+")
    s.add_argument("--every", type=int, default=60, metavar="SECONDS", help="seconds between checks (minimum 10)")
    s.add_argument("--count", type=int, default=0, metavar="N", help="stop after N checks (default: run until Ctrl+C)")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("open", help="open a market's kalshi.com page in your browser")
    s.add_argument("ticker")
    s.set_defaults(func=cmd_open)

    sub.add_parser("account", help="cash balance and open positions (needs API key env vars)").set_defaults(func=cmd_account)
    return p


def main(argv=None):
    # Market titles can contain characters an old Windows console cannot print.
    # Replace those instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except urllib.error.HTTPError as err:
        hint = {401: "Kalshi rejected the API key. Check KALSHI_API_KEY_ID and the .pem file.",
                404: "Nothing found with that ticker. Tickers are case-insensitive but must be exact."}.get(err.code, "")
        sys.exit(f"Kalshi API error {err.code}. {hint}")
    except urllib.error.URLError as err:
        sys.exit(f"Could not reach Kalshi: {err.reason}")
    except BrokenPipeError:
        pass                                       # output was piped to something that closed early


if __name__ == "__main__":
    main()
