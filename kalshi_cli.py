#!/usr/bin/env python3
"""
Kalshi CLI (kalshi_cli.py) - one tool for Kalshi: find markets, find an edge, decide, trade, keep score.

Run it in a terminal, or run `serve` for the same thing as a dashboard in your
browser. Both show identical output, because the dashboard runs these commands.

FIND
  categories        what is open, by category
  top               screen every open market by category, price, volume,
                    keyword, closing time, movement. Top 5 / 10 / 20 / 100.
  show / event      one market or one event in detail, live
  analyze           price history, statistics and a text chart; --deep drills
                    into the order book and recent trades
  ladder            an event's prices as a probability distribution
  watch / open      follow prices on a timer; open the kalshi.com page

FIND AN EDGE
  anomalies         prices that break arithmetic (outcome sets, threshold
                    ladders) or break their own pattern (moves, volume, quotes)
  best [N]          the top-scoring market, or the top 3 / 5 / 10; --by odds
                    ranks by the best chance of winning instead
  grind             compound a small stake through a chain of likely winners,
                    with the honest odds of the whole chain
  multiply          turn $10 / $20 / $100 into 2x / 3x / 5x / 10x inside a week:
                    a single bet, a chain of bets, or a spread of bets, with
                    the honest odds of each and the orders to place.
                    --sweep runs 16 variations into one text file
  picks             a diversified random draw from the best, with basket odds

DECIDE
  brief             a research prompt to give an AI assistant
  plan              your probability in; edge, Kelly size, checklist, order out
  fee               fee calculator
  watchlist         markets you are following
  daily             watchlist + anomalies + top picks + scorecard in one run

TRADE (optional; needs your API key)
  buy / sell        limit orders. A DRY RUN unless you add --live and type the
                    confirmation word. Capped per order.
  orders / cancel   see and cancel resting orders
  positions         cash balance and what you hold

KEEP SCORE
  log init / add / settle / sold / stats / fills
                    the trade log and the scorecard against the guide's rules

HELP
  firehose          run EVERY command and bind the output into one color PDF
  examples          about 90 example commands with what each does
  serve             the dashboard, at http://127.0.0.1:8765

FROM FINDING TO BUYING
  Every market has a unique ticker, shown under each row with its kalshi.com
  link. After any list, the row number works in place of the ticker:
      python kalshi_cli.py best 3
      python kalshi_cli.py analyze 1 --deep
      python kalshi_cli.py brief 1 --copy          (ask an AI, get a probability)
      python kalshi_cli.py plan 1 --my-prob 78 --bankroll 500
      python kalshi_cli.py buy 1                   (dry run)
      python kalshi_cli.py buy 1 --live            (real order, type BUY)
      python kalshi_cli.py log add ...             (the line is printed for you)

WHAT THIS TOOL CANNOT DO
  It cannot predict outcomes. The scores and estimates are arithmetic on
  prices plus one stated assumption. An edge exists only when your own
  probability is better than the market's, and the log is how you find out.

SAFETY
  * Nothing is ever sent without --live plus a typed confirmation.
  * Limit orders only. Buys are post-only unless you add --take.
  * Sells are checked against what you hold. Per-order cap:
    KALSHI_MAX_ORDER_USD (default $25).
  * --demo points everything at Kalshi's practice exchange.
  * The dashboard listens on this computer only and refuses live orders
    unless started with --allow-live.
  * The trading commands have been tested against a stand-in server, not a
    funded account. Try `--demo`, or one small order, before relying on them.

REQUIREMENTS
  * Python 3.9+. Standard library only for everything except trading.
  * Trading and account commands need an API key and:  python -m pip install -r requirements.txt

API KEY AS ENVIRONMENT VARIABLES (Windows PowerShell)
      setx KALSHI_API_KEY_ID "your-key-id"
      setx KALSHI_PRIVATE_KEY_PATH "C:\\path\\to\\kalshi-key.pem"
  Open a new window afterwards. Other settings: KALSHI_LOG (trade log path),
  KALSHI_MAX_ORDER_USD (order cap), KALSHI_HOME (where the watchlist lives).

COLOR
  On when printing to a terminal. --color never, or NO_COLOR=1, turns it off.
"""
import argparse
import base64
import csv
import datetime as dt
import html
import io
import json
import math
import os
import random
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zlib

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

# API root. Override with the KALSHI_API_BASE environment variable if Kalshi moves it.
BASE = os.environ.get("KALSHI_API_BASE", "https://external-api.kalshi.com/trade-api/v2")
DEMO_BASE = "https://external-api.demo.kalshi.co/trade-api/v2"   # Kalshi's practice exchange, used with --demo

# Where the downloaded market list is kept between runs, so the second query
# is instant instead of another full download.
CACHE_FILE = os.path.join(tempfile.gettempdir(), "kalshi_cli_markets_v2.json")

# Short names you type -> the category names Kalshi uses.
# `python kalshi_cli.py categories` shows the live list with counts.
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
# Color and drawing helpers
# ----------------------------------------------------------------------------
# Colors are plain ANSI escape codes, so nothing needs installing. They switch
# off by themselves when the output is piped to a file, when the NO_COLOR
# environment variable is set, or with --color never.

COLOR_ON = False          # set by setup_color()
UNICODE_ON = False        # True when the terminal can print block characters

ANSI = {"bold": "1", "dim": "2", "ul": "4", "inv": "7",
        "red": "91", "green": "92", "yellow": "93", "blue": "94",
        "magenta": "95", "cyan": "96", "white": "97", "gray": "90"}


def setup_color(mode="auto"):
    """Decide whether to use color and block characters. mode: auto, always or never."""
    global COLOR_ON, UNICODE_ON
    if mode == "always":
        COLOR_ON = True
    elif mode == "never" or os.environ.get("NO_COLOR"):
        COLOR_ON = False
    else:
        COLOR_ON = sys.stdout.isatty()
    if COLOR_ON and os.name == "nt":
        # Older Windows consoles ignore ANSI codes until this switch is turned on.
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            handle = kernel.GetStdHandle(-11)                 # -11 = standard output
            mode_now = ctypes.c_uint()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode_now)):
                kernel.SetConsoleMode(handle, mode_now.value | 0x0004)   # enable ANSI processing
        except Exception:
            pass
    try:
        "█░─▲▼".encode(sys.stdout.encoding or "ascii")
        UNICODE_ON = True
    except (UnicodeError, LookupError):
        UNICODE_ON = False


def paint(text, *styles):
    """Wrap text in the given styles, e.g. paint('WIN', 'green', 'bold'). Plain text when color is off."""
    text = str(text)
    if not COLOR_ON or not styles:
        return text
    return "\033[" + ";".join(ANSI[s] for s in styles) + "m" + text + "\033[0m"


def cell(text, width, *styles, align="<"):
    """Pad to a fixed width FIRST, then color, so the invisible color codes never break alignment."""
    return paint(f"{str(text):{align}{width}}", *styles)


def bar(fraction, width, *styles):
    """A horizontal bar: filled part in color, the rest in gray. fraction is 0.0 to 1.0."""
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    full, empty = ("█", "░") if UNICODE_ON else ("#", ".")
    return paint(full * filled, *styles) + paint(empty * (width - filled), "gray")


def rule(width):
    """A horizontal divider line."""
    return paint(("─" if UNICODE_ON else "-") * width, "gray")


def banner(name, text=""):
    """The title strip at the top of each command's output."""
    return "\n" + paint(f" {name} ", "bold", "cyan", "inv") + ("  " + text if text else "")


def signed_money(value):
    """+$12.34 in green, -$5.00 in red."""
    text = f"{'+' if value >= 0 else '-'}${abs(value):,.2f}"
    return paint(text, "green" if value > 0 else "red" if value < 0 else "gray", "bold")


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
            sys.exit("Signing requests needs the cryptography package:  python -m pip install -r requirements.txt")
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
        headers = {"Accept": "application/json", "User-Agent": "kalshi-cli/1.0"}
        if auth:
            headers.update(sign_headers(auth[0], auth[1], "GET", url))
        request = urllib.request.Request(url + ("?" + query if query else ""), headers=headers)
        _last_call = time.time()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504):  # rate limit or a server hiccup: wait, then retry
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 5:                           # a dropped connection: retry a few times, then give up
                raise
            time.sleep(1.5 * (attempt + 1))
    sys.exit("Kalshi kept refusing the request. Wait a minute and try again.")


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
        "event_title": event.get("title") or "",
        "label": label,                                   # which outcome this market is, e.g. "Above 3.6%"
        "mx": bool(event.get("mutually_exclusive")),      # True when only one market in the event can resolve YES
        "strike_type": market.get("strike_type") or "",   # greater / less / between ... for threshold ladders
        "floor": market.get("floor_strike"),              # the "above this number" threshold, when there is one
        "cap": market.get("cap_strike"),                  # the "below this number" threshold, when there is one
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

# One color per category so the eye can group rows without reading the label.
CATEGORY_COLORS = {
    "Sports": "yellow", "Economics": "green", "Financials": "green", "Politics": "magenta",
    "Elections": "magenta", "Crypto": "yellow", "Commodities": "yellow", "Climate and Weather": "cyan",
    "Science and Technology": "blue", "AI": "blue", "Entertainment": "magenta", "Companies": "blue",
    "Mentions": "cyan",
}


def category_tag(name):
    """The category name in its own color."""
    return paint(name or "Uncategorized", CATEGORY_COLORS.get(name, "gray"))


def side_style(side):
    """YES is green, NO is magenta, everywhere in the output."""
    return ("green", "bold") if side == "YES" else ("magenta", "bold")


def print_table(rows, show_links=True):
    """
    The results table. Line 1 of each market is the numbers and the title;
    line 2 (dimmer) is the ticker, category and link.
    """
    width = shutil.get_terminal_size((120, 30)).columns
    fixed = 3 + 1 + 9 + 1 + 9 + 1 + 5 + 1 + 4 + 1 + 6 + 1 + 7 + 1 + 6 + 1 + 8 + 1 + 5 + 2
    title_w = max(28, min(90, width - fixed - 1))
    top_vol = max((m["vol24"] for m in rows), default=0) or 1
    head = (f"{'#':>3} {'BUY':<9} {'YES b/a':<9} {'CHG':>5} {'SPRD':>4} {'PAYS':>6} "
            f"{'24H VOL':>7} {'':<6} {'OPEN INT':>8} {'ENDS':>5}  MARKET")
    print(paint(head, "bold", "cyan"))
    print(rule(min(width - 1, fixed + title_w)))
    for i, m in enumerate(rows, 1):
        price = m.get("buy_price")
        # BUY: the side and price you would pay right now.
        buy = cell(f"{m['buy_side']} {price_text(price)}c", 9, *side_style(m["buy_side"])) if price is not None \
            else cell("-", 9, "gray")
        book = cell(f"{price_text(m['yes_bid'])}/{price_text(m['yes_ask'])}", 9)
        # CHG: green when the price rose since yesterday, red when it fell.
        change = m["change"]
        chg = cell("" if not change else f"{change:+g}", 5, *(("green",) if (change or 0) > 0 else ("red",)), align=">")
        spread = cell(price_text(m["spread"]), 4, *(("yellow",) if (m["spread"] or 0) >= 5 else ()), align=">")
        # PAYS: what $1 becomes if this side wins, before fees. Big multiples stand out.
        pays = cell(f"{100 / price:.1f}x", 6, *(("yellow", "bold") if 100 / price >= 5 else ()), align=">") if price \
            else cell("-", 6, "gray", align=">")
        # Volume gets a small bar, scaled by square root so mid-sized markets still show.
        vol = cell(short_number(m["vol24"]), 7, "bold", align=">")
        vol_bar = bar(math.sqrt(m["vol24"] / top_vol), 6, "cyan")
        interest = cell(short_number(m["oi"]), 8, "gray", align=">")
        # ENDS: red inside 24 hours, yellow inside a week.
        hours = m["hours_left"]
        ends_style = ("red", "bold") if hours is not None and hours < 24 else ("yellow",) if hours is not None and hours < 168 else ()
        ends = cell(time_left(hours), 5, *ends_style, align=">")
        # Title: the question in bold, the outcome label [in brackets] in cyan.
        title = m["title"] if len(m["title"]) <= title_w else m["title"][:title_w - 3] + "..."
        cut = title.rfind(" [")
        title = paint(title, "bold") if cut <= 0 else paint(title[:cut], "bold") + paint(title[cut:], "cyan")
        print(f"{cell(i, 3, 'gray', align='>')} {buy} {book} {chg} {spread} {pays} {vol} {vol_bar} {interest} {ends}  {title}")
        if show_links:
            print(f"    {paint(m['ticker'], 'cyan')}  {category_tag(m['category'])}  {paint(m['url'], 'blue', 'ul')}")


def write_csv(rows, path):
    """Save the result set as a CSV you can open in Excel."""
    cols = ["ticker", "event", "series", "category", "title", "buy_side", "buy_price", "yes_bid", "yes_ask",
            "no_bid", "no_ask", "last", "change", "spread", "vol24", "vol", "oi", "close_time", "url"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(paint("Saved ", "green", "bold") + f"{len(rows)} rows to {path}")


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

def cmd_categories(a):
    """List every category that has open markets, with counts, a volume bar and the short name to type."""
    rows = add_clock_fields(load_markets(a.cache_minutes, a.refresh))
    stats = {}
    for m in rows:
        s = stats.setdefault(m["category"], {"markets": 0, "events": set(), "vol24": 0.0})
        s["markets"] += 1
        s["events"].add(m["event"])
        s["vol24"] += m["vol24"]
    short = {v: k for k, v in CATEGORIES.items()}
    total = sum(s["vol24"] for s in stats.values()) or 1
    biggest = max(s["vol24"] for s in stats.values()) or 1
    print(banner("KALSHI", paint(f"{len(rows):,} open markets in {len(stats)} categories", "bold")))
    print()
    print(paint(f"{'TYPE THIS':<15} {'CATEGORY':<24} {'EVENTS':>7} {'MARKETS':>8} {'24H VOL':>8}  SHARE OF 24H VOLUME", "bold", "cyan"))
    print(rule(96))
    for name, s in sorted(stats.items(), key=lambda kv: -kv[1]["vol24"]):
        flag = short.get(name, "(see below)")
        share = s["vol24"] / total
        print(f"{cell(flag, 15, 'cyan', 'bold')} {cell(name or 'Uncategorized', 24, CATEGORY_COLORS.get(name, 'gray'))} "
              f"{len(s['events']):>7,} {s['markets']:>8,} {cell(short_number(s['vol24']), 8, 'bold', align='>')}  "
              f"{bar(math.sqrt(s['vol24'] / biggest), 24, 'cyan')} {paint(f'{share:5.1%}', 'gray')}")
    ranked = sorted(stats.items(), key=lambda kv: -kv[1]["vol24"])
    lead, lead_stats = ranked[0]
    others = [name for name, _ in ranked[1:4] if name]
    quiet = sum(1 for _, s in ranked if s["vol24"] < 1000)
    plain_words([
        f"{lead} is {lead_stats['vol24'] / total:.0%} of all trading in the last 24 hours, with {lead_stats['markets']:,} open markets.",
        f"After that, the busiest are {', '.join(others)}." if others else "",
        f"{quiet} categories traded fewer than 1,000 contracts today. Thin markets are hard to get in and out of." if quiet else "",
        "Volume shows where prices are most reliable and fills are easiest, not where the best bets are.",
    ])
    extra = [n for n in stats if n and n not in short]
    if extra:
        print(paint("\nNot in the short list; use --category-name \"" + '" or "'.join(extra) + "\"", "gray"))
    print("\n" + paint("Next: ", "gray") + paint("python kalshi_cli.py top --category economics --top 10", "bold"))


def describe_filters(a):
    """A one-line summary of the filters in force, so the result is never a mystery."""
    parts = []
    if a.search:
        parts.append(("any of " if a.any_word else "") + "\"" + " ".join(a.search) + "\"")
    if a.exclude:
        parts.append("not " + ", ".join(a.exclude))
    if a.skip_category:
        parts.append("no " + ", ".join(a.skip_category))
    if a.min_price is not None or a.max_price is not None:
        lo = "1" if a.min_price is None else f"{a.min_price:g}"
        hi = "99" if a.max_price is None else f"{a.max_price:g}"
        parts.append(f"{a.side} price {lo}-{hi}c")
    for label, value in (("24h vol >=", a.min_volume_24h), ("volume >=", a.min_volume), ("open int >=", a.min_interest),
                         ("move >=", a.min_move), ("spread <=", a.max_spread), ("spread >=", a.min_spread)):
        if value:
            parts.append(f"{label} {value:g}")
    for label, value in (("closes within", a.closes_within), ("closes after", a.closes_after), ("opened within", a.opened_within)):
        if value is not None:
            parts.append(f"{label} {value:g}d")
    if a.one_per_event:
        parts.append("one per event")
    return "  |  ".join(parts)


def cmd_top(a):
    """The screener: filter, sort, cut to the top N, print."""
    # 1. A preset fills in any option the user left at its default.
    if a.preset:
        for key, value in PRESETS[a.preset].items():
            if getattr(a, key) in (None, 0, 0.0):
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
    save_last(found)

    # 3. Output. --format json/csv/markdown replaces the table; --out sends it to a file.
    fmt = "json" if a.json else a.format
    if emit(found, LIST_COLUMNS, fmt, a.out):
        pass
    else:
        scope = ", ".join(CATEGORIES[c] for c in a.category) if a.category else "all categories"
        print(banner("KALSHI", paint(f"top {len(found)} of {total:,}", "bold") + paint(f"  in {scope}", "gray")))
        print(paint("sorted by: ", "gray") + SORTS[a.sort][0]
              + (paint("   preset: ", "gray") + a.preset if a.preset else ""))
        filters = describe_filters(a)
        if filters:
            print(paint("filters:   ", "gray") + filters)
        print()
        if found:
            print_table(found, show_links=not a.no_links)
            plain_words(describe_rows(found))
        else:
            print(paint("Nothing matched.", "yellow", "bold") + " Loosen a filter, or run `categories` to see what is open.")
        if a.max_price is not None and a.max_price < 15 and found:
            print("\n" + paint("Note:", "yellow", "bold") + " contracts under 15 cents win less often than their price "
                  "implies. The guide says to skip them.")
        if found:
            print("\n" + paint("Next: ", "gray") + paint("show 1", "bold") + paint("  or  ", "gray") + paint("buy 1 --side yes --price N --count N", "bold")
                  + paint("  (row numbers work in place of tickers)", "gray"))
    if a.csv:
        write_csv(found, a.csv)
    for m in found[: a.open]:
        webbrowser.open(m["url"])


def cmd_show(a):
    """Everything about one market: prices on both sides, fee, break-even, rules, order book, link."""
    a.ticker = resolve(a.ticker)[0]
    market = api_get("/markets/" + urllib.parse.quote(a.ticker.upper()))["market"]
    try:
        event = api_get("/events/" + urllib.parse.quote(market["event_ticker"]))["event"]
    except Exception:
        event = {"event_ticker": market.get("event_ticker", ""), "series_ticker": market.get("event_ticker", "").split("-")[0]}
    m = add_clock_fields([flatten(event, market)])[0]

    print(banner("KALSHI", paint("market detail", "gray")))
    print("\n" + paint(m["title"], "bold", "white"))
    print(f"{paint(m['ticker'], 'cyan')}  {category_tag(m['category'])}  "
          f"{paint('status ' + str(market.get('status')), 'gray')}  "
          f"ends in {paint(time_left(m['hours_left']), 'bold')} {paint('(' + m['close_time'] + ')', 'gray')}")
    print(paint(m["url"], "blue", "ul"))

    # A single bar showing where the market puts the odds: green = YES, magenta = NO.
    mid = None
    if m["yes_bid"] is not None and m["yes_ask"] is not None:
        mid = (m["yes_bid"] + m["yes_ask"]) / 2
    elif m["last"] is not None:
        mid = m["last"]
    if mid is not None:
        width = 50
        filled = int(round(mid / 100 * width))
        full = "█" if UNICODE_ON else "#"
        print(f"\n  {paint(f'YES {mid:.0f}%', 'green', 'bold')} "
              f"{paint(full * filled, 'green')}{paint(full * (width - filled), 'magenta')} "
              f"{paint(f'NO {100 - mid:.0f}%', 'magenta', 'bold')}")

    print("\n" + paint(f"  {'SIDE':<5} {'BID':>6} {'ASK':>6} {'PAYS':>7} {'FEE':>7}   BREAK-EVEN", "bold", "cyan"))
    for side in ("yes", "no"):
        ask, bid = m[side + "_ask"], m[side + "_bid"]
        name = cell(side.upper(), 5, *side_style(side.upper()))
        if ask is None:
            print(f"  {name} {price_text(bid) + 'c':>6} {'-':>6}   " + paint("nobody is selling this side right now", "gray"))
            continue
        fee = taker_fee_cents(ask)
        print(f"  {name} {price_text(bid) + 'c':>6} {paint(f'{price_text(ask) + chr(99):>6}', 'bold')} {f'{100 / ask:.2f}x':>7} "
              f"{f'{fee:.2f}c':>7}   needs to be {paint(f'{ask + fee:.1f}%+', 'yellow', 'bold')} likely "
              + paint("(market order)", "gray"))

    moved = paint("no change", "gray") if not m["change"] else \
        paint(f"{m['change']:+g} since yesterday", "green" if m["change"] > 0 else "red")
    print(f"\n  last trade {paint(price_text(m['last']) + 'c', 'bold')} ({moved})   spread {price_text(m['spread'])}c   "
          f"24h volume {paint(short_number(m['vol24']), 'bold')}   open interest {short_number(m['oi'])}")

    reading = []
    if mid is not None:
        reading.append(f"The market puts YES at about {mid:.0f}%.")
    if m["yes_ask"] is not None:
        reading.append(f"$10 on YES at {m['yes_ask']:g}c buys {int(1000 // m['yes_ask'])} contracts and returns "
                       f"${int(1000 // m['yes_ask']):,} if YES wins.")
    if m["spread"] is not None:
        reading.append("A 1c gap means buying costs the taker fee; there is no room to rest an order inside it." if m["spread"] < 2 else
                       f"The {m['spread']:g}c gap leaves room to rest a limit order between the bid and the ask.")
    reading.append("Busy market: getting in and out should be easy." if m["vol24"] >= 10000 else
                   "Quiet market: an order may sit unfilled." if m["vol24"] < 500 else "")
    plain_words(reading)
    if market.get("rules_primary"):
        print("\n" + paint("RULES", "bold", "cyan"))
        print("  " + market["rules_primary"])
    if a.full_rules and market.get("rules_secondary"):
        print("  " + paint(market["rules_secondary"], "gray"))

    # The order book lists resting bids on each side, drawn as depth bars.
    try:
        book = api_get(f"/markets/{urllib.parse.quote(a.ticker.upper())}/orderbook", {"depth": a.depth})["orderbook_fp"]
        sides = {s: sorted(book.get(s + "_dollars") or [], key=lambda lv: -float(lv[0]))[: a.depth] for s in ("yes", "no")}
        deepest = max([float(q) for lv in sides.values() for _, q in lv] or [1])
        print("\n" + paint("ORDER BOOK", "bold", "cyan") + paint(f"  best {a.depth} resting bids per side", "gray"))
        for side in ("yes", "no"):
            if not sides[side]:
                print(f"  {cell(side.upper(), 4, *side_style(side.upper()))} " + paint("(empty)", "gray"))
            for n, (price, qty) in enumerate(sides[side]):
                name = cell(side.upper() if n == 0 else "", 4, *side_style(side.upper()))
                print(f"  {name} {float(price) * 100:>5g}c  {bar(float(qty) / deepest, 30, side_style(side.upper())[0])}  "
                      f"{short_number(float(qty)):>7}")
    except urllib.error.HTTPError as err:
        print(paint(f"\n(order book not available without an API key: HTTP {err.code})", "gray"))
    except Exception:
        pass
    print("\n" + paint("Next: ", "gray") + paint(f"analyze {m['ticker']}", "bold") + paint("  (price history and statistics)   ", "gray")
          + paint(f"buy {m['ticker']} --side yes --price N --count N", "bold") + paint("  (dry run)", "gray"))
    if a.open:
        webbrowser.open(m["url"])


def cmd_event(a):
    """List every market inside one event and add up the prices."""
    event = api_get("/events/" + urllib.parse.quote(a.event_ticker.upper()), {"with_nested_markets": "true"})["event"]
    rows = add_clock_fields([flatten(event, mk) for mk in event.get("markets") or []])
    for m in rows:
        pick_side(m, "yes", None, None) or m.update(buy_side="", buy_price=None)
    rows.sort(key=lambda m: -(m["yes_ask"] or m["last"] or 0))
    print(banner("KALSHI", paint(f"event with {len(rows)} markets", "gray")))
    print("\n" + paint(event.get("title") or "", "bold", "white") + "  " + category_tag(event.get("category")))
    print(paint(market_url(event.get("series_ticker", ""), event.get("event_ticker", "")), "blue", "ul") + "\n")
    print_table(rows, show_links=False)
    print("\n" + paint("Tickers: ", "gray") + "  ".join(paint(m["ticker"], "cyan") for m in rows))
    priced = [m for m in rows if m["yes_ask"] is not None or m["last"] is not None]
    if priced:
        lead = priced[0]
        chance = (lead["yes_bid"] + lead["yes_ask"]) / 2 if lead["yes_bid"] is not None and lead["yes_ask"] is not None else (lead["yes_ask"] or lead["last"])
        label = lead["title"][lead["title"].rfind("[") + 1:].rstrip("]") if "[" in lead["title"] else lead["title"]
        plain_words([
            f"The market's favorite is \"{label}\" at about {chance:.0f}%.",
            (f"Next is \"{priced[1]['title'][priced[1]['title'].rfind('[') + 1:].rstrip(']')}\" at about "
             f"{(priced[1]['yes_ask'] or priced[1]['last'] or 0):.0f}%.") if len(priced) > 1 else "",
            f"{sum(1 for m in rows if (m['yes_ask'] or 100) <= 5)} of the {len(rows)} outcomes are priced at 5c or less." if len(rows) > 2 else "",
        ])
    # When exactly one outcome can win, the YES prices should add up to about 100.
    # Asks adding to well over 100 is the cost of buying every outcome; bids adding
    # to over 100 would mean the market is paying you to sell every outcome.
    if event.get("mutually_exclusive"):
        asks = [m["yes_ask"] for m in rows if m["yes_ask"] is not None]
        bids = [m["yes_bid"] for m in rows if m["yes_bid"] is not None]
        tone = "green" if 95 <= sum(asks) <= 108 else "yellow"
        print(f"\nOnly one outcome can win. YES asks add up to {paint(f'{sum(asks):g}c', tone, 'bold')} across "
              f"{len(asks)} outcomes; YES bids add up to {paint(f'{sum(bids):g}c', 'bold')}. "
              + paint("Far from 100 means thin or stale prices.", "gray"))


def cmd_watch(a):
    """Re-check a few markets on a timer and print a line whenever a price moves."""
    every = max(10, a.every)
    a.tickers = [resolve(t)[0] for t in a.tickers]
    previous = {}
    up, down = ("▲", "▼") if UNICODE_ON else ("^", "v")
    print(banner("KALSHI", paint(f"watching {len(a.tickers)} market(s) every {every}s", "bold")
                 + paint("  Ctrl+C to stop", "gray")) + "\n")
    try:
        for _ in range(a.count) if a.count else iter(int, 1):
            for ticker in a.tickers:
                market = api_get("/markets/" + urllib.parse.quote(ticker.upper()))["market"]
                now = (cents(market.get("yes_bid_dollars")), cents(market.get("yes_ask_dollars")),
                       cents(market.get("last_price_dollars")))
                if previous.get(ticker) != now:
                    stamp = dt.datetime.now().strftime("%H:%M:%S")
                    arrow = ""
                    if ticker in previous:
                        # Compare the midpoint of bid and ask to say which way it moved.
                        old = [v for v in previous[ticker][:2] if v is not None]
                        new = [v for v in now[:2] if v is not None]
                        rose = sum(new) / max(1, len(new)) >= sum(old) / max(1, len(old))
                        arrow = "  " + (paint(up + " up", "green", "bold") if rose else paint(down + " down", "red", "bold"))
                    print(f"{paint(stamp, 'gray')}  {cell(ticker.upper(), 28, 'cyan')} "
                          f"YES bid {paint(price_text(now[0]) + 'c', 'bold')}  ask {paint(price_text(now[1]) + 'c', 'bold')}  "
                          f"last {price_text(now[2])}c{arrow}")
                    previous[ticker] = now
            time.sleep(every)
    except KeyboardInterrupt:
        print(paint("\nStopped.", "gray"))


def cmd_open(a):
    """Open a market's kalshi.com page in your browser."""
    a.ticker = resolve(a.ticker)[0]
    market = api_get("/markets/" + urllib.parse.quote(a.ticker.upper()))["market"]
    event_ticker = market["event_ticker"]
    try:
        series = api_get("/events/" + urllib.parse.quote(event_ticker))["event"]["series_ticker"]
    except Exception:
        series = event_ticker.split("-")[0]
    url = market_url(series, event_ticker)
    print(paint("Opening ", "gray") + paint(url, "blue", "ul"))
    webbrowser.open(url)


# ----------------------------------------------------------------------------
# Plain-words summaries, text charts and output formats
# ----------------------------------------------------------------------------

def median(values):
    """Middle value of a list (0 for an empty list)."""
    values = sorted(values)
    if not values:
        return 0.0
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0


def sparkline(values):
    """One row of bars of different heights, e.g. for daily volume."""
    if not values:
        return ""
    top = max(values) or 1
    marks = " ▁▂▃▄▅▆▇█" if UNICODE_ON else " ..::-=+#@"
    return "".join(marks[min(len(marks) - 1, int(round(v / top * (len(marks) - 1))))] for v in values)


def area_chart(values, height=8, width=60):
    """
    A filled chart drawn with block characters. Returns (rows, low, high):
    rows is a list of strings, top row first. Each value is stretched across
    several columns so a short history still fills the width.
    """
    low, high = min(values), max(values)
    if high - low < 2:                       # a flat line still needs some room to draw in
        low, high = low - 1, high + 1
    columns = [values[min(len(values) - 1, int(i * len(values) / width))] for i in range(width)]
    marks = " ▁▂▃▄▅▆▇█" if UNICODE_ON else " ...::###"
    rows = []
    for r in range(height - 1, -1, -1):
        line = ""
        for v in columns:
            eighths = (v - low) / (high - low) * height * 8 - r * 8      # how much of this row the bar fills
            # the bottom row always shows at least a sliver, so the lowest point is not a blank gap
            line += marks[int(max(1 if r == 0 else 0, min(8, round(eighths))))]
        rows.append(line)
    return rows, low, high


def plain_words(lines):
    """Print a short 'what this means' block under a table or chart."""
    lines = [ln for ln in lines if ln]
    if not lines:
        return
    print("\n" + paint("  IN PLAIN WORDS", "bold", "cyan"))
    for ln in lines:
        print("  " + paint("-", "gray") + " " + ln)


def describe_rows(rows):
    """A few sentences describing a list of markets: spread of prices, where the volume is, what is urgent."""
    if not rows:
        return []
    out = []
    cats = {}
    for m in rows:
        cats[m["category"]] = cats.get(m["category"], 0) + 1
    top_cat = max(cats, key=cats.get)
    out.append(f"{len(rows)} markets across {len(cats)} categor{'y' if len(cats) == 1 else 'ies'}"
               + (f"; {cats[top_cat]} are {top_cat or 'uncategorized'}." if len(cats) > 1 else f": {top_cat or 'uncategorized'}."))
    prices = [m["buy_price"] for m in rows if m.get("buy_price") is not None]
    if prices:
        cheap = sum(1 for p in prices if p < 15)
        out.append(f"The typical price is {median(prices):g}c (range {min(prices):g}c to {max(prices):g}c)."
                   + (f" {cheap} cost under 15c, which the guide treats as longshots." if cheap else ""))
    total = sum(m["vol24"] for m in rows)
    if total > 0:
        busiest = max(rows, key=lambda m: m["vol24"])
        out.append(f"{short_number(total)} contracts traded in 24 hours; {busiest['vol24'] / total:.0%} of that is in "
                   f"\"{busiest['title'][:48]}\".")
    urgent = [m for m in rows if m["hours_left"] is not None and m["hours_left"] < 24]
    if urgent:
        out.append(f"{len(urgent)} close{'s' if len(urgent) == 1 else ''} within 24 hours. Those may already be all but decided.")
    movers = [m for m in rows if m["change"]]
    if movers:
        big = max(movers, key=lambda m: abs(m["change"]))
        out.append(f"Biggest move since yesterday: {big['change']:+g}c on \"{big['title'][:48]}\".")
    spreads = [m["spread"] for m in rows if m["spread"] is not None]
    if spreads:
        wide = sum(1 for s in spreads if s >= 3)
        out.append(f"The typical bid-ask gap is {median(spreads):g}c."
                   + (f" {wide} have a gap of 3c or more, which leaves room to rest a limit order inside it." if wide else
                      " Gaps this tight mean buying costs the taker fee."))
    return out


LIST_COLUMNS = ["ticker", "title", "category", "buy_side", "buy_price", "yes_bid", "yes_ask", "no_bid", "no_ask",
                "last", "change", "spread", "vol24", "oi", "close_time", "url"]
PICK_COLUMNS = ["ticker", "title", "category", "buy_side", "limit_price", "count", "cost", "how", "mid", "est_prob",
                "edge", "score", "close_time", "url"]


def emit(rows, columns, fmt, out_path):
    """
    Write rows in a machine- or paste-friendly format: json, csv or markdown.
    Goes to the screen, or to a file with --out. Returns False for 'table'
    so the caller prints the normal colored table instead.
    """
    if fmt == "table":
        return False
    data = [{c: (round(m[c], 2) if isinstance(m.get(c), float) else m.get(c)) for c in columns} for m in rows]
    if fmt == "json":
        text = json.dumps(data, indent=2)
    elif fmt == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(data)
        text = buffer.getvalue().rstrip("\n")
    else:                                    # markdown: a table you can paste into notes or a GitHub issue
        clean = lambda v: "" if v is None else str(v).replace("|", "/")
        text = "\n".join(["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
                         + ["| " + " | ".join(clean(d[c]) for c in columns) + " |" for d in data])
    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text + "\n")
        print(paint("Saved ", "green", "bold") + f"{len(rows)} rows to {out_path} ({fmt})", file=sys.stderr)
    else:
        print(text)
    return True


# ----------------------------------------------------------------------------
# Analyze: the statistics and drill-down for one market
# ----------------------------------------------------------------------------

def candle_close(candle):
    """Closing YES price of one candle in cents. Falls back to the bid/ask midpoint when nothing traded."""
    price = cents((candle.get("price") or {}).get("close_dollars"))
    if price is None:
        bid = cents((candle.get("yes_bid") or {}).get("close_dollars"))
        ask = cents((candle.get("yes_ask") or {}).get("close_dollars"))
        if bid is not None and ask is not None:
            price = (bid + ask) / 2.0
    return price


def cmd_analyze(a):
    """Price history, statistics, a chart, and optional drill-down into the order book and recent trades."""
    ticker = resolve(a.market)[0]
    market = api_get("/markets/" + urllib.parse.quote(ticker))["market"]
    try:
        event = api_get("/events/" + urllib.parse.quote(market["event_ticker"]))["event"]
    except Exception:
        event = {"event_ticker": market.get("event_ticker", ""), "series_ticker": market.get("event_ticker", "").split("-")[0]}
    m = add_clock_fields([flatten(event, market)])[0]

    # --- history: one candle per day or per hour ---
    step = 60 if a.interval == "hour" else 1440
    days = min(a.days, 7) if a.interval == "hour" else a.days
    now = int(time.time())
    history = api_get(f"/series/{urllib.parse.quote(m['series'])}/markets/{urllib.parse.quote(ticker)}/candlesticks",
                      {"start_ts": now - int(days * 86400), "end_ts": now, "period_interval": step}).get("candlesticks") or []
    points = [(c["end_period_ts"], candle_close(c), num(c.get("volume_fp")), num(c.get("open_interest_fp"))) for c in history]
    points = [p for p in points if p[1] is not None]
    if len(points) < 3 and a.interval == "day":
        # A market only a day or two old has too few daily points; fall back to hourly ones.
        a.interval, step, days = "hour", 60, min(a.days, 7)
        history = api_get(f"/series/{urllib.parse.quote(m['series'])}/markets/{urllib.parse.quote(ticker)}/candlesticks",
                          {"start_ts": now - int(days * 86400), "end_ts": now, "period_interval": step}).get("candlesticks") or []
        points = [(c["end_period_ts"], candle_close(c), num(c.get("volume_fp")), num(c.get("open_interest_fp"))) for c in history]
        points = [p for p in points if p[1] is not None]
    closes = [p[1] for p in points]
    volumes = [p[2] for p in points]
    unit = "hour" if a.interval == "hour" else "day"

    stats = {"ticker": ticker, "title": m["title"], "url": m["url"], "interval": unit, "periods": len(closes)}
    if len(closes) >= 2:
        moves = [b - c for c, b in zip(closes, closes[1:])]
        mean_move = sum(moves) / len(moves)
        sigma = math.sqrt(sum((x - mean_move) ** 2 for x in moves) / len(moves))
        # Least-squares slope: the average drift per period, less jumpy than first-to-last.
        n = len(closes)
        x_mean, y_mean = (n - 1) / 2.0, sum(closes) / n
        slope = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(closes)) / sum((i - x_mean) ** 2 for i in range(n))
        biggest = max(range(len(moves)), key=lambda i: abs(moves[i]))
        high, low = max(closes), min(closes)
        recent, earlier = volumes[-3:], volumes[:-3]
        stats.update({
            "start": closes[0], "end": closes[-1], "change": closes[-1] - closes[0], "high": high, "low": low,
            "mean": y_mean, "typical_move": sigma, "trend_per_period": slope,
            "biggest_move": moves[biggest], "biggest_move_at": dt.datetime.fromtimestamp(points[biggest + 1][0]).strftime("%b %d %H:%M"),
            "up_periods": sum(1 for x in moves if x > 0), "down_periods": sum(1 for x in moves if x < 0),
            "position_in_range": (closes[-1] - low) / (high - low) if high > low else 0.5,
            "total_volume": sum(volumes), "avg_volume": sum(volumes) / len(volumes),
            "recent_vs_earlier_volume": (sum(recent) / len(recent)) / (sum(earlier) / len(earlier)) if earlier and sum(earlier) else None,
            "open_interest_start": points[0][3], "open_interest_end": points[-1][3],
        })

    # --- drill-down data ---
    book = trades = None
    if a.book or a.deep:
        try:
            raw = api_get(f"/markets/{urllib.parse.quote(ticker)}/orderbook", {"depth": 10})["orderbook_fp"]
            book = {s: sorted(raw.get(s + "_dollars") or [], key=lambda lv: -float(lv[0])) for s in ("yes", "no")}
            depth = {s: sum(float(q) for _, q in book[s]) for s in ("yes", "no")}
            stats["book_yes_depth"], stats["book_no_depth"] = depth["yes"], depth["no"]
        except Exception:
            book = None
    count = a.trades if a.trades else (15 if a.deep else 0)
    if count:
        try:
            trades = api_get("/markets/trades", {"ticker": ticker, "limit": min(1000, max(count, 100))}).get("trades") or []
            taker_yes = sum(num(t.get("count_fp")) for t in trades if (t.get("taker_outcome_side") or t.get("taker_side")) == "yes")
            total = sum(num(t.get("count_fp")) for t in trades)
            stats["recent_trades"] = len(trades)
            stats["taker_bought_yes_share"] = taker_yes / total if total else None
            stats["largest_recent_trade"] = max((num(t.get("count_fp")) for t in trades), default=0)
        except Exception:
            trades = None

    if a.format == "json":
        text = json.dumps({"stats": stats, "history": [{"time": p[0], "close": p[1], "volume": p[2]} for p in points]}, indent=2)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(text + "\n")
            print(paint("Saved ", "green", "bold") + a.out, file=sys.stderr)
        else:
            print(text)
        return

    # --- header ---
    print(banner("KALSHI", paint("analysis", "bold") + paint(f"  last {days:g} days, one point per {unit}", "gray")))
    print("\n" + paint(m["title"], "bold", "white"))
    print(f"{paint(ticker, 'cyan')}  {category_tag(m['category'])}  ends in {paint(time_left(m['hours_left']), 'bold')}"
          f"   YES bid {price_text(m['yes_bid'])}c / ask {price_text(m['yes_ask'])}c")
    print(paint(m["url"], "blue", "ul"))
    if len(closes) < 2:
        print("\n" + paint("Not enough price history to analyze.", "yellow", "bold") + " The market may be new or untraded.")
        return

    # --- chart ---
    rows, low, high = area_chart(closes, height=a.height, width=64)
    tone = "green" if stats["change"] >= 0 else "red"
    print("\n" + paint("  YES PRICE", "bold", "cyan") + paint("  cents", "gray"))
    for i, row in enumerate(rows):
        label = f"{high:>5.0f}" if i == 0 else f"{low:>5.0f}" if i == len(rows) - 1 else f"{(high + low) / 2:>5.0f}" if i == len(rows) // 2 else "     "
        print(f"  {paint(label, 'gray')} {paint('|', 'gray')}{paint(row, tone)}")
    first_day = dt.datetime.fromtimestamp(points[0][0]).strftime("%b %d")
    last_day = dt.datetime.fromtimestamp(points[-1][0]).strftime("%b %d")
    print(paint(f"        +{'-' * 64}", "gray"))
    print(paint(f"         {first_day}{' ' * (64 - len(first_day) - len(last_day))}{last_day}", "gray"))
    stretched = [volumes[min(len(volumes) - 1, int(i * len(volumes) / 64))] for i in range(64)]
    print(f"  {paint('  vol', 'gray')}  {paint(sparkline(stretched), 'cyan')}")

    # --- statistics ---
    s = stats
    label = lambda text: paint(f"  {text:<22}", "gray")
    print("\n" + paint("  STATISTICS", "bold", "cyan"))
    print(label("Start to now") + f"{s['start']:g}c to " + paint(f"{s['end']:g}c", "bold") + "   "
          + paint(f"{s['change']:+g}c", tone, "bold"))
    print(label("Range") + f"{s['low']:g}c to {s['high']:g}c   average {s['mean']:.1f}c   "
          f"now at {s['position_in_range']:.0%} of the range")
    print(label(f"Typical move per {unit}") + f"{s['typical_move']:.1f}c   "
          + paint(f"biggest {s['biggest_move']:+g}c ({s['biggest_move_at']})", "gray"))
    print(label("Trend") + paint(f"{s['trend_per_period']:+.2f}c per {unit}", "green" if s["trend_per_period"] > 0 else "red")
          + f"   up {s['up_periods']} {unit}s, down {s['down_periods']}, flat {len(closes) - 1 - s['up_periods'] - s['down_periods']}")
    print(label("Volume") + f"{short_number(s['total_volume'])} total   {short_number(s['avg_volume'])} per {unit}"
          + ("" if s["recent_vs_earlier_volume"] is None else
             paint(f"   last 3 {unit}s running at {s['recent_vs_earlier_volume']:.1f}x the earlier pace", "gray")))
    print(label("Open interest") + f"{short_number(s['open_interest_start'])} to {short_number(s['open_interest_end'])}")
    left = max(0.0, (m["hours_left"] or 0) / (1 if unit == "hour" else 24))
    if left > 0:
        # If the price kept wandering by its typical step with no drift, this is roughly how far it could go.
        wander = s["typical_move"] * math.sqrt(left)
        print(label("Room to wander") + f"about +/-{min(99, wander):.0f}c over the {left:.0f} {unit}s left "
              + paint("(typical move x square root of time; a scale, not a forecast)", "gray"))

    # --- drill-down: order book ---
    if book is not None:
        total = (stats["book_yes_depth"] + stats["book_no_depth"]) or 1
        deepest = max([float(q) for side in book.values() for _, q in side] or [1])
        print("\n" + paint("  ORDER BOOK", "bold", "cyan") + paint("  resting bids, best first", "gray"))
        for side in ("yes", "no"):
            for n, (price, qty) in enumerate(book[side][:6]):
                name = cell(side.upper() if n == 0 else "", 4, *side_style(side.upper()))
                print(f"  {name} {float(price) * 100:>5g}c  {bar(float(qty) / deepest, 30, side_style(side.upper())[0])}  {short_number(float(qty)):>7}")
        print(f"  {paint('Depth', 'gray')}  YES bids {short_number(stats['book_yes_depth'])} ({stats['book_yes_depth'] / total:.0%})"
              f"   NO bids {short_number(stats['book_no_depth'])} ({stats['book_no_depth'] / total:.0%})")

    # --- drill-down: recent trades ---
    if trades:
        print("\n" + paint("  RECENT TRADES", "bold", "cyan") + paint(f"  newest first, showing {min(count, len(trades))} of the last {len(trades)}", "gray"))
        print(paint(f"  {'TIME':<17} {'TAKER':<10} {'YES PRICE':>9} {'CONTRACTS':>10}", "bold", "cyan"))
        for t in trades[:count]:
            side = (t.get("taker_outcome_side") or t.get("taker_side") or "?").upper()
            when = (t.get("created_time") or "")[:16].replace("T", " ")
            print(f"  {cell(when, 17, 'gray')} {cell('bought ' + side, 10, *side_style(side))} "
                  f"{price_text(cents(t.get('yes_price_dollars'))) + 'c':>9} {num(t.get('count_fp')):>10,.0f}")

    # --- plain words ---
    words = []
    direction = "risen" if s["change"] > 0 else "fallen" if s["change"] < 0 else "not moved"
    words.append(f"The YES price has {direction} {abs(s['change']):g}c over {len(closes)} {unit}s and sits "
                 + ("near the top" if s["position_in_range"] > 0.8 else "near the bottom" if s["position_in_range"] < 0.2 else "in the middle")
                 + " of its range.")
    calm = "calm" if s["typical_move"] < 1 else "moderately active" if s["typical_move"] < 3 else "jumpy"
    words.append(f"It is {calm}: a typical {unit} moves it {s['typical_move']:.1f}c, and the biggest single move was {s['biggest_move']:+g}c.")
    if s["recent_vs_earlier_volume"] is not None:
        r = s["recent_vs_earlier_volume"]
        words.append("Trading has " + ("picked up sharply" if r > 1.5 else "slowed" if r < 0.67 else "held steady")
                     + f" lately ({r:.1f}x the earlier pace).")
    if m["yes_ask"] is not None:
        fee = taker_fee_cents(m["yes_ask"])
        words.append(f"Buying YES now at {m['yes_ask']:g}c only pays off if YES is at least {m['yes_ask'] + fee:.1f}% likely. "
                     "A past trend says nothing about that.")
    if stats.get("taker_bought_yes_share") is not None:
        share = stats["taker_bought_yes_share"]
        words.append(f"In the last {stats['recent_trades']} trades, {share:.0%} of the volume was people buying YES "
                     f"and {1 - share:.0%} buying NO.")
    if book is not None:
        lean = stats["book_yes_depth"] / ((stats["book_yes_depth"] + stats["book_no_depth"]) or 1)
        words.append(f"Resting orders lean {'toward YES' if lean > 0.55 else 'toward NO' if lean < 0.45 else 'neither way'} "
                     f"({lean:.0%} of bid depth is on YES).")
    fits = []
    best_price = max(m["yes_ask"] or 0, m["no_ask"] or 0)
    fits.append("price is in the 60-95c band" if 60 <= best_price <= 95 else "price is outside the guide's 60-95c band")
    days_left = (m["hours_left"] or 0) / 24
    fits.append("closes within 30 days" if days_left <= 30 else f"closes in {days_left:.0f} days, longer than the guide's 30")
    words.append("Against the guide: " + ", ".join(fits) + ".")
    plain_words(words)
    hints = [] if a.deep else ["analyze " + ticker + " --deep" + paint("  (adds order book and recent trades)", "gray")]
    hints.append("buy " + ticker + " --side yes --price N --count N" + paint("  (dry run)", "gray"))
    print("\n" + paint("Next: ", "gray") + ("\n      ").join(hints))


# ----------------------------------------------------------------------------
# Remembering the last list, so "#3" can stand in for a ticker
# ----------------------------------------------------------------------------
# After `top` or `picks` prints a numbered list, that list is saved. Any command
# that takes a ticker also takes a row number from it:  show 3   buy 3 ...
# The ticker is Kalshi's unique id for a market; the number is just a shortcut.

LAST_FILE = os.path.join(tempfile.gettempdir(), "kalshi_cli_last.json")


def save_last(rows):
    """Save the list just printed (ticker plus any suggested side, price and size)."""
    keep = ("ticker", "title", "url", "buy_side", "buy_price", "limit_price", "count", "est_prob")
    try:
        with open(LAST_FILE, "w", encoding="utf-8") as fh:
            json.dump([{k: m.get(k) for k in keep} for m in rows], fh)
    except OSError:
        pass


def resolve(ref):
    """
    Turn what the user typed into (ticker, saved_row). A plain number means
    "that row of the last list"; anything else is taken as a ticker.
    """
    if str(ref).isdigit():
        try:
            with open(LAST_FILE, encoding="utf-8") as fh:
                rows = json.load(fh)
            row = rows[int(ref) - 1]
            if int(ref) < 1:
                raise IndexError
        except (OSError, ValueError, IndexError):
            sys.exit(f"There is no row {ref} in the last list. Run `top` or `picks` first, or give a ticker.")
        print(paint(f"(row {ref} of the last list = {row['ticker']})", "gray"), file=sys.stderr)
        return row["ticker"].upper(), row
    return str(ref).upper(), {}


# ----------------------------------------------------------------------------
# Picks: a scored, diversified short list
# ----------------------------------------------------------------------------
# IMPORTANT, READ THIS: nothing here predicts the future. The "estimated
# probability" is the market's own price nudged by one well-documented pattern,
# the favorite-longshot bias: on Kalshi, cheap contracts have won less often
# than their price implies and expensive ones slightly more often. The nudge is
# small (about one point) and is an assumption, not a fitted model. The picks
# are a research short list. The guide still applies: only trade when YOUR own
# estimate differs from the price by 5 points or more.

# Nudge in percentage points at a given price, joined by straight lines.
# Example: a 85-cent contract is treated as an 86.3% chance.
BIAS_POINTS = [(1, -1.0), (5, -2.0), (15, -1.5), (30, -0.8), (50, 0.0), (70, 0.8), (85, 1.3), (95, 1.0), (99, 0.3)]


def estimated_probability(price_cents, strength=1.0):
    """The market price plus the favorite-longshot nudge. strength 0 = trust the price exactly."""
    points = BIAS_POINTS
    if price_cents <= points[0][0]:
        nudge = points[0][1]
    elif price_cents >= points[-1][0]:
        nudge = points[-1][1]
    else:
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= price_cents <= x1:
                nudge = y0 + (y1 - y0) * (price_cents - x0) / (x1 - x0)
                break
    return max(0.5, min(99.5, price_cents + strength * nudge))


def score_market(m, strength, max_spread=4):
    """
    Work out, for the better side of one market, a suggested limit price, an
    estimated probability, the expected profit per contract, and a 0-100 score.
    Returns None when the market is not worth listing.
    """
    best = None
    for side in ("yes", "no"):
        bid, ask = m[side + "_bid"], m[side + "_ask"]
        if bid is None or ask is None or ask <= bid:
            continue
        spread = ask - bid
        if spread > max_spread:
            continue                                   # a wide gap means nobody really knows the price
        mid = (bid + ask) / 2.0                        # the market's own midpoint for this side
        if spread >= 2:
            # Room inside the spread: rest a limit order one cent above the best bid.
            # A resting order pays no fee on most markets. It may not fill.
            # Being conservative: if someone does sell to you at your price, assume
            # your price was the fair one. A limit order earns nothing just by resting.
            limit, fee, how, fair = bid + 1, 0.0, "limit", bid + 1
        else:
            # One-cent spread: the only way in is to pay the ask, which costs the taker fee.
            limit, fee, how, fair = ask, taker_fee_cents(ask), "take", mid
        prob = estimated_probability(fair, strength)
        edge = prob - limit - fee                      # expected profit per contract, in cents
        edge_if_market_right = fair - limit - fee      # same, if the price is exactly right
        if best is None or edge > best["edge"]:
            best = dict(buy_side=side.upper(), buy_price=ask, limit_price=round(limit, 2), fee=fee, how=how,
                        est_prob=prob, fair=fair, mid=mid, edge=edge, edge_flat=edge_if_market_right)
    if best is None:
        return None
    # --- the score: expected return, scaled up for liquid markets that resolve soon ---
    ret = best["edge"] / best["limit_price"]                         # expected return on the money put in
    days = max(0.25, (m["hours_left"] or 24 * 365) / 24.0)
    liquidity = min(1.0, math.log10(1 + m["vol24"]) / 5.0)           # 100k contracts a day = full marks
    soon = 1.0 / (1.0 + days / 14.0)                                 # two weeks out = half marks
    steady = 1.0 if abs(m["change"] or 0) <= 5 else 0.6              # a big move today means news is still landing
    best["score"] = max(0.0, 100.0 * ret * 8.0) * (0.35 + 0.65 * liquidity) * (0.4 + 0.6 * soon) * steady
    best["days"] = days
    return best


def simulate_basket(picks, use_market_price, runs=20000, seed=1):
    """
    Play the basket out many times with random outcomes and report the spread
    of results in dollars. Each market is treated as independent, which is why
    picks are limited to one per event.
    """
    rng = random.Random(seed)
    results = []
    for _ in range(runs):
        total = 0.0
        for p in picks:
            chance = (p["fair"] if use_market_price else p["est_prob"]) / 100.0
            cost = p["count"] * (p["limit_price"] + p["fee"]) / 100.0
            total += p["count"] * 1.0 - cost if rng.random() < chance else -cost
        results.append(total)
    results.sort()
    return {"mean": sum(results) / runs, "median": results[runs // 2], "bad": results[int(runs * 0.05)],
            "good": results[int(runs * 0.95)], "win": sum(1 for r in results if r > 0) / runs}


def money_cell(value, width):
    """Signed dollars, right-aligned to a width, green or red."""
    text = f"{'+' if value >= 0 else '-'}${abs(value):,.2f}"
    return cell(text, width, "green" if value > 0 else "red" if value < 0 else "gray", align=">")


def cmd_picks(a):
    """Build a scored, diversified short list and show what the basket could do."""
    rows = add_clock_fields(load_markets(a.cache_minutes, a.refresh))
    wanted = {CATEGORIES[c].lower() for c in (a.category or [])}
    skipped = {CATEGORIES[c].lower() for c in (a.skip_category if a.skip_category is not None else ["sports", "crypto"])}
    words = [w.lower() for w in (a.search or [])]

    # 1. Screen: the guide's rules plus enough trading activity to get filled.
    pool = []
    for m in rows:
        category = m["category"].lower()
        if (wanted and category not in wanted) or (not wanted and category in skipped):
            continue
        if words and not all(w in f"{m['title']} {m['ticker']}".lower() for w in words):
            continue
        if m["hours_left"] is None or not (1 <= m["hours_left"] <= a.closes_within * 24):
            continue
        if m["vol24"] < a.min_volume_24h:
            continue
        scored = score_market(m, a.bias, a.max_spread)
        if not scored or not (a.min_price <= scored["limit_price"] <= a.max_price) or scored["score"] <= 0:
            continue
        pool.append({**m, **scored})
    # Rank by score (default), by the best odds of winning, or by what resolves first.
    rank = getattr(a, "by", "score")
    pool.sort(key=(lambda m: -m["est_prob"]) if rank == "odds" else (lambda m: m["hours_left"]) if rank == "soonest"
              else (lambda m: -m["score"]))

    # 2. Choose: one per event, at most two per category, so one surprise cannot sink the basket.
    #    By default the choice is a weighted draw from the best 60, so each run gives a
    #    different but still high-scoring set. --no-random takes the top scorers in order.
    rng = random.Random(a.seed)
    candidates = pool[:60]
    chosen, events, per_category = [], set(), {}
    while candidates and len(chosen) < a.top:
        if a.no_random:
            pick = candidates[0]
        else:
            pick = rng.choices(candidates, weights=[c["score"] for c in candidates])[0]
        candidates.remove(pick)
        if pick["event"] in events or per_category.get(pick["category"], 0) >= (99 if wanted else 2):
            continue
        events.add(pick["event"])
        per_category[pick["category"]] = per_category.get(pick["category"], 0) + 1
        chosen.append(pick)
    chosen.sort(key=(lambda m: -m["est_prob"]) if rank == "odds" else (lambda m: m["hours_left"]) if rank == "soonest"
                else (lambda m: -m["score"]))

    # 3. Size: the same share of the bankroll on each pick (the guide's 1% to 3% rule).
    for m in chosen:
        dollars = a.bankroll * a.risk / 100.0
        m["count"] = max(1, int(dollars // ((m["limit_price"] + m["fee"]) / 100.0)))
        m["cost"] = m["count"] * (m["limit_price"] + m["fee"]) / 100.0
    save_last(chosen)

    if emit(chosen, PICK_COLUMNS, "json" if a.json else a.format, a.out):
        return
    print(banner("KALSHI", paint(f"{len(chosen)} pick{'' if len(chosen) == 1 else 's'}", "bold")
                 + paint(f"  from {len(pool):,} markets that pass the screen", "gray")))
    print(paint("screen:    ", "gray") + f"limit price {a.min_price:g}-{a.max_price:g}c  |  closes within {a.closes_within:g}d  |  "
          f"24h vol >= {a.min_volume_24h:g}  |  spread <= {a.max_spread:g}c  |  " + ("only " + ", ".join(a.category) if a.category else "no " + ", ".join(sorted(skipped))))
    print(paint("selection: ", "gray") + (("top scores in order" if rank == "score" else "taken in ranked order") if a.no_random else
          f"weighted random draw from the best 60 (seed {a.seed if a.seed is not None else 'random'})")
          + "  |  one per event" + ("" if wanted else ", two per category")
          + ("" if rank == "score" else f"  |  ranked by {'best odds of winning' if rank == 'odds' else 'soonest to resolve'}"))
    if not chosen:
        print("\n" + paint("Nothing passed the screen.", "yellow", "bold") + " Widen --closes-within or lower --min-volume-24h.")
        return

    width = shutil.get_terminal_size((120, 30)).columns
    title_w = max(24, min(70, width - 76))
    print()
    print(paint(f"{'#':>3} {'ORDER':<17} {'HOW':<5} {'MKT':>5} {'EST':>6} {'EDGE':>6} {'COST':>8} {'ENDS':>5} {'SCORE':>5} {'':<8}  MARKET", "bold", "cyan"))
    print(rule(min(width - 1, 76 + title_w)))
    top_score = max(m["score"] for m in chosen)
    for i, m in enumerate(chosen, 1):
        order = cell(f"{m['count']} {m['buy_side']} @ {m['limit_price']:g}c", 17, *side_style(m["buy_side"]))
        how = cell(m["how"], 5, *(("green",) if m["how"] == "limit" else ("yellow",)))
        edge = cell(f"{m['edge']:+.1f}", 6, "green" if m["edge"] > 0 else "red", "bold", align=">")
        hours = m["hours_left"]
        ends = cell(time_left(hours), 5, *(("red", "bold") if hours < 24 else ("yellow",) if hours < 168 else ()), align=">")
        title = m["title"] if len(m["title"]) <= title_w else m["title"][:title_w - 3] + "..."
        cut = title.rfind(" [")
        title = paint(title, "bold") if cut <= 0 else paint(title[:cut], "bold") + paint(title[cut:], "cyan")
        cost_text = f"${m['cost']:.2f}"
        print(f"{cell(i, 3, 'gray', align='>')} {order} {how} {m['mid']:>4.0f}% {m['est_prob']:>5.1f}% {edge} "
              f"{cost_text:>8} {ends} {m['score']:>5.1f} {bar(m['score'] / top_score, 8, 'cyan')}  {title}")
        if not a.no_links:
            print(f"    {paint(m['ticker'], 'cyan')}  {category_tag(m['category'])}  {paint(m['url'], 'blue', 'ul')}")

    # 4. The basket as a whole, under two assumptions.
    total = sum(m["cost"] for m in chosen)
    chances = sorted((m["est_prob"] / 100.0 for m in chosen), reverse=True)
    model = simulate_basket(chosen, use_market_price=False)
    flat = simulate_basket(chosen, use_market_price=True)
    print("\n" + paint("  BASKET", "bold", "cyan") + paint(f"  {len(chosen)} positions, ${total:,.2f} at risk "
          f"({100 * total / a.bankroll:.1f}% of a ${a.bankroll:,.0f} bankroll, {a.risk:g}% each)", "gray"))
    print(paint(f"  {'IF...':<34} {'CHANCE UP':>9} {'AVERAGE':>9} {'TYPICAL':>9} {'BAD 5%':>9} {'GOOD 5%':>9}", "bold", "cyan"))
    for label, r in (("the bias pattern holds (EST)", model), ("the market price is right (MKT)", flat)):
        print(f"  {label:<34} {r['win']:>8.0%}  " + " ".join(money_cell(r[k], 9) for k in ("mean", "median", "bad", "good")))
    all_win = math.prod(chances)
    print(paint(f"  Chance every pick wins: {all_win:.0%}. One loss at these prices can wipe out several wins.", "gray"))

    print("\n" + paint("  READ THIS", "bold", "yellow"))
    print("  EST is the price plus a small nudge for a known pricing pattern. It is an assumption, not a forecast.")
    print("  If the market price is simply right, the average result is zero or a little worse, as the table shows.")
    print("  These are candidates to research. The guide's rule still stands: trade only when YOUR number")
    print("  differs from the price by 5 points or more. \"limit\" orders may never fill.")
    print("\n" + paint("Next: ", "gray") + paint("show 1", "bold") + paint("   analyze 1", "bold") + paint("   brief 1", "bold")
          + paint("   plan 1 --my-prob N", "bold") + paint("   buy 1", "bold") + paint("  (a dry run until you add --live)", "gray"))


# ----------------------------------------------------------------------------
# Trading (optional, off unless you ask for it)
# ----------------------------------------------------------------------------
# Every trading command is a DRY RUN by default: it shows exactly what would be
# sent and stops. To send a real order you must add --live AND type the
# confirmation word. There is also a per-order dollar cap.
#
# Safety rules built in:
#   * limit orders only, never market orders
#   * buys rest on the book by default (post-only) so they pay no fee and can
#     never fill at a worse price than you typed; add --take to cross the spread
#   * sells are checked against what you hold, so you never open the opposite side by accident
#   * orders above the cap are refused (KALSHI_MAX_ORDER_USD, default $25)
#   * a failed send is never retried automatically, so nothing is sent twice

DEFAULT_CAP = 25.0


def api_send(method, endpoint, body=None, query=None):
    """
    POST or DELETE, signed with your key. Unlike api_get this never retries:
    if the connection drops we do not know whether Kalshi received the order,
    and sending it again could place it twice.
    """
    key_id, private_key = load_auth(required=True)
    url = BASE + endpoint
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "kalshi-cli/1.0"}
    headers.update(sign_headers(key_id, private_key, method, url))      # the signature covers the path, not the query
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v})
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text.strip() else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:400]
        sys.exit(paint(f"Kalshi refused the request (HTTP {err.code}).", "red", "bold") + f" {detail}\nNothing was changed.")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
        sys.exit(paint("The connection dropped while sending.", "red", "bold") + f" ({err})\n"
                 "It is not known whether Kalshi received it. Run `orders` and `positions` to check before trying again.")


def confirm(word, a):
    """Ask the user to type a word before anything real happens. --yes skips the question."""
    if a.yes:
        return
    if not sys.stdin.isatty():
        sys.exit("Refusing to send without confirmation. Run it in a terminal, or add --yes.")
    typed = input(paint(f"Type {word} to send this for real, anything else to cancel: ", "yellow", "bold"))
    if typed.strip() != word:
        sys.exit("Cancelled. Nothing was sent.")


def order_cap():
    """The most dollars one order may cost. Change it with the KALSHI_MAX_ORDER_USD environment variable."""
    try:
        return float(os.environ.get("KALSHI_MAX_ORDER_USD", DEFAULT_CAP))
    except ValueError:
        return DEFAULT_CAP


def environment_tag():
    """Says, loudly, whether this is the practice exchange or real money."""
    if "demo" in BASE:
        return paint(" DEMO: practice money ", "bold", "cyan", "inv")
    if "kalshi.com" not in BASE:
        return paint(" TEST SERVER: not Kalshi ", "bold", "yellow", "inv")      # KALSHI_API_BASE points somewhere else
    return paint(" LIVE: real money ", "bold", "red", "inv")


def build_order(ticker, outcome, action, price_cents, count, post_only=False, immediate_reduce=False):
    """
    Build the request body for Kalshi's order endpoint (POST /portfolio/events/orders).

    Kalshi's API quotes every order on the YES side of the market:
        side "bid" = buy YES        side "ask" = sell YES
    A NO contract at P cents is the mirror image of YES at (100 - P) cents, so
        buy  NO at 40c  is sent as  ask (sell YES) at 60c
        sell NO at 40c  is sent as  bid (buy YES)  at 60c
    Counts and prices travel as strings: "10" contracts, "0.6100" dollars.
    """
    yes_price = price_cents if outcome == "yes" else 100 - price_cents
    buying_yes = (outcome == "yes") == (action == "buy")
    body = {
        "ticker": ticker,
        "side": "bid" if buying_yes else "ask",
        "count": str(int(count)),
        "price": f"{yes_price / 100.0:.4f}",
        # Rest on the book until filled or cancelled, unless told to fill now or not at all.
        "time_in_force": "immediate_or_cancel" if immediate_reduce else "good_till_canceled",
        # If the order would trade against one of your own, cancel the incoming one.
        "self_trade_prevention_type": "taker_at_cross",
        # A unique id per order, so Kalshi can reject an accidental duplicate.
        "client_order_id": str(uuid.uuid4()),
    }
    if post_only:
        body["post_only"] = True          # refuse to fill immediately; only ever rest on the book
    if immediate_reduce:
        body["reduce_only"] = True        # never sell more than you hold (Kalshi allows this only on fill-now orders)
    return body


def show_dry_run(body):
    """Print the request a --live run would send."""
    shown = {k: v for k, v in body.items() if k != "client_order_id"}
    print("\n" + paint("  DRY RUN", "bold", "cyan") + " Nothing was sent. This is what --live would send:")
    print(paint("  POST /portfolio/events/orders  " + json.dumps(shown), "gray"))
    print(paint('  (Kalshi quotes every order on the YES side: "bid" buys YES, "ask" sells YES; NO at P is YES at 100 - P.)', "gray"))


def cmd_buy(a):
    """Preview a limit buy, and send it only with --live plus a typed confirmation."""
    ticker, saved = resolve(a.market)
    market = api_get("/markets/" + urllib.parse.quote(ticker))["market"]
    if market.get("status") not in ("active", "open"):
        sys.exit(f"{ticker} is not open for trading (status: {market.get('status')}).")

    # --- fill in anything not typed from the saved pick, then from the live market ---
    side = (a.side or saved.get("buy_side") or "").lower()
    if side not in ("yes", "no"):
        sys.exit("Say which side to buy: --side yes or --side no.")
    bid = cents(market.get(side + "_bid_dollars"))
    ask = cents(market.get(side + "_ask_dollars"))
    price = a.price if a.price is not None else saved.get("limit_price")
    if price is None:
        price = min((bid or 0) + 1, (ask or 99) - 1) if (bid and ask and ask - bid >= 2) else None
    if price is None:
        sys.exit("Give a limit price in cents: --price 62.")
    price = int(round(price))
    if not 1 <= price <= 99:
        sys.exit("--price is in whole cents, 1 to 99.")
    if a.spend is not None:
        count = int(a.spend // (price / 100.0))
    else:
        count = a.count if a.count is not None else saved.get("count")
    if not count or count < 1:
        sys.exit("Say how many: --count 10, or --spend 15 for about $15 worth.")
    count = int(count)

    crosses = ask is not None and price >= ask           # would fill right away against a seller
    fee = taker_fee_cents(price) * count / 100.0 if crosses and a.take else 0.0
    cost = count * price / 100.0
    title = market.get("title") or market.get("yes_sub_title") or ticker

    print(banner("KALSHI", paint("buy order", "bold")) + "  " + environment_tag())
    print("\n" + paint(title, "bold", "white"))
    print(f"{paint(ticker, 'cyan')}   now: {side.upper()} bid {price_text(bid)}c / ask {price_text(ask)}c")
    print(f"\n  {paint(f'BUY {count} {side.upper()} @ {price}c', *side_style(side.upper()))}   limit order, good till cancelled"
          + ("" if a.take else paint("   post-only", "gray")))
    print(f"  cost {paint(f'${cost:,.2f}', 'bold')}   fee {'about $' + format(fee, '.2f') if fee else '$0.00 while it rests'}"
          f"   pays {paint(f'${count:,.2f}', 'bold')} if {side.upper()} wins   max loss ${cost + fee:,.2f}")

    # --- checks ---
    problems, warnings = [], []
    cap = a.max_cost if a.max_cost is not None else order_cap()
    if cost > cap:
        problems.append(f"Cost ${cost:,.2f} is over the ${cap:,.2f} per-order cap. Raise it with --max-cost or KALSHI_MAX_ORDER_USD.")
    if crosses and not a.take:
        problems.append(f"{price}c meets the ask ({price_text(ask)}c), so this would fill at once and pay the taker fee. "
                        f"Lower the price to rest on the book, or add --take to accept that.")
    if price < 15:
        warnings.append("Under 15 cents is a longshot. These lose the most on average.")
    if a.my_prob is not None:
        win_prob = a.my_prob if side == "yes" else 100 - a.my_prob
        edge = win_prob - price
        if edge < 5:
            warnings.append(f"Your edge is {edge:+g} pts. The guide says only trade at 5+ pts.")
        else:
            print(f"  your win probability {win_prob:g}%   edge {paint(f'{edge:+g} pts', 'green', 'bold')}")
    else:
        warnings.append("No --my-prob given, so your edge was not checked.")
    if a.bankroll:
        pct = 100 * cost / a.bankroll
        if pct > 3:
            warnings.append(f"Size is {pct:.1f}% of bankroll. The guide's limit is 3%.")
    for w in warnings:
        print("  " + paint("WARNING", "yellow", "bold") + " " + w)
    for p in problems:
        print("  " + paint("BLOCKED", "red", "bold") + " " + p)
    if problems:
        sys.exit(1)

    body = build_order(ticker, side, "buy", price, count, post_only=not a.take)
    if not a.live:
        show_dry_run(body)
        return
    confirm("BUY", a)
    report_order(api_send("POST", "/portfolio/events/orders", body), "placed")
    # Hand off to the trade log: the exact line to run once it has filled.
    prob = f" --my-prob {a.my_prob:g}" if a.my_prob is not None else " --my-prob YOUR_NUMBER"
    print("\n" + paint("When it fills, log it:", "gray"))
    print(f"  python kalshi_cli.py log add --ticker {ticker} --side {side} --price {price} --contracts {count}{prob}"
          + (" --order-type market" if crosses else "") + " --rules-read")


def cmd_sell(a):
    """Preview selling contracts you hold, and send only with --live plus a typed confirmation."""
    ticker, saved = resolve(a.market)
    market = api_get("/markets/" + urllib.parse.quote(ticker))["market"]
    side = (a.side or "").lower()
    if side not in ("yes", "no"):
        sys.exit("Say which contracts you are selling: --side yes or --side no.")
    bid = cents(market.get(side + "_bid_dollars"))
    ask = cents(market.get(side + "_ask_dollars"))
    if a.price is None:
        sys.exit(f"Give the lowest price you will accept, in cents: --price N. Best bid now is {price_text(bid)}c.")
    price, count = int(round(a.price)), int(a.count)
    if not 1 <= price <= 99 or count < 1:
        sys.exit("--price is whole cents 1 to 99, and --count is at least 1.")
    hits_bid = bid is not None and price <= bid
    fee = taker_fee_cents(price) * count / 100.0 if hits_bid else 0.0
    print(banner("KALSHI", paint("sell order", "bold")) + "  " + environment_tag())
    print("\n" + paint(market.get("title") or ticker, "bold", "white"))
    print(f"{paint(ticker, 'cyan')}   now: {side.upper()} bid {price_text(bid)}c / ask {price_text(ask)}c")
    print(f"\n  {paint(f'SELL {count} {side.upper()} @ {price}c', 'yellow', 'bold')}   limit order, "
          + ("fills now or not at all" if hits_bid else "good till cancelled"))
    print(f"  proceeds {paint(f'${count * price / 100.0:,.2f}', 'bold')}   fee "
          + (f"about ${fee:.2f} (fills now against the {price_text(bid)}c bid)" if hits_bid else "$0.00 while it rests"))

    # Never sell more than you hold: selling contracts you do not own would open
    # the opposite position. Check the account first whenever a key is available.
    if load_auth(required=a.live):
        data = api_get("/portfolio/positions", {"ticker": ticker, "limit": 50}, need_auth=True)
        held = sum(num(first(p, "position_fp", "position")) for p in (data.get("market_positions") or []) if p.get("ticker") == ticker)
        owned = held if side == "yes" else -held          # Kalshi reports NO holdings as a negative number
        print(f"  you hold {paint(f'{max(0.0, owned):g} {side.upper()}', 'bold')} in this market")
        if owned < count:
            print("  " + paint("BLOCKED", "red", "bold") + f" You hold {max(0.0, owned):g} {side.upper()} contracts, not {count}. "
                  "Selling more than you hold would open the opposite position.")
            sys.exit(1)
    else:
        print("  " + paint("WARNING", "yellow", "bold") + " No API key set, so your holdings were not checked.")
    # An order that fills right now can carry Kalshi's own reduce-only guard as well.
    body = build_order(ticker, side, "sell", price, count, immediate_reduce=hits_bid)
    if not a.live:
        show_dry_run(body)
        return
    confirm("SELL", a)
    report_order(api_send("POST", "/portfolio/events/orders", body), "placed")
    print("\n" + paint("When it fills, log it:", "gray") + f"\n  python kalshi_cli.py log sold --id LOG_ID --exit-price {price}")


def first(order, *names):
    """Kalshi has renamed some fields over time; return the first one present."""
    for name in names:
        if order.get(name) not in (None, ""):
            return order[name]
    return None


def report_order(order, verb):
    """Print what Kalshi said about an order."""
    filled = first(order, "fill_count", "fill_count_fp") or "0"
    remaining = first(order, "remaining_count", "remaining_count_fp") or "0"
    status = "filled" if num(remaining) == 0 and num(filled) > 0 else "resting on the book" if num(remaining) > 0 else "not filled"
    print("\n  " + paint(f"Order {verb}.", "green", "bold") + f"  id {paint(order.get('order_id', '?'), 'cyan')}"
          f"   {paint(status, 'bold')}   filled {filled}   resting {remaining}")
    if order.get("average_fill_price"):
        print(f"  average fill price ${order['average_fill_price']}   fee per contract ${order.get('average_fee_paid', '0')}")


def cmd_orders(a):
    """List your orders. Resting orders are the ones still waiting on the book."""
    data = api_get("/portfolio/orders", {"status": a.status, "limit": 200}, need_auth=True)
    orders = data.get("orders") or []
    print(banner("KALSHI", paint(f"{len(orders)} {a.status} order(s)", "bold")) + "  " + environment_tag() + "\n")
    if not orders:
        print(paint("None.", "gray"))
        return
    print(paint(f"{'#':>3} {'ORDER':<22} {'FILLED':>7} {'RESTING':>8} {'PLACED':<17} {'TICKER':<30} ORDER ID", "bold", "cyan"))
    print(rule(118))
    for i, o in enumerate(orders, 1):
        # Kalshi reports which outcome the order is positioned for. Selling YES and
        # buying NO are the same thing to the exchange, so both show as NO here.
        side = (first(o, "outcome_side", "side") or "?").upper()
        price = cents(o.get(side.lower() + "_price_dollars")) or first(o, side.lower() + "_price")
        placed = (o.get("created_time") or "")[:16].replace("T", " ")
        what = cell(f"{side} @ {price_text(price)}c", 22, *side_style(side))
        print(f"{cell(i, 3, 'gray', align='>')} {what} {str(first(o, 'fill_count_fp', 'fill_count') or 0):>7} "
              f"{str(first(o, 'remaining_count_fp', 'remaining_count') or 0):>8} {placed:<17} "
              f"{cell(o.get('ticker', '?'), 30, 'cyan')} {paint(o.get('order_id', ''), 'gray')}")
    print("\n" + paint("Selling YES shows as NO at the mirror price (and the reverse); the exchange treats them as the same order.", "gray"))
    print(paint("Cancel one with: ", "gray") + "cancel ORDER_ID --live")


def cmd_cancel(a):
    """Cancel one resting order, or all of them with --all."""
    if a.all:
        orders = api_get("/portfolio/orders", {"status": "resting", "limit": 200}, need_auth=True).get("orders") or []
        ids = [(o["order_id"], o.get("ticker", "")) for o in orders]
    else:
        if not a.order_id:
            sys.exit("Give an order id (see `orders`), or use --all.")
        # Kalshi needs the market ticker alongside the order id to find the order,
        # so look it up among your resting orders when a key is available.
        ticker = ""
        if load_auth(required=a.live):
            resting = api_get("/portfolio/orders", {"status": "resting", "limit": 200}, need_auth=True).get("orders") or []
            ticker = next((o.get("ticker", "") for o in resting if o.get("order_id") == a.order_id), "")
            if not ticker:
                sys.exit(f"No resting order with id {a.order_id}. Run `orders` to see them.")
        ids = [(a.order_id, ticker)]
    print(banner("KALSHI", paint(f"cancel {len(ids)} order(s)", "bold")) + "  " + environment_tag())
    for order_id, ticker in ids:
        print("  " + paint(order_id, "cyan") + paint(f"  {ticker}", "gray"))
    if not ids:
        print(paint("  No resting orders.", "gray"))
        return
    if not a.live:
        print("\n" + paint("  DRY RUN", "bold", "cyan") + " Nothing was cancelled. Add --live to cancel.")
        return
    confirm("CANCEL", a)
    for order_id, ticker in ids:
        result = api_send("DELETE", "/portfolio/events/orders/" + urllib.parse.quote(order_id), query={"market_ticker": ticker})
        print("  " + paint("Cancelled", "green", "bold") + f" {order_id}   contracts removed: {first(result, 'reduced_by', 'reduced_by_fp') or '?'}")


def cmd_positions(a):
    """What you hold right now, with today's price and what it would fetch."""
    balance = api_get("/portfolio/balance", need_auth=True)
    data = api_get("/portfolio/positions", {"limit": 200}, need_auth=True)
    held = [p for p in (data.get("market_positions") or []) if num(first(p, "position_fp", "position")) != 0]
    print(banner("KALSHI", paint("account", "bold")) + "  " + environment_tag())
    print("\n  Cash balance " + paint(f"${num(balance.get('balance')) / 100:,.2f}", "green", "bold")
          + paint(f"    {len(held)} open position(s)", "gray") + "\n")
    if not held:
        return
    print(paint(f"{'#':>3} {'HOLDING':<12} {'COST':>9} {'BID NOW':>8} {'WORTH':>9} {'IF SOLD':>9}  TICKER", "bold", "cyan"))
    print(rule(92))
    rows = []
    for i, p in enumerate(held, 1):
        # Kalshi reports YES holdings as a positive number and NO holdings as a negative one.
        position = num(first(p, "position_fp", "position"))
        side, count = ("yes" if position > 0 else "no"), abs(position)
        cost = num(first(p, "market_exposure_dollars")) or num(p.get("market_exposure")) / 100.0
        try:
            market = api_get("/markets/" + urllib.parse.quote(p["ticker"]))["market"]
            bid = cents(market.get(side + "_bid_dollars"))
        except Exception:
            bid = None
        worth = count * bid / 100.0 if bid is not None else None
        print(f"{cell(i, 3, 'gray', align='>')} {cell(f'{count:g} {side.upper()}', 12, *side_style(side.upper()))} "
              f"{f'${cost:,.2f}':>9} {price_text(bid) + 'c':>8} {('$' + format(worth, ',.2f')) if worth is not None else '-':>9} "
              f"{signed_money(worth - cost) if worth is not None else '-':>9}  {paint(p['ticker'], 'cyan')}")
        rows.append({"ticker": p["ticker"], "buy_side": side.upper(), "count": count})
    save_last(rows)
    print("\n" + paint("Sell one with: ", "gray") + "sell 1 --side yes --count N --price CENTS")


# ----------------------------------------------------------------------------
# Edge finders: places where the prices themselves do not add up
# ----------------------------------------------------------------------------
# These commands look for things that are wrong by arithmetic, not by opinion:
#   * a set of outcomes where only one can win, but the bids add to more than $1
#   * a ladder of thresholds where the higher bar is priced above the lower bar
#   * a price that moved far more today than it normally moves
#   * trading volume running at several times its usual pace
#   * a quote that has drifted well away from the last trade
# Every finding comes from the saved market list, which can be up to 30 minutes
# old, and quoted sizes can be tiny. ALWAYS confirm with `show` before acting.

def group_by_event(rows):
    """Bucket the flat market list by event ticker."""
    events = {}
    for m in rows:
        events.setdefault(m["event"], []).append(m)
    return events


def find_overround(events, min_edge):
    """
    Events where only one outcome can win. Buying NO on every outcome costs
    sum(100 - yes_bid) and pays at least $1 for all but one of them, so the
    position profits by at least (sum of YES bids - 100) cents per set,
    whatever happens. Buying NO at the ask crosses the spread, so fees apply.
    """
    found = []
    for event, markets in events.items():
        legs = [m for m in markets if m.get("mx") and m["yes_bid"] is not None]
        if len(legs) < 2:
            continue
        total = sum(m["yes_bid"] for m in legs)
        fees = sum(taker_fee_cents(100 - m["yes_bid"]) for m in legs)
        edge = total - 100 - fees
        if edge >= min_edge:
            found.append({"kind": "overround", "event": event, "legs": legs, "edge": edge, "sum": total,
                          "title": legs[0].get("event_title") or legs[0]["title"], "url": legs[0]["url"],
                          "ticker": legs[0]["ticker"], "category": legs[0]["category"],
                          "what": f"buy NO on all {len(legs)} outcomes: YES bids add to {total:g}c"})
    return sorted(found, key=lambda f: -f["edge"])


def find_underround(events, min_edge):
    """
    The mirror image: the YES asks add to less than $1. Buying every outcome
    wins only if one of the listed outcomes must happen, which Kalshi does not
    guarantee, so this is flagged as a lead to check and not as free money.
    """
    found = []
    for event, markets in events.items():
        legs = [m for m in markets if m.get("mx")]
        if len(legs) < 2 or any(m["yes_ask"] is None for m in legs):
            continue
        total = sum(m["yes_ask"] for m in legs)
        if total < 80:
            continue                                  # far below $1 means outcomes are missing, not mispriced
        fees = sum(taker_fee_cents(m["yes_ask"]) for m in legs)
        edge = 100 - total - fees
        if edge >= min_edge:
            found.append({"kind": "underround", "event": event, "legs": legs, "edge": edge, "sum": total,
                          "title": legs[0].get("event_title") or legs[0]["title"], "url": legs[0]["url"],
                          "ticker": legs[0]["ticker"], "category": legs[0]["category"],
                          "what": f"YES asks on all {len(legs)} outcomes add to only {total:g}c"})
    return sorted(found, key=lambda f: -f["edge"])


def ladder_of(markets):
    """
    The markets of an event that form a threshold ladder ("above 3.4", "above
    3.6"...), sorted by threshold. Returns (legs, direction): direction is +1
    when YES means "above the number" and -1 when it means "below".
    """
    # A rung has ONE threshold: "above X" (a floor, no cap) or "below X" (a cap, no floor). Markets with both
    # are "exactly X" or "between X and Y" buckets, where only one can win; those are not a ladder.
    if any(m.get("mx") for m in markets):
        return [], 0
    above = [m for m in markets if m.get("strike_type") in ("greater", "greater_or_equal")
             and m.get("floor") is not None and m.get("cap") is None]
    below = [m for m in markets if m.get("strike_type") in ("less", "less_or_equal")
             and m.get("cap") is not None and m.get("floor") is None]
    for legs, key, direction in ((above, "floor", 1), (below, "cap", -1)):
        # A real ladder asks the same question at different numbers: "Above 3.4%", "Above 3.6%".
        # "Will it rain in Austin" and "...in Atlanta" both have a threshold but are different questions,
        # so group by the label with its numbers blanked out and keep the biggest group.
        groups = {}
        for m in legs:
            groups.setdefault(re.sub(r"[\d.,]+", "#", m.get("label") or ""), []).append(m)
        best = max(groups.values(), key=len, default=[])
        if len(best) >= 2 and len({m[key] for m in best}) == len(best):
            return sorted(best, key=lambda m: m[key]), direction
    return [], 0


def find_ladder_breaks(events, min_edge):
    """
    On a ladder, "above the higher number" can never be more likely than
    "above the lower number". If the higher bar's YES bid is above the lower
    bar's YES ask, then buying YES on the lower bar and NO on the higher bar
    costs less than the $1 the pair is guaranteed to pay.
    """
    found = []
    for event, markets in events.items():
        legs, direction = ladder_of(markets)
        if not legs:
            continue
        if direction < 0:
            legs = legs[::-1]                       # now "easier to satisfy" always comes first
        for i, easy in enumerate(legs):
            for hard in legs[i + 1:]:
                if easy["yes_ask"] is None or hard["yes_bid"] is None or easy["yes_bid"] is None or hard["yes_ask"] is None:
                    continue                          # both rungs need a real two-sided quote
                if (easy["spread"] or 99) > 20 or (hard["spread"] or 99) > 20:
                    continue                          # a 20c-wide quote is nobody's real price
                fees = taker_fee_cents(easy["yes_ask"]) + taker_fee_cents(100 - hard["yes_bid"])
                edge = hard["yes_bid"] - easy["yes_ask"] - fees
                if edge >= min_edge:
                    found.append({"kind": "ladder", "event": event, "legs": [easy, hard], "edge": edge,
                                  "title": easy.get("event_title") or easy["title"], "url": easy["url"],
                                  "ticker": easy["ticker"], "category": easy["category"],
                                  "what": f"buy YES [{easy.get('label')}] at {easy['yes_ask']:g}c and NO [{hard.get('label')}] "
                                          f"at {100 - hard['yes_bid']:g}c: the pair pays at least $1"})
    return sorted(found, key=lambda f: -f["edge"])


def find_volume_surges(rows, min_volume):
    """Today's volume compared with the market's own average day since it opened."""
    found = []
    for m in rows:
        days_open = (m["hours_open"] or 0) / 24.0
        if days_open < 3 or m["vol24"] < max(min_volume, 1000):
            continue
        earlier = (m["vol"] - m["vol24"]) / max(1.0, days_open - 1)      # average day before today
        if earlier < 50:
            continue                                  # a market that barely traded before has no "normal" to compare with
        ratio = m["vol24"] / earlier
        if ratio >= 4:
            found.append({**m, "kind": "surge", "ratio": ratio, "edge": 0.0,
                          "what": f"{short_number(m['vol24'])} contracts today vs {short_number(earlier)} on a normal day ({ratio:.0f}x)"})
    return sorted(found, key=lambda f: -f["ratio"])


def find_dislocations(rows, min_volume):
    """Quotes that have moved well away from where the market last traded."""
    found = []
    for m in rows:
        if m["last"] is None or m["yes_bid"] is None or m["yes_ask"] is None or m["vol24"] < min_volume:
            continue
        if (m["spread"] or 99) > 10 or not 5 <= (m["yes_bid"] + m["yes_ask"]) / 2 <= 95:
            continue                                  # skip empty books and markets that are already decided
        gap = m["yes_bid"] - m["last"] if m["last"] < m["yes_bid"] else m["last"] - m["yes_ask"] if m["last"] > m["yes_ask"] else 0
        if gap >= max(4, 2 * (m["spread"] or 1)):
            side = "above" if m["last"] < m["yes_bid"] else "below"
            found.append({**m, "kind": "dislocation", "gap": gap, "edge": 0.0,
                          "what": f"quote is {m['yes_bid']:g}/{m['yes_ask']:g}c, {gap:g}c {side} the last trade at {m['last']:g}c"})
    return sorted(found, key=lambda f: -f["gap"])


def find_unusual_moves(rows, min_volume, checks):
    """
    Price moves measured against each market's own history. A 6c move is
    routine in a jumpy market and extraordinary in a calm one, so the score is
    today's move divided by that market's typical daily move (a z-score).
    Only the biggest raw movers are checked, because each needs its own history.
    """
    shortlist = sorted((m for m in rows if m["change"] and m["vol24"] >= min_volume and (m["hours_left"] or 0) > 6
                        and m["last"] is not None and 4 <= m["last"] <= 96),      # 4-96c: not already decided
                       key=lambda m: -abs(m["change"]) * math.log10(10 + m["vol24"]))[:checks]
    found = []
    now = int(time.time())
    for m in shortlist:
        try:
            candles = api_get(f"/series/{urllib.parse.quote(m['series'])}/markets/{urllib.parse.quote(m['ticker'])}/candlesticks",
                              {"start_ts": now - 30 * 86400, "end_ts": now, "period_interval": 1440}).get("candlesticks") or []
        except Exception:
            continue
        closes = [c for c in (candle_close(x) for x in candles) if c is not None]
        if len(closes) < 6:
            continue
        past = [b - c for c, b in zip(closes[:-1], closes[1:-1])]         # every daily move except today's
        if len(past) < 4:
            continue
        typical = math.sqrt(sum(x * x for x in past) / len(past)) or 0.5   # root-mean-square daily move
        z = m["change"] / max(0.5, typical)
        if abs(z) >= 2.5:
            found.append({**m, "kind": "move", "z": z, "typical": typical, "edge": 0.0,
                          "what": f"moved {m['change']:+g}c today; a normal day is about {typical:.1f}c ({abs(z):.1f}x)"})
    return sorted(found, key=lambda f: -abs(f["z"]))


def cmd_anomalies(a):
    """Scan every open market for prices that break arithmetic or break their own pattern."""
    rows = add_clock_fields(load_markets(a.cache_minutes, a.refresh))
    wanted = {CATEGORIES[c].lower() for c in (a.category or [])}
    # Sports and crypto are left out unless asked for: live games and 15-minute crypto markets
    # swing to 0 or 100 as they finish, which looks like an anomaly and is not.
    skipped = set() if wanted else {CATEGORIES[c].lower() for c in (a.skip_category if a.skip_category is not None else ["sports", "crypto"])}
    rows = [m for m in rows if (not wanted or m["category"].lower() in wanted) and m["category"].lower() not in skipped]
    events = group_by_event(rows)
    kinds = set(a.type) if a.type else {"overround", "ladder", "underround", "move", "surge", "dislocation"}

    sections = []
    if "overround" in kinds:
        sections.append(("BIDS ADD TO MORE THAN $1", "Only one outcome can win, yet the YES bids sum past 100c. Buying NO on "
                         "every outcome locks in the difference.", find_overround(events, a.min_edge)))
    if "ladder" in kinds:
        sections.append(("LADDER OUT OF ORDER", "A harder-to-reach threshold is priced above an easier one.",
                         find_ladder_breaks(events, a.min_edge)))
    if "underround" in kinds:
        sections.append(("ASKS ADD TO LESS THAN $1", "Buying every outcome costs under $1. This only wins if one of the "
                         "listed outcomes MUST happen. Read the rules first.", find_underround(events, a.min_edge)))
    if "move" in kinds:
        sections.append(("UNUSUAL MOVES", "Today's move against that market's own normal day.",
                         find_unusual_moves(rows, a.min_volume_24h, a.checks)))
    if "surge" in kinds:
        sections.append(("VOLUME SURGES", "Trading at several times the market's usual daily pace.",
                         find_volume_surges(rows, a.min_volume_24h)))
    if "dislocation" in kinds:
        sections.append(("QUOTE AWAY FROM LAST TRADE", "The bid and ask have moved off the last traded price.",
                         find_dislocations(rows, a.min_volume_24h)))

    flat = [f for _, _, found in sections for f in found[: a.top]]
    for f in flat:
        f.setdefault("buy_side", "")
        f.setdefault("buy_price", None)
    save_last(flat)
    if a.format != "table":
        columns = ["kind", "ticker", "title", "category", "edge", "what", "url"]
        emit([{**f, "title": f["title"]} for f in flat], columns, a.format, a.out)
        return

    total = sum(len(found) for _, _, found in sections)
    print(banner("KALSHI", paint(f"{total} anomalies", "bold") + paint(f"  across {len(rows):,} markets in {len(events):,} events", "gray")))
    width = shutil.get_terminal_size((120, 30)).columns
    n = 0
    for name, blurb, found in sections:
        print("\n" + paint(f"  {name}", "bold", "cyan") + paint(f"  {len(found)} found", "gray"))
        print(paint("  " + blurb, "gray"))
        if not found:
            print(paint("  None right now.", "gray"))
            continue
        for f in found[: a.top]:
            n += 1
            edge = paint(f"{f['edge']:+.1f}c", "green", "bold") if f["edge"] else paint("     ", "gray")
            title = f["title"][: max(30, width - 30)]
            print(f"  {cell(n, 3, 'gray', align='>')} {cell(edge if f['edge'] else '', 7)} {paint(title, 'bold')}")
            print(f"      {f['what']}")
            print(f"      {paint(f['ticker'] if f['kind'] in ('move', 'surge', 'dislocation') else f['event'], 'cyan')}  "
                  f"{category_tag(f['category'])}  {paint(f['url'], 'blue', 'ul')}")

    words = []
    locked = [f for _, _, found in sections for f in found if f["kind"] in ("overround", "ladder")]
    if locked:
        best = max(locked, key=lambda f: f["edge"])
        words.append(f"{len(locked)} price sets break arithmetic after fees. The largest is worth {best['edge']:.1f}c per set on paper.")
        words.append("On paper is the catch: these are usually a few contracts deep and gone in seconds. Check the size with `show`.")
    else:
        words.append("No price sets break arithmetic after fees right now. That is the normal state of a watched market.")
    moves = next((found for name, _, found in sections if name == "UNUSUAL MOVES"), [])
    if moves:
        words.append(f"{len(moves)} markets moved at least 2.5 times their normal day. A move that big usually means news; "
                     "read it before assuming the market overreacted.")
    words.append("Prices here come from the saved list. Put --refresh before the command for a fresh one.")
    plain_words(words)
    if flat:
        print("\n" + paint("Next: ", "gray") + paint("show 1", "bold") + paint("   analyze 1 --deep", "bold")
              + paint("   event EVENT_TICKER", "bold") + paint("  (row numbers work)", "gray"))


# ----------------------------------------------------------------------------
# Ladder: turn an event's prices into a probability distribution
# ----------------------------------------------------------------------------

def midpoint(m):
    """The market's own estimate for YES, in percent: middle of bid and ask, else the last trade."""
    if m["yes_bid"] is not None and m["yes_ask"] is not None:
        return (m["yes_bid"] + m["yes_ask"]) / 2.0
    if m["yes_ask"] is not None:
        return m["yes_ask"] / 2.0                   # nobody bidding: somewhere between 0 and the ask
    return m["last"] if m["last"] is not None else 0.0


def cmd_ladder(a):
    """Show what an event's prices imply as a full probability distribution, and where it kinks."""
    ref, saved = resolve(a.event)
    # A row number or a market ticker is fine: find the event it belongs to.
    try:
        event = api_get("/events/" + urllib.parse.quote(ref), {"with_nested_markets": "true"})["event"]
    except urllib.error.HTTPError:
        market = api_get("/markets/" + urllib.parse.quote(ref))["market"]
        event = api_get("/events/" + urllib.parse.quote(market["event_ticker"]), {"with_nested_markets": "true"})["event"]
    rows = add_clock_fields([flatten(event, mk) for mk in event.get("markets") or [] if mk.get("status") in (None, "active", "open")])
    if len(rows) < 2:
        sys.exit("This event has fewer than two open markets, so there is no distribution to draw.")
    legs, direction = ladder_of(rows)

    print(banner("KALSHI", paint("implied distribution", "bold")))
    print("\n" + paint(event.get("title") or "", "bold", "white") + "  " + category_tag(event.get("category")))
    print(paint(market_url(event.get("series_ticker", ""), event.get("event_ticker", "")), "blue", "ul"))
    words = []

    if legs:
        # --- a threshold ladder: differences between neighbouring rungs are the chance of landing between them ---
        key = "floor" if direction > 0 else "cap"
        survive = [midpoint(m) for m in legs]                    # chance of YES on each rung
        if direction < 0:
            survive = [100 - s for s in survive]                 # convert "below X" into "above X"
        buckets = [(f"below {legs[0][key]:g}", 100 - survive[0], None, legs[0][key])]
        for lo, hi, p_lo, p_hi in zip(legs, legs[1:], survive, survive[1:]):
            buckets.append((f"{lo[key]:g} to {hi[key]:g}", p_lo - p_hi, lo[key], hi[key]))
        buckets.append((f"above {legs[-1][key]:g}", survive[-1], legs[-1][key], None))
        kinks = [b for b in buckets if b[1] < -0.5]
        peak = max(max(b[1] for b in buckets), 1)
        print("\n" + paint(f"  {'OUTCOME':<22} {'CHANCE':>7}", "bold", "cyan"))
        for name, chance, _, _ in buckets:
            tone = "red" if chance < -0.5 else "green"
            print(f"  {name:<22} {paint(f'{chance:>6.1f}%', tone, 'bold' if chance < -0.5 else 'white')}  "
                  f"{bar(max(0, chance) / peak, 40, tone)}")
        # Median: the threshold where the chance of being above it crosses 50%.
        median_at = None
        for lo, hi, p_lo, p_hi in zip(legs, legs[1:], survive, survive[1:]):
            if p_lo >= 50 >= p_hi and p_lo != p_hi:
                median_at = lo[key] + (hi[key] - lo[key]) * (p_lo - 50) / (p_lo - p_hi)
        step = (legs[-1][key] - legs[0][key]) / max(1, len(legs) - 1)
        mean = sum(max(0, chance) / 100 * ((lo if lo is not None else hi - step) + (hi if hi is not None else lo + step)) / 2
                   for _, chance, lo, hi in buckets)
        print()
        if median_at is not None:
            print(f"  {paint('Implied median', 'gray')}  {paint(f'{median_at:.2f}', 'bold')}"
                  f"   {paint('Implied average', 'gray')}  about {mean:.2f}")
            words.append(f"The market's central guess is about {median_at:.2f}: half the money says higher, half says lower.")
        likely = max(buckets, key=lambda b: b[1])
        words.append(f"The single most likely outcome is \"{likely[0]}\" at {likely[1]:.0f}%.")
        if kinks:
            words.append(f"{len(kinks)} range(s) come out NEGATIVE, which is impossible: a higher threshold is priced above a lower "
                         "one. Run `anomalies --type ladder` to see whether it survives the bid-ask gap and fees.")
        else:
            words.append("The ladder is in order: every higher threshold is priced at or below the one before it.")
    else:
        # --- named outcomes: rescale so they add to 100, which removes the built-in margin ---
        mids = [(m, midpoint(m)) for m in rows]
        total = sum(p for _, p in mids) or 1
        mids.sort(key=lambda mp: -mp[1])
        print("\n" + paint(f"  {'OUTCOME':<30} {'PRICE':>6} {'FAIR':>7}   {'ASK':>5}  ", "bold", "cyan"))
        for m, p in mids[: a.top]:
            fair = p / total * 100
            cheap = m["yes_ask"] is not None and m["yes_ask"] < fair - 1
            label = (m.get("label") or m["title"])[:30]
            print(f"  {label:<30} {p:>5.1f}% {paint(f'{fair:>6.1f}%', 'bold')}   {price_text(m['yes_ask']) + 'c':>5}  "
                  f"{bar(fair / max(1, mids[0][1] / total * 100), 34, 'green' if cheap else 'cyan')}"
                  + (paint("  ask below fair", "green") if cheap else ""))
        print(f"\n  {paint('Prices add to', 'gray')} {paint(f'{total:.0f}%', 'bold')}"
              + paint("   FAIR rescales them to 100%, removing the built-in margin.", "gray"))
        words.append(f"The favorite is \"{(mids[0][0].get('label') or mids[0][0]['title'])[:40]}\" at a fair {mids[0][1] / total * 100:.0f}%.")
        if total > 103:
            words.append(f"Prices add to {total:.0f}%, so buyers are collectively overpaying by {total - 100:.0f} points. "
                         "That margin is what a limit-order seller collects.")
        elif total < 97:
            words.append(f"Prices add to only {total:.0f}%. Either an unlisted outcome is likely, or some of these are underpriced.")
        if not event.get("mutually_exclusive"):
            words.append("More than one of these can happen, so they are not shares of a single pie. Read FAIR loosely.")
    plain_words(words)
    save_last([{**m, "buy_side": "", "buy_price": None} for m in rows])
    print("\n" + paint("Next: ", "gray") + paint(f"event {event.get('event_ticker')}", "bold") + paint("   show 1", "bold"))


# ----------------------------------------------------------------------------
# Plan: from your probability to a sized order
# ----------------------------------------------------------------------------

def kelly_fraction(win_prob, cost):
    """
    The Kelly criterion for a contract that costs `cost` dollars and pays $1:
    the share of your bankroll that maximises long-run growth is
        (p - cost) / (1 - cost)
    It is zero or negative when you have no edge. Full Kelly swings wildly and
    assumes your probability is exactly right, so this tool uses a quarter of it.
    """
    if cost >= 1 or cost <= 0:
        return 0.0
    return (win_prob - cost) / (1.0 - cost)


def cmd_plan(a):
    """Turn your own probability into edge, expected value, a Kelly size and a ready-to-run order."""
    ticker, saved = resolve(a.market)
    market = api_get("/markets/" + urllib.parse.quote(ticker))["market"]
    try:
        event = api_get("/events/" + urllib.parse.quote(market["event_ticker"]))["event"]
    except Exception:
        event = {"event_ticker": market.get("event_ticker", ""), "series_ticker": market.get("event_ticker", "").split("-")[0]}
    m = add_clock_fields([flatten(event, market)])[0]

    print(banner("KALSHI", paint("trade plan", "bold")))
    print("\n" + paint(m["title"], "bold", "white"))
    print(f"{paint(ticker, 'cyan')}  {category_tag(m['category'])}  ends in {paint(time_left(m['hours_left']), 'bold')}")
    print(f"  your probability of YES: {paint(f'{a.my_prob:g}%', 'bold')}     market: YES {price_text(m['yes_bid'])}/{price_text(m['yes_ask'])}c"
          f"   NO {price_text(m['no_bid'])}/{price_text(m['no_ask'])}c")

    # --- work out every way in: each side, as a resting limit order or by paying the ask ---
    options = []
    for side in ("yes", "no"):
        win = (a.my_prob if side == "yes" else 100 - a.my_prob) / 100.0
        bid, ask = m[side + "_bid"], m[side + "_ask"]
        if ask is not None:
            fee = taker_fee_cents(ask)
            options.append(dict(side=side, how="take", price=ask, fee=fee, win=win))
        if bid is not None and ask is not None and ask - bid >= 2:
            options.append(dict(side=side, how="limit", price=bid + 1, fee=0.0, win=win))
        elif bid is not None and ask is None:
            options.append(dict(side=side, how="limit", price=bid + 1, fee=0.0, win=win))
    if a.side:
        options = [o for o in options if o["side"] == a.side]
    for o in options:
        cost = (o["price"] + o["fee"]) / 100.0
        o["edge"] = o["win"] * 100 - o["price"] - o["fee"]             # cents of expected profit per contract
        o["ret"] = o["edge"] / (o["price"] + o["fee"])                 # expected return on the money put in
        o["kelly"] = max(0.0, kelly_fraction(o["win"], cost))
        o["stake_pct"] = min(a.max_risk, 100 * o["kelly"] * a.kelly)   # quarter Kelly, never above the guide's cap
        o["count"] = int((a.bankroll * o["stake_pct"] / 100.0) // cost) if cost > 0 else 0
        o["cost"] = o["count"] * cost
        # What if you are too confident? Recompute the edge with your probability pulled 5 points toward the market.
        o["edge_if_off"] = (o["win"] * 100 - 5) - o["price"] - o["fee"]
    if not options:
        sys.exit("Nobody is quoting this market right now, so there is nothing to plan.")
    options.sort(key=lambda o: -o["edge"])

    print("\n" + paint(f"  {'#':>2} {'ORDER':<16} {'HOW':<6} {'WIN %':>6} {'EDGE':>7} {'RETURN':>7} {'IF 5 OFF':>9} {'KELLY':>7} {'SIZE':>6} {'CONTRACTS':>10} {'COST':>9}",
                       "bold", "cyan"))
    for i, o in enumerate(options, 1):
        tone = "green" if o["edge"] >= 5 else "yellow" if o["edge"] > 0 else "red"
        order = cell(f"{o['side'].upper()} @ {o['price']:g}c", 16, *side_style(o["side"].upper()))
        how = cell(o["how"], 6, "green" if o["how"] == "limit" else "yellow")
        edge = cell(f"{o['edge']:+.1f}c", 7, tone, "bold", align=">")
        if_off = cell(f"{o['edge_if_off']:+.1f}c", 9, "green" if o["edge_if_off"] > 0 else "red", align=">")
        cost = "$" + format(o["cost"], ",.2f")
        print(f"  {cell(i, 2, 'gray', align='>')} {order} {how} {o['win'] * 100:>5.0f}% {edge} {o['ret']:>+7.1%} {if_off} "
              f"{o['kelly']:>7.1%} {o['stake_pct']:>5.1f}% {o['count']:>10} {cost:>9}")
    print(paint(f"  KELLY is the full Kelly share of bankroll. SIZE is {a.kelly:g} of that, capped at {a.max_risk:g}% "
                f"of your ${a.bankroll:,.0f} bankroll. \"limit\" may never fill.", "gray"))

    # --- the guide's checklist against the best option ---
    best = options[0]
    days = (m["hours_left"] or 0) / 24
    checks = [
        (best["edge"] >= 5, f"edge of 5 points or more (yours: {best['edge']:+.1f})"),
        (best["price"] >= 15, f"price at 15c or more (this: {best['price']:g}c)"),
        (best["edge_if_off"] > 0, "still positive if your probability is 5 points too confident"),
        (days <= 30, f"resolves within 30 days (this: {time_left(m['hours_left'])})"),
        (best["how"] == "limit", "can be entered with a limit order, no fee"),
        (m["vol24"] >= 500, f"enough trading to get filled ({short_number(m['vol24'])} contracts in 24h)"),
    ]
    print("\n" + paint("  CHECKLIST", "bold", "cyan") + paint(f"  for option 1: {best['side'].upper()} @ {best['price']:g}c", "gray"))
    for ok, text in checks:
        print(f"  {paint('PASS', 'green', 'bold') if ok else paint('FAIL', 'red', 'bold')}  {text}")
    passed = sum(1 for ok, _ in checks if ok)
    must = checks[0][0] and checks[1][0] and best["count"] >= 1
    verdict = paint("WORTH PLACING", "green", "bold") if must and passed >= 5 else \
        paint("MARGINAL", "yellow", "bold") if must else paint("SKIP", "red", "bold")
    print(f"\n  {paint('VERDICT', 'bold', 'cyan')}  {verdict}  {paint(f'({passed} of {len(checks)} checks pass)', 'gray')}")

    words = []
    if best["edge"] <= 0:
        words.append("At your own probability there is no side of this market with positive expected value. Skip it.")
    else:
        words.append(f"You think {best['side'].upper()} wins {best['win'] * 100:.0f}% of the time and can get in at {best['price']:g}c, "
                     f"so each contract is worth {best['edge']:.1f}c more than it costs, if you are right.")
        words.append(f"If your number is 5 points too confident, the edge becomes {best['edge_if_off']:+.1f}c. "
                     + ("It survives being somewhat wrong." if best["edge_if_off"] > 0 else "It depends on you being right."))
        if best["count"] >= 1:
            profit = best["count"] * (1 - (best["price"] + best["fee"]) / 100.0)
            words.append(f"The suggested {best['count']} contracts risk ${best['cost']:,.2f} to make ${profit:,.2f}, "
                         f"with an expected profit of ${best['count'] * best['edge'] / 100:,.2f}.")
        else:
            words.append("The edge is too thin for even one contract at this bankroll and risk setting.")
    words.append("Everything here follows from YOUR probability. The script cannot tell you whether it is right.")
    plain_words(words)

    save_last([{"ticker": ticker, "title": m["title"], "url": m["url"], "buy_side": best["side"].upper(), "buy_price": best["price"],
                "limit_price": best["price"], "count": best["count"], "est_prob": a.my_prob}])
    if best["count"] >= 1 and best["edge"] > 0:
        take = " --take" if best["how"] == "take" else ""
        print("\n" + paint("Next: ", "gray") + paint(f"buy {ticker} --side {best['side']} --price {best['price']:g} --count {best['count']} "
              f"--my-prob {a.my_prob:g} --bankroll {a.bankroll:g}{take}", "bold") + paint(f"  (dry run; or just: buy 1{take})", "gray"))


# ----------------------------------------------------------------------------
# Brief: a research prompt for an AI assistant
# ----------------------------------------------------------------------------

def cmd_brief(a):
    """
    Write a research prompt you can paste into an AI assistant. The assistant
    does the reading and gives a probability with reasons; you bring that
    number back to `plan`. This script does not call any AI service itself.
    """
    ticker, saved = resolve(a.market)
    market = api_get("/markets/" + urllib.parse.quote(ticker))["market"]
    try:
        event = api_get("/events/" + urllib.parse.quote(market["event_ticker"]))["event"]
    except Exception:
        event = {"event_ticker": market.get("event_ticker", ""), "series_ticker": market.get("event_ticker", "").split("-")[0]}
    m = add_clock_fields([flatten(event, market)])[0]
    now = int(time.time())
    try:
        candles = api_get(f"/series/{urllib.parse.quote(m['series'])}/markets/{urllib.parse.quote(ticker)}/candlesticks",
                          {"start_ts": now - 14 * 86400, "end_ts": now, "period_interval": 1440}).get("candlesticks") or []
        closes = [c for c in (candle_close(x) for x in candles) if c is not None]
    except Exception:
        closes = []
    history = (f"Over the last {len(closes)} days the YES price went from {closes[0]:g}c to {closes[-1]:g}c "
               f"(low {min(closes):g}c, high {max(closes):g}c)." if len(closes) >= 2 else "No price history is available.")
    sources = "; ".join(f"{s.get('name')} ({s.get('url')})" for s in (event.get("settlement_sources") or [])[:6]) or "see the rules"
    today = dt.date.today().strftime("%B %d, %Y")
    text = f"""I am deciding whether to trade a prediction market and I want an independent probability estimate.
Today is {today}. Please search for current information; do not rely on memory for anything recent.

THE QUESTION
{m['title']}

HOW IT RESOLVES (these exact rules decide the outcome, so read them literally)
{market.get('rules_primary') or '(no rules text returned)'}
{market.get('rules_secondary') or ''}
Resolution sources: {sources}
The market closes {m['close_time']} (in {time_left(m['hours_left'])}).

WHAT I NEED FROM YOU
1. Restate in one sentence exactly what has to happen for this to resolve YES, including any deadline or
   threshold. Point out anything in the rules that could make it resolve differently from the headline.
2. The base rate: how often has this kind of thing happened in comparable past cases? Say which cases.
3. The current evidence for and against, each item with its source and date. Prefer primary sources.
4. Your probability that this resolves YES, as a single number, plus a range you are 80% sure contains
   the truth. Show how you got from the base rate to that number.
5. What specific news between now and the close would move your number by 10 points or more?
6. How confident you are in the estimate itself, and what you could not find out.

Form your own estimate from the evidence BEFORE reading the next section.

FOR COMPARISON ONLY
The market currently prices YES at about {price_text(m['yes_bid'])}c bid / {price_text(m['yes_ask'])}c ask.
{history}
After giving your estimate, say whether you disagree with the market by 5 points or more and, if so, the
single strongest reason the market could be right and you wrong.

Market page: {m['url']}
"""
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(paint("Saved ", "green", "bold") + f"the research prompt to {a.out}")
    else:
        print(text)
    if a.copy:
        try:
            tool = ["clip"] if os.name == "nt" else ["pbcopy"] if sys.platform == "darwin" else ["xclip", "-selection", "clipboard"]
            subprocess.run(tool, input=text.encode("utf-16le" if os.name == "nt" else "utf-8"), check=True)
            print(paint("Copied to the clipboard.", "green", "bold"), file=sys.stderr)
        except Exception as err:
            print(paint(f"Could not copy to the clipboard ({err}).", "yellow"), file=sys.stderr)
    print(paint(f"\nWhen you have a number: python kalshi_cli.py plan {ticker} --my-prob NUMBER --bankroll YOUR_BANKROLL", "gray"),
          file=sys.stderr)


# ----------------------------------------------------------------------------
# Watchlist
# ----------------------------------------------------------------------------

HOME_DIR = os.environ.get("KALSHI_HOME", os.path.join(os.path.expanduser("~"), ".kalshi_cli"))
WATCH_FILE = os.path.join(HOME_DIR, "watchlist.json")


def load_watchlist():
    try:
        with open(WATCH_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def cmd_watchlist(a):
    """Keep a list of markets you care about and see how each has moved since you added it."""
    items = load_watchlist()
    if a.action in ("add", "remove") and not a.market:
        sys.exit(f"Say which market: watchlist {a.action} TICKER")
    if a.action == "add":
        ticker = resolve(a.market)[0]
        market = api_get("/markets/" + urllib.parse.quote(ticker))["market"]
        bid, ask = cents(market.get("yes_bid_dollars")), cents(market.get("yes_ask_dollars"))
        mid = (bid + ask) / 2 if bid is not None and ask is not None else cents(market.get("last_price_dollars"))
        items = [i for i in items if i["ticker"] != ticker]
        items.append({"ticker": ticker, "title": market.get("title") or ticker, "added": now_stamp(), "price": mid, "note": a.note or ""})
    elif a.action == "remove":
        ticker = resolve(a.market)[0]
        items = [i for i in items if i["ticker"] != ticker]
    elif a.action == "clear":
        items = []
    if a.action != "list":
        os.makedirs(HOME_DIR, exist_ok=True)
        with open(WATCH_FILE, "w", encoding="utf-8") as fh:
            json.dump(items, fh, indent=2)
    print(banner("KALSHI", paint(f"watchlist: {len(items)} market(s)", "bold")) + "\n")
    if not items:
        print(paint("Empty. Add one with: watchlist add TICKER --note \"why\"", "gray"))
        return
    print(paint(f"{'#':>3} {'YES b/a':<10} {'ADDED AT':>8} {'SINCE':>7} {'ENDS':>5}  MARKET", "bold", "cyan"))
    print(rule(100))
    rows = []
    for n, item in enumerate(items, 1):
        try:
            market = api_get("/markets/" + urllib.parse.quote(item["ticker"]))["market"]
        except Exception:
            print(f"{cell(n, 3, 'gray', align='>')} {paint('(not found)', 'gray'):<10} {item['ticker']}")
            continue
        bid, ask = cents(market.get("yes_bid_dollars")), cents(market.get("yes_ask_dollars"))
        mid = (bid + ask) / 2 if bid is not None and ask is not None else cents(market.get("last_price_dollars"))
        moved = (mid - item["price"]) if mid is not None and item.get("price") is not None else None
        closes = parse_time(market.get("close_time"))
        hours = (closes - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600 if closes else None
        since = cell("" if moved is None else f"{moved:+.1f}c", 7, "green" if (moved or 0) > 0 else "red" if (moved or 0) < 0 else "gray", align=">")
        status = "" if market.get("status") in ("active", "open") else paint(f"  [{market.get('status')}: {market.get('result') or 'no result yet'}]", "yellow")
        print(f"{cell(n, 3, 'gray', align='>')} {price_text(bid) + '/' + price_text(ask):<10} {price_text(item.get('price')) + 'c':>8} {since} "
              f"{time_left(hours):>5}  {paint(item['title'][:60], 'bold')}{status}")
        print(f"    {paint(item['ticker'], 'cyan')}" + (paint(f"  note: {item['note']}", "gray") if item.get("note") else ""))
        rows.append({"ticker": item["ticker"], "title": item["title"], "buy_side": "", "buy_price": None})
    save_last(rows)


def now_stamp():
    return dt.datetime.now().replace(microsecond=0).isoformat()


# ----------------------------------------------------------------------------
# Best: the single top-scoring pick, or the top few
# ----------------------------------------------------------------------------

def cmd_best(a):
    """`best` = the one top-scoring market. `best 3`, `best 5`, `best 10` = that many, in score order."""
    a.top = a.count
    a.no_random = True
    cmd_picks(a)


# ----------------------------------------------------------------------------
# Grind: compounding a small stake through a chain of likely winners
# ----------------------------------------------------------------------------
# The idea: put the bankroll on a heavy favorite that resolves soon, collect a
# small gain, roll it into the next one, and repeat: $10 -> $11 -> $12.50 ...
#
# The arithmetic you need to see before trying it: each step is likely, but the
# chain is only as likely as all of its steps multiplied together. Turning $10
# into $40 at fair prices succeeds about 10/40 = 25% of the time however you
# slice it, and fees make it a little worse. This command builds the chain and
# shows those odds honestly, including what smaller stakes do to them.

def simulate_chain(steps, start, stake_pct, target, use_market_price, runs=20000, seed=1):
    """Walk the chain many times. Each step stakes a share of the current bankroll; a loss forfeits that stake."""
    rng = random.Random(seed)
    finals = []
    for _ in range(runs):
        bank = start
        for s in steps:
            cost = (s["buy_price"] + s["fee"]) / 100.0
            count = int((bank * stake_pct / 100.0) // cost)
            if count < 1:
                break
            chance = (s["mid"] if use_market_price else s["est_prob"]) / 100.0
            bank += count * (1.0 - cost) if rng.random() < chance else -count * cost
            if bank >= target:
                break
        finals.append(bank)
    finals.sort()
    return {"reach": sum(1 for f in finals if f >= target) / runs, "mean": sum(finals) / runs,
            "median": finals[runs // 2], "down": sum(1 for f in finals if f < start) / runs,
            "bust": sum(1 for f in finals if f < start * 0.25) / runs}


def cmd_grind(a):
    """Build a chain of likely winners that resolve one after another, and show the odds of the whole chain."""
    rows = add_clock_fields(load_markets(a.cache_minutes, a.refresh))
    wanted = {CATEGORIES[c].lower() for c in (a.category or [])}
    skipped = {CATEGORIES[c].lower() for c in (a.skip_category if a.skip_category is not None else ["sports", "crypto"])}

    # 1. Candidates: heavy favorites that resolve soon, in markets busy enough to get filled at the ask.
    pool = []
    for m in rows:
        category = m["category"].lower()
        if (wanted and category not in wanted) or (not wanted and category in skipped):
            continue
        if m["hours_left"] is None or not (0.5 <= m["hours_left"] <= a.closes_within * 24) or m["vol24"] < a.min_volume_24h:
            continue
        for side in ("yes", "no"):
            bid, ask = m[side + "_bid"], m[side + "_ask"]
            if bid is None or ask is None or not (a.min_price <= ask <= a.max_price) or ask - bid > a.max_spread:
                continue
            mid = (bid + ask) / 2.0
            fee = taker_fee_cents(ask)                  # compounding needs a certain fill, so this pays the ask
            pool.append({**m, "buy_side": side.upper(), "buy_price": ask, "limit_price": ask, "fee": fee, "mid": mid,
                         "est_prob": estimated_probability(mid, a.bias), "gain": (100 - ask - fee) / (ask + fee)})
    pool.sort(key=lambda m: m["hours_left"])

    # 2. Chain them: each step must resolve after the one before it, so the money is back in time.
    #    Among the next few to resolve, take the best value. One market per event.
    steps, used_events, clock, bank = [], set(), 0.0, a.start
    while len(steps) < a.steps and bank < a.target:
        later = [m for m in pool if m["hours_left"] > clock and m["event"] not in used_events]
        if not later:
            break
        # "Best" = most expected value per dollar staked: the estimated chance of winning divided by
        # the price paid. Ranking on chance alone would always pick 97c contracts that barely move the bankroll.
        pick = max(later[:12], key=lambda m: (m["est_prob"] / (m["buy_price"] + m["fee"]), -m["hours_left"]))
        cost = (pick["buy_price"] + pick["fee"]) / 100.0
        count = int((bank * a.stake / 100.0) // cost)
        if count < 1:
            break
        after = bank + count * (1.0 - cost)
        steps.append({**pick, "count": count, "stake": count * cost, "before": bank, "after": after})
        used_events.add(pick["event"])
        clock, bank = pick["hours_left"], after
    save_last(steps)

    print(banner("KALSHI", paint(f"grind ${a.start:g} toward ${a.target:g}", "bold")
                 + paint(f"  staking {a.stake:g}% of the bankroll each step", "gray")))
    print(paint("screen:    ", "gray") + f"price {a.min_price:g}-{a.max_price:g}c  |  resolves within {a.closes_within:g}d  |  "
          f"24h vol >= {a.min_volume_24h:g}  |  spread <= {a.max_spread:g}c  |  "
          + ("only " + ", ".join(a.category) if a.category else "no " + ", ".join(sorted(skipped))))
    print(paint("candidates: ", "gray") + f"{len(pool):,} favorites pass the screen")
    if not steps:
        print("\n" + paint("No chain could be built.", "yellow", "bold") + " Widen --closes-within, lower --min-volume-24h, or raise --max-price.")
        return

    width = shutil.get_terminal_size((120, 30)).columns
    title_w = max(24, min(60, width - 84))
    print()
    print(paint(f"{'STEP':>4} {'ORDER':<17} {'WINS':>6} {'GAIN':>6} {'BANKROLL IF IT WINS':<22} {'CHAIN ODDS':>10} {'ENDS':>5}  MARKET", "bold", "cyan"))
    print(rule(min(width - 1, 84 + title_w)))
    chain = 1.0
    for i, s in enumerate(steps, 1):
        chain *= s["est_prob"] / 100.0
        order = cell(f"{s['count']} {s['buy_side']} @ {s['buy_price']:g}c", 17, *side_style(s["buy_side"]))
        grow = f"${s['before']:,.2f} -> " + paint(f"${s['after']:,.2f}", "green", "bold")
        pad = " " * max(0, 22 - len(f"${s['before']:,.2f} -> ${s['after']:,.2f}"))
        tone = "green" if chain >= 0.6 else "yellow" if chain >= 0.3 else "red"
        title = s["title"] if len(s["title"]) <= title_w else s["title"][:title_w - 3] + "..."
        cut = title.rfind(" [")
        title = paint(title, "bold") if cut <= 0 else paint(title[:cut], "bold") + paint(title[cut:], "cyan")
        print(f"{cell(i, 4, 'gray', align='>')} {order} {s['est_prob']:>5.1f}% {s['gain']:>+6.1%} {grow}{pad} "
              f"{cell(f'{chain:.0%}', 10, tone, 'bold', align='>')} {time_left(s['hours_left']):>5}  {title}")
        if not a.no_links:
            print(f"     {paint(s['ticker'], 'cyan')}  {category_tag(s['category'])}  {paint(s['url'], 'blue', 'ul')}")
    reached = steps[-1]["after"] >= a.target
    print("\n  " + (paint(f"The chain reaches ${steps[-1]['after']:,.2f} in {len(steps)} wins.", "bold") if reached else
                    paint(f"The chain runs out at ${steps[-1]['after']:,.2f} after {len(steps)} wins, short of ${a.target:g}.", "yellow", "bold"))
          + paint(f"  It takes about {time_left(steps[-1]['hours_left'])} to play out.", "gray"))

    # 3. The same chain under different staking plans.
    goal = min(a.target, steps[-1]["after"])
    print("\n" + paint(f"  STAKING PLANS", "bold", "cyan") + paint(f"  20,000 simulated runs of this chain, aiming for ${goal:,.2f}", "gray"))
    print(paint(f"  {'STAKE EACH STEP':<18} {'REACH GOAL':>10} {'END BELOW START':>16} {'LOSE 75%+':>10} {'AVERAGE END':>12} {'TYPICAL END':>12}", "bold", "cyan"))
    plans = sorted({100.0, 50.0, 25.0, float(a.stake)}, reverse=True)
    for pct in plans:
        r = simulate_chain(steps, a.start, pct, goal, use_market_price=False)
        mark = paint("  <- yours", "gray") if pct == float(a.stake) else ""
        avg_tone = "green" if r["mean"] > a.start else "red"
        print(f"  {f'{pct:g}% of bankroll':<18} {r['reach']:>9.0%} {r['down']:>15.0%} {r['bust']:>10.0%} "
              f"{cell('$' + format(r['mean'], ',.2f'), 12, avg_tone, align='>')} {'$' + format(r['median'], ',.2f'):>12}{mark}")
    fair = simulate_chain(steps, a.start, a.stake, goal, use_market_price=True)

    words = [
        f"Each step wins about {sum(s['est_prob'] for s in steps) / len(steps):.0f}% of the time, but all {len(steps)} in a row "
        f"happens only {chain:.0%} of the time.",
        f"Going from ${a.start:g} to ${goal:,.2f} is a {goal / a.start:.1f}x gain. At fair prices a {goal / a.start:.1f}x gain can "
        f"succeed at most {a.start / goal:.0%} of the time. Chaining favorites does not beat that; it only hides it.",
        f"If the market prices are simply right, staking {a.stake:g}% ends at ${fair['mean']:,.2f} on average, below the "
        f"${a.start:g} you started with, because every step pays the taker fee.",
        "Smaller stakes survive a loss but reach the goal less often with this many steps. Pick the row whose worst case you can live with.",
        "This is the guide's high-price zone, where contracts have been slightly underpriced on average. That is a small edge, "
        "and one loss at these prices erases many wins.",
    ]
    plain_words(words)
    print("\n" + paint("Next: ", "gray") + paint("show 1", "bold") + paint("   buy 1 --take", "bold")
          + paint("  (dry run; --take because these fill at the ask)", "gray"))


# ----------------------------------------------------------------------------
# Multiply: turn a stake into 2x / 3x / 5x / 10x inside a week
# ----------------------------------------------------------------------------
# You say how much you want to put down and what multiple you want back. The
# tool then lays out up to three ways to try for it, side by side:
#
#   SINGLE  one bet. A contract priced near 100 / multiple cents. A 5x needs a
#           contract at about 20c: it pays $1, so every 20c becomes 100c.
#   CHAIN   several bets in a row. Each one is far more likely than the single
#           bet, but ALL of them have to win, and the winnings of one are the
#           stake of the next. Two 70c winners in a row is roughly a 2x.
#   SPREAD  several bets at once. The stake is split across cheaper longshots,
#           sized so that ANY ONE of them winning pays the whole target.
#
# THE HONEST PART: none of these beats arithmetic. At fair prices a 5x comes in
# about one time in five whichever way it is sliced, and fees shave a little
# off that. What the routes change is the SHAPE of the risk (one coin flip, a
# run of likely wins, or several lottery tickets), not the odds of the result.
#
# Everything must resolve inside the window: --days, default 3, never above 7.

MULTIPLY_DEFAULT_DAYS = 3.0     # how far ahead to look when --days is not given
MULTIPLY_MAX_DAYS = 7.0         # the hard ceiling; a longer --days is cut back to this
MULTIPLY_COLUMNS = ["route", "leg", "ticker", "title", "category", "buy_side", "limit_price", "count", "cost",
                    "pays", "est_prob", "mid", "close_time", "url"]


def parse_multiple(text):
    """Read '5x', '5X', 'x5' or plain '5' as the number 5.0. Used as the argparse type for the multiple."""
    cleaned = str(text).lower().replace("x", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a multiple. Write it like 2x, 3x, 5x or 10x.")
    if not 1.1 <= value <= 50:
        raise argparse.ArgumentTypeError("the multiple must be between 1.1x and 50x.")
    return value


def multiply_pool(rows, a, days):
    """Every side of every market that is tradable now and resolves inside the window, with its real cost."""
    wanted = {CATEGORIES[c].lower() for c in (a.category or [])}
    skipped = {CATEGORIES[c].lower() for c in (a.skip_category if a.skip_category is not None else ["sports", "crypto"])}
    pool = []
    for m in rows:
        category = m["category"].lower()
        if (wanted and category not in wanted) or (not wanted and category in skipped):
            continue
        if m["hours_left"] is None or not (0.5 <= m["hours_left"] <= days * 24) or m["vol24"] < a.min_volume_24h:
            continue
        for side in ("yes", "no"):
            bid, ask = m[side + "_bid"], m[side + "_ask"]
            if bid is None or ask is None or not (1 <= ask <= 98) or ask - bid > a.max_spread:
                continue
            mid = (bid + ask) / 2.0
            fee = taker_fee_cents(ask)          # these routes need a certain fill, so they pay the ask and the fee
            cost_c = ask + fee                  # what one contract really costs, in cents
            pool.append({**m, "buy_side": side.upper(), "buy_price": ask, "limit_price": ask, "fee": fee, "mid": mid,
                         "cost_c": cost_c, "est_prob": estimated_probability(mid, a.bias),
                         "value": estimated_probability(mid, a.bias) / cost_c})   # expected cents back per cent paid
    return pool, skipped


def multiply_single(pool, stake, multiple, top):
    """One-bet route: contracts that pay at least the multiple, but not wildly more than was asked for."""
    highest_cost = 100.0 / multiple              # pay more than this per contract and it cannot return the multiple
    lowest_cost = 100.0 / (multiple * 1.5)       # cheaper than this is a bigger longshot than was asked for
    found, seen = [], set()
    for m in sorted(pool, key=lambda m: (-m["value"], -m["vol24"])):
        if not (lowest_cost <= m["cost_c"] <= highest_cost) or m["event"] in seen:
            continue
        count = int(stake * 100 // m["cost_c"])
        if count < 1:
            continue
        seen.add(m["event"])
        cost = count * m["cost_c"] / 100.0
        found.append({**m, "route": "single", "leg": 1, "count": count, "cost": cost, "pays": float(count)})
        if len(found) >= top:
            break
    return found


def multiply_chain(pool, stake, multiple, legs, days):
    """
    Several-bets-in-a-row route. Returns the legs, or [] when no chain fits.

    The window is cut into `legs` equal slots and one bet is taken from each
    slot, so each bet has time to pay out before the next is placed. Every leg
    aims for the same share of the climb: a 5x in 2 legs needs about 2.24x per
    leg, which is a contract near 45c.
    """
    target, bank, clock, used, chain = stake * multiple, stake, 0.0, set(), []
    slot = days * 24.0 / legs
    for i in range(legs):
        left = legs - i
        need = target / bank                                   # the multiple still to go
        want_cost = 100.0 / (need ** (1.0 / left))             # cost per contract that shares the rest evenly
        last = left == 1
        options = [m for m in pool if m["event"] not in used and m["hours_left"] > clock + 4      # 4 hours for the earlier bet to pay out
                   and (last or m["hours_left"] <= slot * (i + 1))]
        if last:
            # The final leg has to actually reach the target with whole contracts. Among those that do,
            # take the most likely one, which is the most expensive contract that still gets there.
            reach = []
            for m in options:
                count = int(bank * 100 // m["cost_c"])
                if count >= 1 and bank - count * m["cost_c"] / 100.0 + count >= target - 0.005:
                    reach.append((m, count))
            if not reach:
                return []
            pick, count = max(reach, key=lambda mc: (mc[0]["est_prob"], mc[0]["vol24"]))
        else:
            near = [m for m in options if abs(m["cost_c"] - want_cost) <= 8]
            if not near:
                return []
            # Closest to the wanted price first (in 3-cent steps), then the better value, then the sooner one.
            pick = min(near, key=lambda m: (round(abs(m["cost_c"] - want_cost) / 3.0), -m["value"], m["hours_left"]))
            count = int(bank * 100 // pick["cost_c"])
            if count < 1:
                return []
        cost = count * pick["cost_c"] / 100.0
        after = bank - cost + count                            # unspent cents ride along; each winner pays $1
        chain.append({**pick, "route": f"chain-{legs}", "leg": i + 1, "count": count, "cost": cost,
                      "pays": float(count), "before": bank, "after": after})
        used.add(pick["event"])
        clock, bank = pick["hours_left"], after
    return chain


def multiply_spread(pool, stake, multiple, shots):
    """
    Several-bets-at-once route. Returns the shots, or [] when none fit.

    Buy the SAME number of contracts in each market: enough that one winner
    alone pays the target. With N contracts in each, the stake covers a
    combined price of stake / N per contract, shared between the shots.
    """
    count = int(math.ceil(stake * multiple - 1e-9))            # contracts per shot; each winner pays $1
    budget = stake * 100.0 / count                             # combined cents the shots may cost per contract
    picked, used_events, used_categories = [], set(), set()
    for i in range(shots):
        left = shots - i
        share = budget / left                                  # an even share of what is still unspent
        low, high = (share * 0.6, budget) if left == 1 else (share * 0.6, min(share * 1.25, budget - 1.0 * (left - 1)))
        options = [m for m in pool if m["event"] not in used_events and low <= m["cost_c"] <= high]
        if not options:
            return []
        # Prefer a category not used yet, so the shots are less likely to hang on the same news.
        pick = max(options, key=lambda m: (m["category"] not in used_categories, m["value"], m["vol24"]))
        picked.append({**pick, "route": f"spread-{shots}", "leg": i + 1, "count": count,
                       "cost": count * pick["cost_c"] / 100.0, "pays": float(count)})
        used_events.add(pick["event"])
        used_categories.add(pick["category"])
        budget -= pick["cost_c"]
    return picked


def route_summary(name, legs, stake, multiple):
    """Cost, payout and chance of one route, under the tool's estimate and under plain market prices."""
    est = [m["est_prob"] / 100.0 for m in legs]
    fair = [m["mid"] / 100.0 for m in legs]
    if name == "single":
        cost, pays, p_est, p_fair = legs[0]["cost"], legs[0]["pays"], est[0], fair[0]
        how = "it wins"
    elif name == "chain":
        cost, pays = legs[0]["cost"], legs[-1]["after"]
        p_est, p_fair = math.prod(est), math.prod(fair)
        how = f"all {len(legs)} win"
    else:
        cost, pays = sum(m["cost"] for m in legs), legs[0]["pays"]
        p_est, p_fair = 1 - math.prod(1 - p for p in est), 1 - math.prod(1 - p for p in fair)
        how = f"any 1 of {len(legs)} wins"
    # Expected result per attempt. For the spread, two winners pay double, so count every winner.
    expected = (sum(p * m["pays"] for p, m in zip(est, legs)) if name == "spread" else p_est * pays) - cost
    return {"name": name, "legs": legs, "bets": len(legs), "cost": cost, "pays": pays, "p_est": p_est, "p_fair": p_fair,
            "how": how, "expected": expected, "hours": max(m["hours_left"] for m in legs), "x": pays / cost if cost else 0}


def cmd_multiply(a):
    """Lay out single-bet and multi-bet ways to turn a stake into a chosen multiple inside a week."""
    if getattr(a, "sweep", None):                             # --sweep: run the whole set of variations into a text file
        return multiply_sweep(a)
    days = a.days
    note = ""
    if days > MULTIPLY_MAX_DAYS:                              # the window is never allowed past a week
        note, days = f"--days {a.days:g} is over the {MULTIPLY_MAX_DAYS:g}-day limit, so {MULTIPLY_MAX_DAYS:g} was used.", MULTIPLY_MAX_DAYS
    if days < 0.1:
        sys.exit("--days must be more than 0.1.")
    if a.stake <= 0:
        sys.exit("--stake is the dollars you want to put down, e.g. --stake 10.")
    multiple, stake, target = a.multiple, a.stake, a.stake * a.multiple
    want = {"single": {"single"}, "multi": {"chain", "spread"}, "chain": {"chain"}, "spread": {"spread"},
            "all": {"single", "chain", "spread"}}[a.route]

    rows = add_clock_fields(load_markets(a.cache_minutes, a.refresh))
    pool, skipped = multiply_pool(rows, a, days)

    # ---- build each route ----
    singles = multiply_single(pool, stake, multiple, a.top) if "single" in want else []
    chain = []
    if "chain" in want:
        # Try every chain length allowed and keep the one most likely to come in.
        tries = [multiply_chain(pool, stake, multiple, n, days) for n in range(2, a.max_bets + 1)]
        tries = [t for t in tries if t]
        chain = max(tries, key=lambda t: math.prod(m["est_prob"] / 100.0 for m in t)) if tries else []
    spread = []
    if "spread" in want:
        tries = [multiply_spread(pool, stake, multiple, n) for n in range(2, a.max_bets + 1)]
        tries = [t for t in tries if t]
        spread = max(tries, key=lambda t: 1 - math.prod(1 - m["est_prob"] / 100.0 for m in t)) if tries else []

    everything = singles + chain + spread
    save_last(everything)                                     # so `show 3` and `buy 3 --take` work on any row below
    if emit(everything, MULTIPLY_COLUMNS, a.format, a.out):
        return

    # ---- header ----
    print(banner("KALSHI", paint(f"multiply ${stake:g} by {multiple:g}x", "bold")
                 + paint(f"  =  ${target:,.2f} back, inside {days:g} day{'s' if days != 1 else ''}", "gray")))
    print(paint("screen:    ", "gray") + f"resolves within {days:g}d (limit {MULTIPLY_MAX_DAYS:g}d)  |  24h vol >= {a.min_volume_24h:g}  |  "
          f"spread <= {a.max_spread:g}c  |  " + ("only " + ", ".join(a.category) if a.category else "no " + ", ".join(sorted(skipped))))
    print(paint("candidates: ", "gray") + f"{len(pool):,} tradable sides resolve in the window"
          + paint(f"   a fair {multiple:g}x comes in {1 / multiple:.0%} of the time", "gray"))
    if note:
        print(paint(note, "yellow"))
    if not everything:
        print("\n" + paint("Nothing fits.", "yellow", "bold") + " Raise --days (up to 7), lower --min-volume-24h, widen --max-spread, "
              "or pass --skip-category with no names to include sports and crypto.")
        return

    width = shutil.get_terminal_size((120, 30)).columns
    title_w = max(22, min(56, width - 76))
    head = paint(f"{'#':>3} {'ORDER':<18} {'COST':>8} {'PAYS':>8} {'WINS':>6} {'MKT':>5} {'ENDS':>5}  MARKET", "bold", "cyan")
    row_no = [0]                                              # one running row number across all three sections

    def line(m, extra=""):
        """One bet: what to buy, what it costs, what it pays, and how likely it is."""
        row_no[0] += 1
        order = cell(f"{m['count']} {m['buy_side']} @ {m['buy_price']:g}c", 18, *side_style(m["buy_side"]))
        title = m["title"] if len(m["title"]) <= title_w else m["title"][:title_w - 3] + "..."
        cut = title.rfind(" [")
        title = paint(title, "bold") if cut <= 0 else paint(title[:cut], "bold") + paint(title[cut:], "cyan")
        print(f"{cell(row_no[0], 3, 'gray', align='>')} {order} {'$' + format(m['cost'], ',.2f'):>8} "
              f"{paint(('$' + format(m['pays'], ',.2f')).rjust(8), 'green', 'bold')} {m['est_prob']:>5.1f}% {m['mid']:>4.0f}% "
              f"{time_left(m['hours_left']):>5}  {title}{extra}")
        if not a.no_links:
            print(f"    {paint(m['ticker'], 'cyan')}  {category_tag(m['category'])}  {paint(m['url'], 'blue', 'ul')}")

    summaries = []

    # ---- route 1: one bet ----
    if "single" in want:
        print("\n" + paint(" SINGLE BET ", "bold", "green", "inv") + paint(f"  one contract near {100 / multiple:.0f}c. It wins or it does not.", "gray"))
        if singles:
            print(head)
            print(rule(min(width - 1, 62 + title_w)))
            for m in singles:
                line(m)
            summaries.append(route_summary("single", singles[:1], stake, multiple))
        else:
            print(paint(f"  No contract priced {100 / (multiple * 1.5):.0f}-{100 / multiple:.0f}c passes the screen in this window.", "yellow"))

    # ---- route 2: bets in a row ----
    if "chain" in want:
        print("\n" + paint(" CHAIN ", "bold", "yellow", "inv") + paint("  bets in a row. Each win is the stake for the next. All must win.", "gray"))
        if chain:
            print(head)
            print(rule(min(width - 1, 62 + title_w)))
            for m in chain:
                line(m, paint(f"   ${m['before']:,.2f} -> ${m['after']:,.2f}", "gray"))
            summaries.append(route_summary("chain", chain, stake, multiple))
        else:
            print(paint("  No chain of likely winners fits the window. Try --days 7 or --max-bets 4.", "yellow"))

    # ---- route 3: bets at once ----
    if "spread" in want:
        print("\n" + paint(" SPREAD ", "bold", "magenta", "inv") + paint("  bets placed together. Any one winner pays the whole target.", "gray"))
        if spread:
            print(head)
            print(rule(min(width - 1, 62 + title_w)))
            for m in spread:
                line(m)
            summaries.append(route_summary("spread", spread, stake, multiple))
        else:
            print(paint("  No set of longshots fits the stake. Try --days 7 or a lower --min-volume-24h.", "yellow"))

    if not summaries:
        return

    # ---- side by side ----
    print("\n" + paint("  SIDE BY SIDE", "bold", "cyan") + paint(f"  each route trying to turn ${stake:g} into ${target:,.2f}", "gray"))
    print(paint(f"  {'ROUTE':<8} {'BETS':>4} {'YOU PUT IN':>11} {'YOU GET':>9} {'RETURN':>7} {'CHANCE':>7} {'AT MKT PRICE':>13} "
                f"{'AVG RESULT':>11} {'DONE IN':>8}  WORKS WHEN", "bold", "cyan"))
    best = max(summaries, key=lambda s: s["p_est"])
    for s in summaries:
        tone = "green" if s is best else "white"
        chance = format(s["p_est"], ".0%")                     # worked out first so the line below stays valid on Python 3.9
        print(f"  {cell(s['name'], 8, tone, 'bold')} {s['bets']:>4} {'$' + format(s['cost'], ',.2f'):>11} {'$' + format(s['pays'], ',.2f'):>9} "
              f"{s['x']:>6.1f}x {cell(chance, 7, tone, 'bold', align='>')} {s['p_fair']:>12.0%} "
              f"{money_cell(s['expected'], 11)} {time_left(s['hours']):>8}  {s['how']}")
    print("  " + bar(best["p_est"], 40, "green") + paint(f"  {best['name']}: about {best['p_est']:.0%}", "gray"))

    # ---- ready-to-run orders ----
    cap = order_cap()
    print("\n" + paint("  ORDERS", "bold", "cyan") + paint("  dry runs. Nothing is sent until you add --live and type the word.", "gray"))

    def order_line(m):
        over = f" --max-cost {math.ceil(m['count'] * m['buy_price'] / 100.0)}" if m["count"] * m["buy_price"] / 100.0 > cap else ""
        return (f"python kalshi_cli.py buy {m['ticker']} --side {m['buy_side'].lower()} --price {m['buy_price']:g} "
                f"--count {m['count']} --take{over}")

    for s in summaries:
        label = {"single": "single bet (row 1; any other row works the same way)",
                 "chain": "chain: place the first now. Place each later one only after the one before it pays out",
                 "spread": "spread: place all of these together"}[s["name"]]
        print(paint(f"  {label}", "gray"))
        for m in s["legs"]:
            print("    " + paint(order_line(m), "bold"))

    words = [
        f"A {multiple:g}x means risking ${stake:g} to get ${target:,.2f}. If prices are fair that works {1 / multiple:.0%} of the time, "
        f"and fails the other {1 - 1 / multiple:.0%}, whichever route is used.",
        f"Highest estimated chance here: {best['name']}, about {best['p_est']:.0%}. The differences between routes are small "
        f"and come from the price-bias assumption, not from knowing the outcomes.",
        ("The chain has the likeliest individual bets, but one miss ends it. Later legs are sized from today's prices and "
         "will have moved by the time you get there, so re-run before each one." if chain else ""),
        ("The spread assumes its markets are unrelated. If they hang on the same news they win or lose together, and the "
         "real chance is lower than shown." if spread else ""),
        ("Some of these are under 15 cents. That is the guide's longshot zone, where buyers have lost the most on average."
         if any(m["buy_price"] < 15 for m in everything) else ""),
        (f"These need up to {max(m['count'] for m in everything):,} contracts at the ask. Check there is that much for sale "
         f"with `analyze N --deep` before sending anything." if max(m["count"] for m in everything) >= 100 else ""),
        "AVG RESULT is the average gain or loss per attempt under the tool's estimate. Under plain market prices it is "
        "always a small loss, because every route pays the taker fee.",
    ]
    plain_words(words)
    print("\n" + paint("Next: ", "gray") + paint("show 1", "bold") + paint("   analyze 1 --deep", "bold") + paint("   buy 1 --take", "bold")
          + paint("  (row numbers run across all three sections)", "gray"))


# ----------------------------------------------------------------------------
# Trade log and scorecard  (the `log` commands)
# ----------------------------------------------------------------------------
# One CSV file holds every trade you make. The scorecard judges it against the
# guide's rules. These commands never place an order; `log add` records one you
# have already made, in the app or with `buy`.

LOG_FILE_DEFAULT = os.environ.get("KALSHI_LOG", "trade_log.csv")

# Column order of the trade log. If you add a column, add it at the end so
# older logs still load (missing columns are filled with blanks on read).
LOG_COLUMNS = [
    "id",                # running number, assigned by `add`
    "mode",              # "real" or "paper" (paper = practice trade, no money)
    "opened_at",         # when you logged the trade, local time, ISO format
    "ticker",            # Kalshi market ticker
    "title",             # market title pulled from the API (or typed with --title)
    "niche",             # your category label, e.g. econ / weather / tech
    "side",              # "yes" or "no" - which contract you bought
    "order_type",        # "limit" or "market"
    "contracts",         # number of contracts
    "price_cents",       # price you paid per contract, for YOUR side, in cents
    "mkt_yes_bid",       # market's best YES bid when you logged it (cents)
    "mkt_yes_ask",       # market's best YES ask when you logged it (cents)
    "my_prob_yes",       # YOUR probability the market resolves YES, in percent
    "edge_pts",          # your probability of winning minus the price you paid
    "fee_usd",           # entry fee in dollars
    "cost_usd",          # contracts x price, in dollars (fee not included)
    "bankroll_usd",      # your account size when you placed the trade
    "pct_bankroll",      # cost as a percent of bankroll
    "rules_read",        # "y" if you read the market's resolution rules
    "thesis",            # one line: why your number differs from the market
    "close_time",        # when the market closes, from the API
    "status",            # "open", "settled" or "sold"
    "closed_at",         # when the trade ended
    "result",            # "win", "loss" or "sold"
    "exit_price_cents",  # price you sold at, if you sold before settlement
    "exit_fee_usd",      # fee on the sale, if any
    "payout_usd",        # money returned to you ($1 per winning contract, or sale proceeds)
    "pnl_usd",           # payout - cost - entry fee - exit fee
    "notes",             # anything else
]

# Rules from the guide, kept in one place so they are easy to change.
MAX_PCT_PER_TRADE = 3.0      # section 7: risk 1% to 3% of bankroll per trade
MIN_EDGE_PTS = 5.0           # section 5: only trade a 5+ point disagreement
LONGSHOT_CENTS = 15          # section 5: skip contracts under about 15 cents
GATE_TRADES = 100            # section 8: the Phase 1 test is 100 real trades
MONTHLY_STOP_PCT = 15.0      # section 7: down 15% in a month -> stop for the month
HARD_STOP_PCT = 40.0         # section 7: down 40% from deposits -> stop
MILESTONES = [1000, 5000, 10000, 25000]


def log_market(ticker):
    """Market details for the log, or None when the API cannot be reached (the trade is still logged)."""
    try:
        return api_get("/markets/" + urllib.parse.quote(ticker))["market"]
    except urllib.error.HTTPError as err:
        print(f"  (API returned {err.code} for {ticker})")
    except Exception as err:
        print(f"  (could not reach the API: {err})")
    return None


def now_iso():
    """Current local time as 2026-10-10T13:45:00 (no microseconds)."""
    return dt.datetime.now().replace(microsecond=0).isoformat()


def fnum(value, default=0.0):
    """Read a number out of a CSV cell. Blank or junk becomes `default`."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def taker_fee_usd(contracts, price_cents):
    """
    Kalshi's standard taker fee: 7% x contracts x P x (1 - P), rounded UP to the cent,
    where P is the price in dollars. Example: 100 contracts at 50 cents = $1.75.
    Some markets use a different rate, so treat this as an estimate and pass
    --fee with the real figure from the app when they differ.
    """
    p = price_cents / 100.0
    raw = 0.07 * contracts * p * (1.0 - p)
    # round(…, 6) first so floating point dust (1.7500000001) does not bump a cent
    return math.ceil(round(raw * 100, 6)) / 100.0


def dollars_to_cents(text):
    """API prices arrive as strings like '0.6200'. Return cents (62.0) or '' if missing."""
    if text in (None, ""):
        return ""
    return round(float(text) * 100, 2)


def load_log(path):
    """Return the log as a list of dicts. Exits with a clear message if it is missing."""
    if not os.path.exists(path):
        sys.exit(f"No log found at {path}. Create one with:  kalshi_cli.py log --file \"{path}\" init")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    # Make sure every row has every column, even if the file is from an older version.
    for row in rows:
        for col in LOG_COLUMNS:
            row.setdefault(col, "")
    return rows


def save_log(path, rows):
    """
    Write the whole log back safely:
      1. copy the current file to <name>.bak (one-step undo),
      2. write the new contents to a temp file in the same folder,
      3. swap the temp file into place in a single move.
    If the script dies halfway, the original log is still intact.
    """
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def log_init(args):
    """Create an empty log with the header row. Refuses to overwrite an existing log."""
    if os.path.exists(args.log):
        sys.exit(f"{args.log} already exists. Not overwriting it.")
    save_log(args.log, [])
    print(paint("Created ", "green", "bold") + args.log)


def log_add(args):
    """Append one trade to the log and check it against the guide's rules."""
    rows = load_log(args.log)

    # --- pull what we can from the API so you do not have to type it ---
    market = None if args.offline else log_market(args.ticker)
    title = args.title or (market and (market.get("title") or market.get("yes_sub_title"))) or ""
    bid = dollars_to_cents(market.get("yes_bid_dollars")) if market else ""
    ask = dollars_to_cents(market.get("yes_ask_dollars")) if market else ""
    close_time = market.get("close_time", "") if market else ""

    # --- the arithmetic ---
    cost = round(args.contracts * args.price / 100.0, 2)
    if args.fee is not None:
        fee = args.fee                                   # you typed the real fee
    elif args.order_type == "market":
        fee = taker_fee_usd(args.contracts, args.price)  # standard taker formula
    else:
        fee = 0.0                                        # resting limit orders are free on most markets
    # Your chance of WINNING this trade: your YES probability if you bought YES,
    # or 100 minus it if you bought NO. Edge is that minus the price you paid.
    win_prob = args.my_prob if args.side == "yes" else 100.0 - args.my_prob
    edge = round(win_prob - args.price, 2)
    pct = round(100.0 * cost / args.bankroll, 2) if args.bankroll else ""

    row = dict.fromkeys(LOG_COLUMNS, "")
    row.update({
        "id": max([int(fnum(r["id"])) for r in rows], default=0) + 1,
        "mode": "paper" if args.paper else "real",
        "opened_at": now_iso(), "ticker": args.ticker, "title": title, "niche": args.niche,
        "side": args.side, "order_type": args.order_type, "contracts": args.contracts,
        "price_cents": args.price, "mkt_yes_bid": bid, "mkt_yes_ask": ask,
        "my_prob_yes": args.my_prob, "edge_pts": edge, "fee_usd": f"{fee:.2f}",
        "cost_usd": f"{cost:.2f}", "bankroll_usd": args.bankroll or "", "pct_bankroll": pct,
        "rules_read": "y" if args.rules_read else "n", "thesis": args.thesis,
        "close_time": close_time, "status": "open", "notes": args.notes,
    })
    rows.append(row)
    save_log(args.log, rows)

    side_color = "green" if args.side == "yes" else "magenta"
    print(banner("KALSHI", paint(f"logged #{row['id']}", "green", "bold") + paint(f"  ({row['mode']} trade)", "gray")))
    print(f"\n  {paint(str(args.contracts) + ' ' + args.side.upper(), side_color, 'bold')} @ "
          f"{paint(f'{args.price:g}c', 'bold')}  {paint(title, 'white', 'bold')}")
    edge_color = "green" if edge >= MIN_EDGE_PTS else "yellow"
    print(f"  cost {paint(f'${cost:.2f}', 'bold')}   fee ${fee:.2f}   your win probability {win_prob:g}%   "
          f"edge {paint(f'{edge:+g} pts', edge_color, 'bold')}"
          + (f"   {pct}% of bankroll" if pct != "" else ""))

    # --- rule checks: these warn, they never block. The decision stays yours. ---
    warnings = []
    if edge < MIN_EDGE_PTS:
        warnings.append(f"Edge is {edge:+g} pts. The guide says only trade at {MIN_EDGE_PTS:g}+ pts.")
    if args.price < LONGSHOT_CENTS:
        warnings.append(f"Price under {LONGSHOT_CENTS}c is a longshot. These lose the most on average.")
    if pct != "" and pct > MAX_PCT_PER_TRADE:
        warnings.append(f"Size is {pct}% of bankroll. The limit is {MAX_PCT_PER_TRADE:g}%.")
    if not args.bankroll:
        warnings.append("No --bankroll given, so the sizing rule was not checked.")
    if args.order_type == "market":
        warnings.append("Market order. The guide uses limit orders except to exit a bad position.")
    if not args.rules_read:
        warnings.append("Rules not marked as read. Add --rules-read once you have read them.")
    for w in warnings:
        print("  " + paint("WARNING", "yellow", "bold") + " " + w)
    if not warnings:
        print("  " + paint("All of the guide's rules pass.", "green"))


def result_tag(result):
    """WIN in green, LOSS in red, SOLD in yellow."""
    return paint(result.upper(), {"win": "green", "loss": "red"}.get(result, "yellow"), "bold")


def close_row(row, result, payout, closed_at=None, exit_price="", exit_fee=0.0):
    """Fill in the closing columns of one row and compute its profit or loss."""
    pnl = payout - fnum(row["cost_usd"]) - fnum(row["fee_usd"]) - exit_fee
    row.update({
        "status": "sold" if result == "sold" else "settled",
        "result": result, "closed_at": closed_at or now_iso(),
        "exit_price_cents": exit_price, "exit_fee_usd": f"{exit_fee:.2f}" if exit_fee else "",
        "payout_usd": f"{payout:.2f}", "pnl_usd": f"{pnl:.2f}",
    })
    return pnl


def log_settle(args):
    """
    Close out open trades.
      * No flags: ask the API for the result of every open trade and settle the ones that have one.
      * --id N --result win|loss: settle one trade by hand (works offline).
    Each winning contract pays $1.
    """
    rows = load_log(args.log)
    changed = 0
    if args.id is not None:
        if not args.result:
            sys.exit("--id needs --result win or --result loss")
        row = next((r for r in rows if str(r["id"]) == str(args.id)), None)
        if not row:
            sys.exit(f"No trade with id {args.id}")
        payout = fnum(row["contracts"]) * 1.0 if args.result == "win" else 0.0
        pnl = close_row(row, args.result, payout)
        print(f"#{row['id']} {result_tag(args.result)}  P&L {signed_money(pnl)}")
        changed = 1
    else:
        for row in rows:
            if row["status"] != "open":
                continue
            m = log_market(row["ticker"])
            if not m or m.get("result") not in ("yes", "no"):
                continue                      # not settled yet (or scalar market): leave open
            won = m["result"] == row["side"]
            payout = fnum(row["contracts"]) * 1.0 if won else 0.0
            pnl = close_row(row, "win" if won else "loss", payout, closed_at=m.get("settlement_ts") or None)
            print(f"#{row['id']} {paint(row['ticker'], 'cyan')} resolved {m['result'].upper()} -> "
                  f"{result_tag('win' if won else 'loss')}  P&L {signed_money(pnl)}")
            changed += 1
    if changed:
        save_log(args.log, rows)
    still_open = sum(1 for r in rows if r["status"] == "open")
    print(paint(f"{changed} trade(s) closed. {still_open} still open.", "gray"))


def log_sold(args):
    """Record selling a position before the market settles."""
    rows = load_log(args.log)
    row = next((r for r in rows if str(r["id"]) == str(args.id)), None)
    if not row:
        sys.exit(f"No trade with id {args.id}")
    if row["status"] != "open":
        sys.exit(f"Trade {args.id} is already {row['status']}.")
    contracts = fnum(row["contracts"])
    # Selling at a market price costs a second taker fee unless you pass the real one.
    exit_fee = args.exit_fee if args.exit_fee is not None else taker_fee_usd(contracts, args.exit_price)
    payout = round(contracts * args.exit_price / 100.0, 2)
    pnl = close_row(row, "sold", payout, exit_price=args.exit_price, exit_fee=exit_fee)
    save_log(args.log, rows)
    print(f"#{row['id']} {result_tag('sold')} @ {args.exit_price:g}c  exit fee ${exit_fee:.2f}  P&L {signed_money(pnl)}")


def compute_stats(rows, deposits=None, include_paper=False):
    """Boil the log down to the numbers the guide cares about. Returns a dict."""
    rows = [r for r in rows if include_paper or r["mode"] != "paper"]
    closed = [r for r in rows if r["status"] in ("settled", "sold")]
    closed.sort(key=lambda r: r["closed_at"])
    pnl = sum(fnum(r["pnl_usd"]) for r in closed)
    staked = sum(fnum(r["cost_usd"]) for r in closed)
    fees = sum(fnum(r["fee_usd"]) + fnum(r["exit_fee_usd"]) for r in closed)
    decided = [r for r in closed if r["result"] in ("win", "loss")]
    wins = sum(1 for r in decided if r["result"] == "win")

    # --- calibration: is YOUR probability better than the MARKET's price? ---
    # Brier score = average of (forecast - outcome)^2. Lower is better. If yours is
    # not lower than the market's, you do not have an edge, whatever the P&L says.
    mine = mkt = 0.0
    scored = [r for r in decided if r["my_prob_yes"] != ""]
    for r in scored:
        yes_happened = 1.0 if (r["side"] == "yes") == (r["result"] == "win") else 0.0
        price = fnum(r["price_cents"]) / 100.0
        market_yes = price if r["side"] == "yes" else 1.0 - price
        mine += (fnum(r["my_prob_yes"]) / 100.0 - yes_happened) ** 2
        mkt += (market_yes - yes_happened) ** 2
    brier_me = mine / len(scored) if scored else None
    brier_mkt = mkt / len(scored) if scored else None

    # --- the Phase 1 gate: 100 closed real trades, profitable after fees ---
    n = len(closed)
    if n < GATE_TRADES:
        gate = f"IN PROGRESS ({n}/{GATE_TRADES} closed trades)"
    else:
        gate = "PASS" if pnl > 0 else "FAIL - stop here (guide, section 8)"

    # --- stops (need to know what you deposited) ---
    month = dt.date.today().strftime("%Y-%m")
    month_pnl = sum(fnum(r["pnl_usd"]) for r in closed if r["closed_at"].startswith(month))
    stops = []
    if deposits:
        start_of_month = deposits + pnl - month_pnl      # account value when the month began
        if start_of_month > 0 and month_pnl <= -MONTHLY_STOP_PCT / 100.0 * start_of_month:
            stops.append(f"MONTHLY STOP HIT: down ${-month_pnl:.2f} this month "
                         f"({-100 * month_pnl / start_of_month:.1f}%). Stop until next month.")
        if pnl <= -HARD_STOP_PCT / 100.0 * deposits:
            stops.append(f"HARD STOP HIT: down ${-pnl:.2f}, {-100 * pnl / deposits:.1f}% of deposits. Stop.")

    # running profit after each closed trade, for the dashboard chart
    curve, total = [], 0.0
    for r in closed:
        total += fnum(r["pnl_usd"])
        curve.append(round(total, 2))

    by_niche = {}
    for r in closed:
        d = by_niche.setdefault(r["niche"] or "(none)", {"n": 0, "pnl": 0.0})
        d["n"] += 1
        d["pnl"] += fnum(r["pnl_usd"])

    return {
        "n_closed": n, "n_open": sum(1 for r in rows if r["status"] == "open"),
        "open_cost": sum(fnum(r["cost_usd"]) for r in rows if r["status"] == "open"),
        "pnl": pnl, "staked": staked, "fees": fees,
        "roi": 100 * pnl / staked if staked else None,
        "wins": wins, "decided": len(decided),
        "win_rate": 100 * wins / len(decided) if decided else None,
        "avg_price": sum(fnum(r["price_cents"]) for r in decided) / len(decided) if decided else None,
        "brier_me": brier_me, "brier_mkt": brier_mkt, "n_scored": len(scored),
        "market_orders": sum(1 for r in rows if r["order_type"] == "market"),
        "oversized": sum(1 for r in rows if fnum(r["pct_bankroll"]) > MAX_PCT_PER_TRADE),
        "thin_edge": sum(1 for r in rows if r["edge_pts"] != "" and fnum(r["edge_pts"]) < MIN_EDGE_PTS),
        "n_total": len(rows), "gate": gate, "stops": stops, "month_pnl": month_pnl,
        "curve": curve, "by_niche": by_niche, "deposits": deposits,
    }


def log_stats(args):
    """Print the scorecard."""
    s = compute_stats(load_log(args.log), args.deposits, args.include_paper)
    if args.json:
        # Everything the scorecard shows, as data you can feed to another program.
        print(json.dumps(s, indent=2))
        return
    label = lambda text: paint(f"  {text:<20}", "gray")

    print(banner("KALSHI", paint("scorecard", "bold") + paint(f"  {args.log}", "gray")))
    print()
    print(label("Profit after fees") + signed_money(s["pnl"])
          + paint("    this month ", "gray") + signed_money(s["month_pnl"])
          + paint(f"    fees paid ${s['fees']:,.2f}", "gray"))
    if s["roi"] is not None:
        tone = "green" if s["roi"] > 0 else "red"
        print(label("Return on stake") + paint(f"{s['roi']:+.1f}%", tone, "bold") + paint(f"    staked ${s['staked']:,.2f}", "gray"))
    print(label("Trades") + paint(f"{s['n_closed']} closed", "bold") + f"    {s['n_open']} open "
          + paint(f"(${s['open_cost']:,.2f} at risk)", "gray"))
    if s["win_rate"] is not None:
        # You only profit when your win rate beats the average price you paid.
        gap = s["win_rate"] - s["avg_price"]
        tone = "green" if gap > 0 else "red"
        print(label("Win rate") + paint(f"{s['win_rate']:.1f}%", tone, "bold") + f" ({s['wins']}/{s['decided']})"
              + paint(f"    average price paid {s['avg_price']:.1f}c    ", "gray")
              + paint(f"{'ahead' if gap > 0 else 'behind'} by {abs(gap):.1f} pts", tone))
    if s["brier_me"] is not None:
        better = s["brier_me"] < s["brier_mkt"]
        print(label("Calibration") + "you " + paint(f"{s['brier_me']:.4f}", "bold")
              + f"  market {s['brier_mkt']:.4f}" + paint("  (lower is better)    ", "gray")
              + (paint("you are beating the market's prices", "green") if better
                 else paint("the market's prices are beating you", "red")))
    slips = s["market_orders"] + s["oversized"] + s["thin_edge"]
    print(label("Rule slips") + paint(str(slips), "green" if slips == 0 else "yellow", "bold")
          + paint(f"    {s['market_orders']} market orders, {s['oversized']} over {MAX_PCT_PER_TRADE:g}% size, "
                  f"{s['thin_edge']} under {MIN_EDGE_PTS:g}-pt edge (of {s['n_total']} trades)", "gray"))

    # --- the 100-trade test ---
    print("\n" + paint("  100-TRADE TEST", "bold", "cyan"))
    done = min(1.0, s["n_closed"] / GATE_TRADES)
    if s["n_closed"] < GATE_TRADES:
        verdict = paint("IN PROGRESS", "yellow", "bold")
    elif s["pnl"] > 0:
        verdict = paint("PASS", "green", "bold")
    else:
        verdict = paint("FAIL - stop here (guide, section 8)", "red", "bold")
    print(f"  {bar(done, 40, 'cyan')} {s['n_closed']}/{GATE_TRADES}  {verdict}")

    # --- milestones ---
    print("\n" + paint("  MILESTONES", "bold", "cyan"))
    for m in MILESTONES:
        pct = max(0.0, min(100.0, 100 * s["pnl"] / m))
        reached = paint("  REACHED", "green", "bold") if s["pnl"] >= m else ""
        print(f"  {f'${m:,}':>8}  {bar(pct / 100, 40, 'green')} {pct:5.1f}%{reached}")

    if s["by_niche"]:
        print("\n" + paint("  BY NICHE", "bold", "cyan"))
        for name, v in sorted(s["by_niche"].items(), key=lambda kv: -kv[1]["pnl"]):
            print(f"  {cell(name, 12, 'bold')} {v['n']:>4} trades   {signed_money(v['pnl'])}")

    print("\n" + paint("  STOPS", "bold", "cyan"))
    if not args.deposits:
        print(paint("  Not checked. Pass --deposits AMOUNT to check the monthly stop and hard stop.", "gray"))
    elif not s["stops"]:
        print(paint(f"  Clear. Monthly {MONTHLY_STOP_PCT:g}% and hard {HARD_STOP_PCT:g}% stops not hit.", "green"))
    for stop in s["stops"]:
        print("  " + paint(stop, "red", "bold"))

    # --- the same numbers as a few sentences ---
    words = []
    if s["n_closed"] == 0:
        words.append("No closed trades yet, so there is nothing to score. Log trades with `add` and close them with `settle`.")
    else:
        left = GATE_TRADES - s["n_closed"]
        words.append(f"You are {'up' if s['pnl'] >= 0 else 'down'} ${abs(s['pnl']):,.2f} after fees on {s['n_closed']} closed trades"
                     + (f", with {left} to go before the 100-trade test means anything." if left > 0 else "."))
        if s["win_rate"] is not None:
            gap = s["win_rate"] - s["avg_price"]
            words.append(f"You won {s['win_rate']:.0f}% of the time while paying {s['avg_price']:.0f}c on average. "
                         + ("That gap is where the profit comes from." if gap > 0 else
                            "You need to win more often than the price you pay, so this is losing ground."))
        if s["brier_me"] is not None:
            words.append("Your probabilities have been closer to what happened than the market's prices were. That is the real sign of an edge."
                         if s["brier_me"] < s["brier_mkt"] else
                         "The market's prices have been closer to what happened than your probabilities. Any profit so far may be luck.")
        if s["n_closed"] < 30:
            words.append("With fewer than 30 trades, all of this can still be chance.")
        slips = s["market_orders"] + s["oversized"] + s["thin_edge"]
        if slips:
            words.append(f"You broke your own rules {slips} time(s). Those trades are worth rereading in the log.")
        if s["by_niche"] and len(s["by_niche"]) > 1:
            best = max(s["by_niche"].items(), key=lambda kv: kv[1]["pnl"])
            worst = min(s["by_niche"].items(), key=lambda kv: kv[1]["pnl"])
            if best[0] != worst[0]:
                words.append(f"Best niche so far: {best[0]}. Weakest: {worst[0]}.")
    print("\n" + paint("  IN PLAIN WORDS", "bold", "cyan"))
    for line in words:
        print("  " + paint("-", "gray") + " " + line)


def cmd_fee(args):
    """Fee calculator for one order."""
    fee = taker_fee_usd(args.contracts, args.price)
    cost = args.contracts * args.price / 100.0
    print(banner("KALSHI", paint("fee calculator", "gray")))
    print(f"\n  {paint(f'{args.contracts:g} contracts at {args.price:g}c', 'bold')}   cost {paint(f'${cost:.2f}', 'bold')}")
    print(f"  {cell('market order', 13, 'yellow')} fee {paint(f'${fee:.2f}', 'yellow', 'bold')} ({100 * fee / cost:.2f}% of cost)"
          f"   break-even: true odds of {paint(f'{args.price + 100 * fee / args.contracts:.2f}%+', 'bold')}")
    print(f"  {cell('limit order', 13, 'green')} fee {paint('$0.00', 'green', 'bold')} on most markets "
          + paint("(about a quarter of the market-order fee on some)", "gray"))


def log_fills(args):
    """
    Download your raw fills from Kalshi into a separate CSV (needs an API key).
    This is your tax record and a way to reconcile the log against what actually
    filled. It writes exactly what the API returns and does not touch the trade log.
    """
    fills, cursor = [], ""
    while True:                                # the API returns pages; follow the cursor to the end
        page = api_get("/portfolio/fills", {"limit": 1000, "cursor": cursor}, need_auth=True)
        fills.extend(page.get("fills", []))
        cursor = page.get("cursor") or ""
        if not cursor or not page.get("fills"):
            break
    if not fills:
        print("No fills returned.")
        return
    cols = sorted({k for f in fills for k in f})
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(fills)
    print(paint("Wrote ", "green", "bold") + f"{len(fills)} fills to {args.out}")


# ----------------------------------------------------------------------------
# Daily: the morning run-through
# ----------------------------------------------------------------------------

def dispatch(argv, inherit=None):
    """Run another command of this tool in-process, e.g. dispatch(["best", "3"])."""
    args = build_parser().parse_args(argv)
    if inherit is not None:
        args.cache_minutes, args.refresh = inherit.cache_minutes, False
    finish_args(args)
    args.func(args)


def cmd_daily(a):
    """One command for the start of a session: your watchlist, what looks odd, the top picks, and your scorecard."""
    steps = [("YOUR WATCHLIST", ["watchlist"]),
             ("WHAT LOOKS ODD TODAY", ["anomalies", "--top", "3"]),
             ("TOP PICKS BY SCORE", ["best", str(a.picks), "--bankroll", str(a.bankroll)])]
    if os.path.exists(os.path.expanduser(LOG_FILE_DEFAULT)):
        steps.append(("YOUR SCORECARD", ["log", "stats"]))
    if a.refresh:
        load_markets(a.cache_minutes, True)            # one fresh download, shared by every step below
    for title, argv in steps:
        print("\n" + paint("=" * 20 + f"  {title}  " + "=" * 20, "bold", "yellow"))
        try:
            dispatch(argv, inherit=a)
        except SystemExit as stop:                      # one step failing should not end the run
            if stop.code not in (None, 0):
                print(paint(f"  (skipped: {stop.code})", "gray"))


# ----------------------------------------------------------------------------
# Examples
# ----------------------------------------------------------------------------

EXAMPLES = [
    ("Start here", "examples", "This list. Add --search WORD to filter it."),
    ("Start here", "categories", "Every category with open markets, counts, and its share of today's volume."),
    ("Start here", "daily", "The morning run-through: watchlist, anomalies, top picks and your scorecard in one go."),
    ("Start here", "serve", "Open the dashboard at http://127.0.0.1:8765. Same commands, same output, with buttons."),
    ("Start here", "firehose --open", "Run EVERY command and bind the output into one color PDF with clickable contents, then open it."),
    ("Start here", "--refresh categories", "Put --refresh before any command to re-download the market list instead of using the saved copy."),

    ("Find markets", "top --category economics --top 10", "The 10 most traded economics markets in the last 24 hours."),
    ("Find markets", "top --all --top 100", "The 100 busiest markets on the whole exchange."),
    ("Find markets", "top --all --under 10 --top 20", "The 20 busiest contracts costing 10 cents or less. Longshots; the guide says to skip them."),
    ("Find markets", "top --category politics elections --over 85 --top 15", "Heavy favorites (85 cents and up) in two categories at once."),
    ("Find markets", "top --search fed rate --top 5", "Keyword search. A market must contain every word."),
    ("Find markets", "top --search bitcoin ethereum --any-word --exclude daily", "Match either word, and drop anything containing 'daily'."),
    ("Find markets", "top --skip-category sports crypto --top 20", "The busiest markets outside sports and crypto."),
    ("Find markets", "top --preset movers --min-volume-24h 5000", "Biggest price changes since yesterday, in markets that actually trade."),
    ("Find markets", "top --preset closing-soon --category weather", "Weather markets that resolve within two days."),
    ("Find markets", "top --preset wide --top 10", "Bid-ask gaps of 5 to 20 cents with real volume: room to rest a limit order."),
    ("Find markets", "top --preset guide --top 10", "Markets that fit the guide: 60 to 95 cents, resolving within 30 days, active."),
    ("Find markets", "top --side no --min-price 60 --max-price 80 --closes-within 7", "NO contracts priced 60 to 80 cents that resolve this week."),
    ("Find markets", "top --category tech ai --sort interest --one-per-event", "Tech and AI ranked by open interest, one market per event."),
    ("Find markets", "top --series KXFED --sort closing", "Every market in one series, soonest first."),
    ("Find markets", "top --category economics --top 3 --open 3", "Show the top 3 and open each one's kalshi.com page in your browser."),

    ("Look closer", "show KXFEDDECISION-26OCT-H0", "One market live: both sides, fee, break-even, rules and the order book."),
    ("Look closer", "show 2", "The same for row 2 of whatever list you printed last. Row numbers work everywhere."),
    ("Look closer", "show 2 --full-rules", "Include the secondary rules text. Read it before trading anything."),
    ("Look closer", "event KXFEDDECISION-26OCT", "Every outcome in one event, with the prices added up."),
    ("Look closer", "ladder KXFEDDECISION-26OCT", "The event as a probability distribution: fair odds with the margin removed, or the implied median for a threshold ladder."),
    ("Look closer", "analyze KXFEDDECISION-26OCT-H0", "Fourteen days of price history as a chart, with range, typical move, trend and volume."),
    ("Look closer", "analyze 1 --interval hour --days 3", "The same at hourly resolution for the last three days."),
    ("Look closer", "analyze 1 --deep", "Drill all the way down: adds the order book depth and the last 15 trades with who was buying."),
    ("Look closer", "analyze 1 --trades 40 --book", "Pick the drill-downs yourself: the last 40 trades and the book."),
    ("Look closer", "watch KXFEDDECISION-26OCT-H0 --every 30", "Re-check every 30 seconds and print a line when the price moves. Ctrl+C stops it."),
    ("Look closer", "open 1", "Open row 1's page on kalshi.com."),

    ("Find an edge", "anomalies", "Scan everything for prices that break arithmetic or break their own pattern."),
    ("Find an edge", "anomalies --type overround ladder", "Only the arithmetic breaks: outcome sets and threshold ladders that guarantee a profit on paper."),
    ("Find an edge", "anomalies --type move --checks 40", "Moves measured against each market's own normal day, checking the 40 biggest movers."),
    ("Find an edge", "anomalies --category economics weather --top 10", "Limit the scan to two categories and show 10 per section."),
    ("Find an edge", "--refresh anomalies --type surge dislocation", "Fresh prices, then volume surges and quotes that have left the last trade behind."),
    ("Find an edge", "best", "The single top-scoring market after the guide's screen."),
    ("Find an edge", "best 3", "The top three by score. Also: best 5, best 10."),
    ("Find an edge", "best 5 --category economics weather --bankroll 2500", "Top five inside two categories, sized for a $2,500 bankroll."),
    ("Find an edge", "best 5 --by odds", "The five with the best odds of winning, instead of the best score. Small payouts, high hit rate."),
    ("Find an edge", "best 5 --by soonest --closes-within 2", "The five that resolve first, so the money comes back quickly."),
    ("Find an edge", "grind --start 10 --target 40", "Chain likely winners to compound $10 toward $40, with the real odds of the whole chain."),
    ("Find an edge", "grind --start 10 --target 16 --stake 50 --closes-within 2", "A shorter climb, risking half the bankroll per step instead of all of it."),
    ("Find an edge", "grind --start 25 --target 100 --min-price 90 --skip-category", "Only 90c-and-up favorites, with sports and crypto included."),
    ("Find an edge", "multiply 2x --stake 10", "Ways to turn $10 into $20 within three days: one bet, a chain of bets, and a spread of bets, side by side with the odds of each."),
    ("Find an edge", "multiply 3x --stake 20", "The same for $20 into $60. The single bet is a contract near 33c; the chain is two or three likelier bets in a row."),
    ("Find an edge", "multiply 5x --stake 20 --days 5", "Aim for 5x and let the bets run up to five days out instead of the default three."),
    ("Find an edge", "multiply 10x --stake 100 --days 7", "$100 into $1,000 over the longest window allowed. Seven days is the ceiling; a bigger number is cut back to 7."),
    ("Find an edge", "multiply 5x --stake 10 --route single --top 10", "Only the one-bet route: the ten best contracts that pay 5x or a little more."),
    ("Find an edge", "multiply 3x --stake 20 --route multi --max-bets 4", "Only the several-bet routes, allowing up to four bets in the chain or the spread."),
    ("Find an edge", "multiply 2x --stake 100 --route chain --category weather economics", "A 2x built only from bets in a row, inside two categories."),
    ("Find an edge", "multiply --sweep", "Run 16 multiply variations in one go and write each command with its output to multiply_results.txt."),
    ("Find an edge", "multiply --sweep my_runs.txt", "The same sweep, written to a file name you choose."),
    ("Find an edge", "x 5x -s 20 -d 2 --format json --out multiply.json", "Short form of the command, with the bets written to a file for a spreadsheet or script."),
    ("Find an edge", "picks --top 10 --bankroll 500", "Ten picks as a weighted random draw from the best 60, one per event, with basket odds."),
    ("Find an edge", "picks --top 5 --seed 7", "Fix the random draw so the same five come back."),
    ("Find an edge", "picks --top 5 --bias 0", "Turn the pricing-pattern adjustment off: what the picks look like if the market is simply right."),
    ("Find an edge", "picks --top 8 --closes-within 3 --min-volume-24h 5000", "Only liquid markets that resolve within three days."),

    ("Decide", "brief KXFEDDECISION-26OCT-H0 --copy", "Write a research prompt for an AI assistant and copy it to the clipboard. Paste it, get a probability back."),
    ("Decide", "brief 1 --out research.txt", "Save the research prompt to a file instead."),
    ("Decide", "plan KXFEDDECISION-26OCT-H0 --my-prob 92 --bankroll 500", "Your probability in, edge, expected value, Kelly size, checklist and verdict out."),
    ("Decide", "plan 1 --my-prob 30 --bankroll 2500 --kelly 0.5 --max-risk 2", "Half Kelly instead of a quarter, capped at 2% of bankroll."),
    ("Decide", "fee --price 55 --contracts 100", "What a market order would cost in fees, and the break-even it implies."),
    ("Decide", "watchlist add KXFEDDECISION-26OCT-H0 --note \"wait for CPI\"", "Remember a market and the price you first saw."),
    ("Decide", "watchlist", "Your saved markets with live prices and the move since you added each."),

    ("Trade", "buy 1", "After plan or picks: preview the suggested order for row 1. A dry run; nothing is sent."),
    ("Trade", "buy KXFEDDECISION-26OCT-H0 --side yes --price 83 --count 10 --my-prob 92 --bankroll 500", "Preview a specific limit order with the edge and sizing checks."),
    ("Trade", "buy 1 --spend 15", "Size by dollars instead of contracts."),
    ("Trade", "buy 1 --live", "Send it for real. Asks you to type BUY first. Needs your API key."),
    ("Trade", "--demo buy 1 --live", "The same against Kalshi's practice exchange, with practice money and a demo key."),
    ("Trade", "orders", "Orders still resting on the book."),
    ("Trade", "cancel ORDER_ID --live", "Cancel one resting order. Without --live it only shows what it would do."),
    ("Trade", "positions", "Cash balance and what you hold, with what each would fetch now."),
    ("Trade", "sell 1 --side yes --count 5 --price 66", "Preview selling 5 contracts of row 1 from positions. Checked against what you hold."),

    ("Keep score", "log init", "Create the trade log (trade_log.csv, or the path in KALSHI_LOG)."),
    ("Keep score", "log add --ticker KXFEDDECISION-26OCT-H0 --side yes --price 83 --contracts 10 --my-prob 92 --bankroll 500 --niche econ --rules-read --thesis \"no hike signalled\"", "Record a trade with your probability and reasoning."),
    ("Keep score", "log add --ticker TICKER --side no --price 70 --contracts 5 --my-prob 20 --paper", "A practice trade that stays out of the scorecard."),
    ("Keep score", "log settle", "Look up results and close every trade whose market has resolved."),
    ("Keep score", "log sold --id 7 --exit-price 71", "Record selling a position before it settled."),
    ("Keep score", "log stats --deposits 500", "The scorecard: profit, win rate, calibration, the 100-trade test, milestones and stops."),
    ("Keep score", "log --file ~/kalshi/2026.csv stats --json", "A different log file, with the scorecard as JSON."),
    ("Keep score", "log fills --out fills_2026.csv", "Download your raw fills from Kalshi for tax records. Needs your API key."),

    ("Output", "top -c weather -n 20 --format markdown --out weather.md", "Any list as a Markdown table in a file. Also csv and json."),
    ("Output", "picks -n 10 --format csv --out picks.csv", "Picks as a spreadsheet."),
    ("Output", "analyze 1 --format json", "Statistics and price history as JSON for another program."),
    ("Output", "--color never top --category economics", "Plain text with no color codes. Color also switches off when you pipe to a file."),
    ("Output", "serve --port 9000", "The dashboard on a different port."),
    ("Output", "firehose --theme light --out report.pdf", "The everything-report on a white background, for printing."),
    ("Output", "--refresh firehose --ticker KXFEDDECISION-26OCT-H0 --my-prob 92 --bankroll 500 --open", "Fresh prices, a market of your choosing, and your own probability in the plan."),
]


def cmd_examples(a):
    """Print the example commands, grouped, with what each one does."""
    words = [w.lower() for w in (a.search or [])]
    chosen = [e for e in EXAMPLES if all(w in (e[0] + " " + e[1] + " " + e[2]).lower() for w in words)]
    if a.markdown:
        print(f"# Example commands\n\n{len(EXAMPLES)} commands, grouped by what you are trying to do. "
              "Each is run as `python kalshi_cli.py <command>`.\n\nA row number from the last list (`1`, `2`, ...) "
              "works anywhere a ticker does. Replace `TICKER` and `ORDER_ID` with real values.")
        group = None
        for g, command, text in chosen:
            if g != group:
                print(f"\n## {g}\n\n| Command | What it does |\n|---|---|")
                group = g
            shown = command.replace("|", "\\|")
            print(f"| `python kalshi_cli.py {shown}` | {text} |")
        return
    print(banner("KALSHI", paint(f"{len(chosen)} example commands", "bold") + paint("  run each as: python kalshi_cli.py <command>", "gray")))
    group, n = None, 0
    for g, command, text in chosen:
        if g != group:
            print("\n" + paint(f"  {g.upper()}", "bold", "cyan"))
            group = g
        n += 1
        print(f"  {cell(n, 3, 'gray', align='>')} {paint(command, 'bold')}")
        print(f"      {paint(text, 'gray')}")
    if not chosen:
        print(paint("\n  Nothing matches. Try one word, such as: examples --search volume", "yellow"))
    print("\n" + paint("A row number from the last list works anywhere a ticker does. Add -h to any command for its options.", "gray"))


# ----------------------------------------------------------------------------
# Serve: the same tool as a local dashboard
# ----------------------------------------------------------------------------
# The dashboard does not have its own logic. Each button builds a command, the
# server runs this same script with that command, and the page shows the output
# with the same colors. So the dashboard and the terminal can never disagree.
#
# Safety: it listens on 127.0.0.1 only, every request must carry a secret token
# that is baked into the page (so another website cannot send commands to it),
# and live orders are refused unless you start it with --allow-live.

ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")
URL_RE = re.compile(r"(https?://[^\s<]+)")
TICKER_SPAN_RE = re.compile(r'<span class="a96">([A-Z0-9][A-Z0-9._-]{4,})</span>')

RUNNABLE = {"categories", "top", "picks", "best", "grind", "multiply", "x", "anomalies", "ladder", "show", "analyze", "event", "plan", "brief",
            "watchlist", "daily", "examples", "fee", "buy", "sell", "orders", "cancel", "positions", "log", "open", "watch"}


def ansi_to_html(text):
    """Turn colored terminal output into HTML spans, then make links and tickers clickable."""
    out, open_spans, pos = [], 0, 0
    for match in ANSI_RE.finditer(text):
        out.append(html.escape(text[pos:match.start()], quote=False))
        pos = match.end()
        codes = [c for c in match.group(1).split(";") if c]
        if not codes or codes == ["0"]:
            out.append("</span>" * open_spans)
            open_spans = 0
        else:
            out.append('<span class="' + " ".join("a" + c for c in codes) + '">')
            open_spans += 1
    out.append(html.escape(text[pos:], quote=False) + "</span>" * open_spans)
    page = "".join(out)
    page = URL_RE.sub(r'<a href="\1" target="_blank" rel="noopener">\1</a>', page)
    return TICKER_SPAN_RE.sub(r'<span class="a96 tk" data-t="\1">\1</span>', page)


def run_for_dashboard(command, columns, allow_live, confirm_word):
    """Validate one command from the page, run this script with it, and return (html, exit_code)."""
    try:
        argv = shlex.split(command)
    except ValueError as err:
        return html.escape(f"Could not read that command: {err}"), 2
    while argv and (argv[0].lower() in ("python", "python3", "py") or argv[0].lower().endswith("kalshi_cli.py")):
        argv = argv[1:]                                # people paste the whole line; drop the "python kalshi_cli.py" part
    names = [x for x in argv if not x.startswith("-")]
    if not names or names[0] not in RUNNABLE:
        return html.escape("Type a command such as:  top --category economics   |   best 3   |   examples"), 2
    if "--yes" in argv:
        return html.escape("--yes is not accepted from the dashboard."), 2
    if "--live" in argv:
        word = {"buy": "BUY", "sell": "SELL", "cancel": "CANCEL"}.get(names[0])
        if not allow_live:
            return ansi_to_html(paint("Live orders are switched off in this dashboard.", "yellow", "bold")
                                + "\nRemove --live to see the dry run, or restart with:  python kalshi_cli.py serve --allow-live"), 2
        if not word or confirm_word != word:
            return html.escape(f"Not sent. The confirmation word was not typed."), 2
        argv.append("--yes")                           # the typed word in the page stands in for the terminal prompt
    if names[0] == "watch" and "--count" not in argv:
        argv += ["--count", "3"]                       # a watch with no end would hang the request
    env = dict(os.environ, COLUMNS=str(columns), LINES="50", PYTHONIOENCODING="utf-8")
    env.pop("NO_COLOR", None)
    try:
        done = subprocess.run([sys.executable, os.path.abspath(__file__), "--color", "always"] + argv,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=420,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return html.escape("That took longer than 7 minutes and was stopped."), 1
    text = done.stdout
    if done.stderr.strip():
        text += ("\n" if text else "") + "\x1b[90m" + done.stderr.rstrip() + "\x1b[0m"
    return ansi_to_html(text.rstrip("\n")), done.returncode


DASHBOARD_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Kalshi CLI</title>
<style>
:root{--bg:#0c0c0c;--panel:#141416;--line:#2a2a2e;--ink:#d4d4d4;--dim:#8a8a8a;--cyan:#29d9e6;--field:#1c1c20}
*{box-sizing:border-box} html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:13.5px/1.42 Consolas,"Cascadia Mono","DejaVu Sans Mono",Menlo,monospace}
.app{display:grid;grid-template-columns:292px 1fr;height:100vh}
aside{background:var(--panel);border-right:1px solid var(--line);overflow-y:auto;padding:12px 12px 30px}
main{display:flex;flex-direction:column;min-width:0}
h1{font-size:14px;margin:0 0 2px;color:#000;background:var(--cyan);display:inline-block;padding:1px 8px;font-weight:700}
.sub{color:var(--dim);font-size:12px;margin:6px 0 10px}
h2{font-size:11.5px;letter-spacing:.08em;color:var(--cyan);margin:16px 0 6px;font-weight:700}
button,select,input{font:inherit;color:var(--ink);background:var(--field);border:1px solid var(--line);border-radius:5px;padding:5px 8px}
button{cursor:pointer} button:hover{border-color:var(--cyan);color:#fff}
button.go{background:#12343a;border-color:#1f5f69;color:#e8feff}
.row{display:flex;gap:6px;margin:5px 0} .row>*{flex:1 1 0;min-width:0}
.w2{flex:0 0 74px!important}
label{display:block;color:var(--dim);font-size:11.5px;margin:6px 0 2px}
.bar{display:flex;gap:8px;padding:10px 12px;border-bottom:1px solid var(--line);background:var(--panel);align-items:center}
.bar span{color:var(--dim);white-space:nowrap} #cmd{flex:1;min-width:0}
.sel{display:flex;gap:6px;padding:7px 12px;border-bottom:1px solid var(--line);align-items:center;flex-wrap:wrap}
.sel b{color:var(--cyan);font-weight:400} .sel button{padding:2px 8px;font-size:12.5px}
#out{flex:1;overflow:auto;padding:4px 14px 40px}
.blk{border-bottom:1px solid var(--line);padding:10px 0 14px}
.blk .p{color:var(--dim)} .blk .p b{color:#f2f2f2;font-weight:400}
pre{margin:0;white-space:pre;font:inherit} .busy{color:var(--cyan)}
a{color:inherit} .tk{cursor:pointer;border-bottom:1px dotted #29d9e6} .tk:hover{background:#12343a}
.a1{font-weight:700}.a2{opacity:.7}.a4{text-decoration:underline}.a7{background:var(--cyan);color:#000;padding:0 1px}
.a90{color:#8a8a8a}.a91{color:#ff5f6b}.a92{color:#3ddc5a}.a93{color:#f2d544}.a94{color:#6fa8ff}.a95{color:#f25ad6}.a96{color:#29d9e6}.a97{color:#fff}
.a91.a7{background:#ff5f6b;color:#000}.a93.a7{background:#f2d544;color:#000}.a96.a7{background:#29d9e6;color:#000}
@media (max-width:820px){.app{grid-template-columns:minmax(0,1fr);height:auto}aside{border-right:0;border-bottom:1px solid var(--line)}#out{overflow-x:auto;overflow-y:visible}.bar span{display:none}}
</style></head><body><div class="app">
<aside>
  <h1>KALSHI</h1><div class="sub">Every button runs a command and shows exactly what the terminal would. Click any ticker to select it.</div>

  <h2>FIND</h2>
  <div class="row"><button data-c="categories">Categories</button><button data-c="daily">Daily run</button></div>
  <div class="row"><select id="cat"><option value="">all categories</option></select><select id="n" class="w2"><option>5</option><option selected>10</option><option>20</option><option>50</option><option>100</option></select></div>
  <div class="row"><select id="sort"></select></div>
  <div class="row"><button class="go" id="btop">Top markets</button></div>
  <div class="row"><select id="preset"></select><button id="bpreset" class="w2">Run</button></div>
  <div class="row"><input id="q" placeholder="search words"><button id="bq" class="w2">Search</button></div>
  <label>Price range in cents (optional)</label>
  <div class="row"><input id="pmin" placeholder="min"><input id="pmax" placeholder="max"><select id="pside"><option value="either">either side</option><option value="yes">YES</option><option value="no">NO</option></select></div>

  <h2>FIND AN EDGE</h2>
  <div class="row"><button data-c="best">Best 1</button><button data-c="best 3">Best 3</button></div>
  <div class="row"><button data-c="best 5">Best 5</button><button data-c="best 10">Best 10</button></div>
  <div class="row"><button data-c="anomalies">Anomalies</button><button id="bpicks">Random picks</button></div>
  <label>Bankroll for sizing ($)</label>
  <div class="row"><input id="bank" value="500"></div>
  <label>Grind: compound from $ to $</label>
  <div class="row"><input id="gs" value="10"><input id="gt" value="40"><button id="bgrind">Grind</button></div>
  <label>Multiply: stake $, target, days (max 7)</label>
  <div class="row"><input id="ms" value="10"><select id="mx"><option>2x</option><option>3x</option><option>5x</option><option>10x</option></select><input id="md" value="3"></div>
  <div class="row"><select id="mr"><option value="all">single + multi</option><option value="single">single bet</option><option value="multi">multiple bets</option></select><button id="bmult">Multiply</button></div>
  <div class="row"><button data-c="best 5 --by odds">Best odds 5</button><button data-c="best 5 --by soonest">Soonest 5</button></div>

  <h2>SELECTED MARKET</h2>
  <div class="row"><input id="tk" placeholder="ticker or row number"></div>
  <div class="row"><button data-t="show">Show</button><button data-t="analyze">Analyze</button><button data-t="analyze" data-x="--deep">Deep</button></div>
  <div class="row"><button data-t="ladder">Ladder</button><button data-t="brief">AI brief</button><button data-t="watchlist add">Watch</button></div>
  <label>Your probability of YES (%)</label>
  <div class="row"><input id="prob" placeholder="e.g. 70"><button class="go" id="bplan">Plan</button></div>
  <label>Order (dry run)</label>
  <div class="row"><select id="side"><option>yes</option><option>no</option></select><input id="price" placeholder="price c"><input id="count" placeholder="count"></div>
  <div class="row"><button id="bbuy">Preview buy</button><button id="bsell">Preview sell</button></div>

  <h2>ACCOUNT AND SCORE</h2>
  <div class="row"><button data-c="positions">Positions</button><button data-c="orders">Orders</button><button data-c="watchlist">Watchlist</button></div>
  <div class="row"><button data-c="log stats">Scorecard</button><button data-c="log settle">Settle log</button></div>

  <h2>HELP</h2>
  <div class="row"><button data-c="examples">Example commands</button></div>
</aside>
<main>
  <div class="bar"><span>PS&gt; python kalshi_cli.py</span><input id="cmd" placeholder="type any command, e.g.  top --category economics --top 10" autofocus><button class="go" id="run">Run</button></div>
  <div class="sel"><span>selected:</span> <b id="selname">none (click a ticker in the output)</b></div>
  <div id="out"><div class="blk"><pre><span class="a1 a96 a7"> KALSHI </span>  <span class="a1">dashboard</span>

  Pick something on the left, or type a command above. The first list takes 1 to 2 minutes
  while every open market is downloaded; after that it is saved for 30 minutes.

  <span class="a90">Good places to start:</span>  <span class="a1">daily</span>    <span class="a1">best 3</span>    <span class="a1">anomalies</span>    <span class="a1">examples</span>
  <span class="a90">Orders:</span>  buy, sell and cancel are dry runs here%LIVE_NOTE%.</pre></div></div>
</main></div>
<script>
const TOKEN="%TOKEN%", ALLOW_LIVE=%ALLOW_LIVE%;
const CATS=%CATS%, SORTS=%SORTS%, PRESETS=%PRESETS%;
const $=id=>document.getElementById(id), out=$("out");
CATS.forEach(c=>$("cat").add(new Option(c,c)));
SORTS.forEach(s=>$("sort").add(new Option("sort: "+s,s)));
PRESETS.forEach(p=>$("preset").add(new Option("preset: "+p,p)));
let hist=[],hi=0;
// how many characters fit across the output pane, so tables size themselves like a terminal would
function cols(){const probe=document.createElement("span");probe.textContent="0".repeat(50);probe.style.visibility="hidden";out.appendChild(probe);
  const w=probe.getBoundingClientRect().width/50;probe.remove();return Math.max(100,Math.min(190,Math.floor((out.clientWidth-30)/w)));}
async function run(cmd){
  cmd=cmd.trim(); if(!cmd) return;
  let word="";
  if(/(^|\s)--live(\s|$)/.test(cmd)&&ALLOW_LIVE){
    const need=cmd.startsWith("sell")?"SELL":cmd.startsWith("cancel")?"CANCEL":"BUY";
    word=prompt("This sends a REAL order. Type "+need+" to confirm.")||""; }
  hist.push(cmd);hi=hist.length;
  const blk=document.createElement("div");blk.className="blk";
  blk.innerHTML='<div class="p">PS&gt; python kalshi_cli.py <b></b></div><pre class="busy">running...</pre>';
  blk.querySelector("b").textContent=cmd; out.prepend(blk); out.scrollTop=0;
  try{
    const r=await fetch("/run",{method:"POST",headers:{"Content-Type":"application/json","X-Kalshi-Token":TOKEN},
      body:JSON.stringify({cmd:cmd,cols:cols(),confirm:word})});
    const j=await r.json(); const pre=blk.querySelector("pre"); pre.className=""; pre.innerHTML=j.html||"(no output)";
  }catch(e){blk.querySelector("pre").textContent="The dashboard server did not answer. Is it still running in the terminal?";}
}
function sel(t){$("tk").value=t;$("selname").textContent=t;}
function need(){const t=$("tk").value.trim();if(!t){$("tk").focus();$("tk").placeholder="enter a ticker first";}return t;}
function filters(){let s="";const c=$("cat").value;if(c)s+=" --category "+c;
  if($("pmin").value)s+=" --min-price "+$("pmin").value;if($("pmax").value)s+=" --max-price "+$("pmax").value;
  if(($("pmin").value||$("pmax").value)&&$("pside").value!="either")s+=" --side "+$("pside").value;return s;}
document.addEventListener("click",e=>{
  const b=e.target.closest("button"), t=e.target.closest(".tk");
  if(t){sel(t.dataset.t);return;}
  if(!b)return;
  if(b.dataset.c){let c=b.dataset.c;if(c.startsWith("best"))c+=" --bankroll "+($("bank").value||500)+($("cat").value?" --category "+$("cat").value:"");run(c);}
  else if(b.dataset.t){const k=need();if(k)run(b.dataset.t+" "+k+(b.dataset.x?" "+b.dataset.x:""));}
});
$("btop").onclick=()=>run("top"+filters()+" --sort "+$("sort").value+" --top "+$("n").value);
$("bpreset").onclick=()=>run("top --preset "+$("preset").value+($("cat").value?" --category "+$("cat").value:"")+" --top "+$("n").value);
$("bq").onclick=()=>{if($("q").value.trim())run("top --search "+$("q").value.trim()+filters()+" --top "+$("n").value);};
$("q").onkeydown=e=>{if(e.key=="Enter")$("bq").click();};
$("bpicks").onclick=()=>run("picks --top "+$("n").value+" --bankroll "+($("bank").value||500)+($("cat").value?" --category "+$("cat").value:""));
$("bgrind").onclick=()=>run("grind --start "+($("gs").value||10)+" --target "+($("gt").value||40)+($("cat").value?" --category "+$("cat").value:""));
$("bmult").onclick=()=>run("multiply "+$("mx").value+" --stake "+($("ms").value||10)+" --days "+Math.min(7,parseFloat($("md").value)||3)+" --route "+$("mr").value+($("cat").value?" --category "+$("cat").value:""));
$("bplan").onclick=()=>{const k=need();if(!k)return;if(!$("prob").value){$("prob").focus();return;}run("plan "+k+" --my-prob "+$("prob").value+" --bankroll "+($("bank").value||500));};
function order(kind){const k=need();if(!k)return;let c=kind+" "+k+" --side "+$("side").value;
  if($("price").value)c+=" --price "+$("price").value;if($("count").value)c+=" --count "+$("count").value;
  if(kind=="buy"&&$("prob").value)c+=" --my-prob "+$("prob").value+" --bankroll "+($("bank").value||500);run(c);}
$("bbuy").onclick=()=>order("buy"); $("bsell").onclick=()=>order("sell");
$("run").onclick=()=>{run($("cmd").value);$("cmd").value="";};
$("cmd").onkeydown=e=>{if(e.key=="Enter")$("run").click();
  else if(e.key=="ArrowUp"&&hi>0){hi--;$("cmd").value=hist[hi];e.preventDefault();}
  else if(e.key=="ArrowDown"){hi=Math.min(hist.length,hi+1);$("cmd").value=hist[hi]||"";}};
</script></body></html>"""


def cmd_serve(a):
    """Run the dashboard on this computer only."""
    import http.server
    token = secrets.token_urlsafe(24)
    page = (DASHBOARD_PAGE.replace("%TOKEN%", token)
            .replace("%ALLOW_LIVE%", "true" if a.allow_live else "false")
            .replace("%LIVE_NOTE%", "; add --live to send one for real" if a.allow_live else " (start with --allow-live to send real ones)")
            .replace("%CATS%", json.dumps(sorted(CATEGORIES)))
            .replace("%SORTS%", json.dumps(["hot"] + sorted(k for k in SORTS if k != "hot")))
            .replace("%PRESETS%", json.dumps(sorted(PRESETS))))
    allowed_hosts = {f"127.0.0.1:{a.port}", f"localhost:{a.port}"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def reply(self, code, body, kind):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("Host") not in allowed_hosts:
                return self.reply(403, "Open the dashboard at the address printed in the terminal.", "text/plain")
            if self.path.split("?")[0] == "/":
                return self.reply(200, page, "text/html")
            self.reply(404, "Not found", "text/plain")

        def do_POST(self):
            # Three checks before anything runs: the right address, the secret token, and a sane body.
            if self.headers.get("Host") not in allowed_hosts or self.path != "/run":
                return self.reply(403, json.dumps({"html": "Refused."}), "application/json")
            if not secrets.compare_digest(self.headers.get("X-Kalshi-Token", ""), token):
                return self.reply(403, json.dumps({"html": "Refused: reload the dashboard page."}), "application/json")
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 20000)))
                columns = max(100, min(190, int(body.get("cols", 150))))
                text, code = run_for_dashboard(str(body.get("cmd", "")), columns, a.allow_live, str(body.get("confirm", "")))
            except Exception as err:
                text, code = html.escape(f"The dashboard could not run that: {err}"), 1
            self.reply(200, json.dumps({"html": text, "code": code}), "application/json")

        def log_message(self, *args):          # keep the terminal quiet
            pass

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    except OSError as err:
        sys.exit(f"Could not start on port {a.port} ({err}). Try: serve --port {a.port + 1}")
    url = f"http://127.0.0.1:{a.port}"
    print(banner("KALSHI", paint("dashboard running", "bold")))
    print(f"\n  Open {paint(url, 'blue', 'ul')} in your browser.   {paint('Ctrl+C here to stop it.', 'gray')}")
    print("  " + (paint("Live orders are ON for this session.", "red", "bold") if a.allow_live
                  else paint("Orders are dry runs only. Restart with --allow-live to send real ones.", "gray")))
    print(paint("  Only this computer can reach it.", "gray"))
    if not a.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(paint("\nStopped.", "gray"))


# ----------------------------------------------------------------------------
# Firehose: run everything and bind the output into one color PDF
# ----------------------------------------------------------------------------
# `firehose` runs every command this tool has, one after another, and writes
# what each printed into a single PDF that looks like the terminal: same
# layout, same colors, same bars. Nothing needs installing: the PDF is written
# by hand below, using the Courier font that every PDF reader already has.
#
# It never sends an order. Trading commands appear as dry runs only.

PDF_THEMES = {
    # name: (page background, default text, {ansi color code: (r, g, b)})
    "dark": ((0.047, 0.047, 0.047), (0.83, 0.83, 0.83),
             {"90": (0.54, 0.54, 0.54), "91": (1.0, 0.37, 0.42), "92": (0.24, 0.86, 0.35), "93": (0.95, 0.84, 0.27),
              "94": (0.44, 0.66, 1.0), "95": (0.95, 0.35, 0.84), "96": (0.16, 0.85, 0.90), "97": (1.0, 1.0, 1.0)}),
    "light": ((1.0, 1.0, 1.0), (0.10, 0.10, 0.10),
              {"90": (0.47, 0.47, 0.47), "91": (0.78, 0.16, 0.16), "92": (0.11, 0.54, 0.23), "93": (0.60, 0.45, 0.0),
               "94": (0.12, 0.37, 0.75), "95": (0.65, 0.15, 0.64), "96": (0.04, 0.50, 0.55), "97": (0.0, 0.0, 0.0)}),
}

# Block characters the terminal output uses, and how tall each one is (in eighths of a line).
BLOCK_HEIGHTS = {"▁": 1, "▂": 2, "▃": 3, "▄": 4, "▅": 5, "▆": 6, "▇": 7, "█": 8}


def ansi_cells(text, columns):
    """
    Turn colored terminal text into rows of cells: (character, color code, bold, underline, inverse).
    Lines longer than the page are wrapped.
    """
    rows = []
    for raw in text.split("\n"):
        row, color, bold, under, inverse, pos = [], None, False, False, False, 0
        for match in ANSI_RE.finditer(raw):
            row.extend((ch, color, bold, under, inverse) for ch in raw[pos:match.start()])
            pos = match.end()
            codes = [c for c in match.group(1).split(";") if c]
            if not codes or codes == ["0"]:
                color, bold, under, inverse = None, False, False, False
            for code in codes:
                if code == "1":
                    bold = True
                elif code == "4":
                    under = True
                elif code == "7":
                    inverse = True
                elif code in ("90", "91", "92", "93", "94", "95", "96", "97"):
                    color = code
        row.extend((ch, color, bold, under, inverse) for ch in raw[pos:])
        while len(row) > columns:                      # wrap anything wider than the page, at a space when there is one
            cut = columns
            for i in range(columns, columns // 2, -1):
                if row[i][0] == " ":
                    cut = i
                    break
            rows.append(row[:cut])
            row = row[cut:]
            while row and row[0][0] == " ":            # do not start the next line with the space we broke at
                row = row[1:]
        rows.append(row)
    return rows


def pdf_text(chars):
    """Escape a run of characters for a PDF string. Anything Courier cannot draw becomes '?'."""
    data = "".join(chars).encode("cp1252", "replace").decode("latin-1")
    return data.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path, sections, theme="dark", columns=130, title="Kalshi CLI firehose"):
    """
    sections: list of (heading, rows) where rows come from ansi_cells().
    Layout: landscape letter pages, one section starting per page, a cover page
    with a clickable list of sections, and bookmarks in the PDF sidebar.
    """
    background, ink, palette = PDF_THEMES[theme]
    width, height, margin = 792.0, 612.0, 30.0
    size = min(9.0, (width - 2 * margin) / (columns * 0.6))    # Courier is 0.6 of its size wide
    cell_w, line_h = size * 0.6, size * 1.28
    top = height - margin - 16                                  # leave room for the page header
    per_page = int((top - margin) // line_h)
    rgb = lambda c: "%.3f %.3f %.3f" % c
    color_of = lambda code: palette.get(code, ink)

    def draw_rows(rows, header):
        """Return the PDF drawing commands for one page of rows."""
        ops = [f"{rgb(background)} rg 0 0 {width} {height} re f"]
        ops.append(f"BT /F1 7.5 Tf {rgb(palette['90'])} rg 1 0 0 1 {margin} {height - margin - 4:.1f} Tm ({pdf_text(header)}) Tj ET")
        for n, row in enumerate(rows):
            y = top - (n + 1) * line_h + line_h * 0.28          # text baseline for this line
            base = top - (n + 1) * line_h                       # bottom edge of this line's cell
            col = 0
            while col < len(row):
                ch, color, bold, under, inverse = row[col]
                x = margin + col * cell_w
                fill = color_of(color)
                if ch in BLOCK_HEIGHTS or ch == "░":
                    # Bars: draw the block characters as real rectangles so they look solid.
                    end = col
                    while end < len(row) and row[end][0] == ch and row[end][1] == color:
                        end += 1
                    if ch == "░":
                        shade = tuple(background[i] + (ink[i] - background[i]) * 0.16 for i in range(3))
                        ops.append(f"{rgb(shade)} rg {x:.2f} {base + line_h * 0.12:.2f} {(end - col) * cell_w:.2f} {line_h * 0.76:.2f} re f")
                    else:
                        tall = line_h * BLOCK_HEIGHTS[ch] / 8.0
                        ops.append(f"{rgb(fill)} rg {x:.2f} {base:.2f} {(end - col) * cell_w + 0.05:.2f} {tall:.2f} re f")
                    col = end
                    continue
                if ch == "─":                              # horizontal rule
                    end = col
                    while end < len(row) and row[end][0] == ch:
                        end += 1
                    ops.append(f"{rgb(fill)} RG 0.5 w {x:.2f} {base + line_h * 0.5:.2f} m {x + (end - col) * cell_w:.2f} {base + line_h * 0.5:.2f} l S")
                    col = end
                    continue
                # A run of ordinary text in one style.
                end = col
                while end < len(row) and row[end][1:] == row[col][1:] and row[end][0] not in BLOCK_HEIGHTS \
                        and row[end][0] not in ("░", "─"):
                    end += 1
                chars = ["^" if c[0] == "▲" else "v" if c[0] == "▼" else c[0] for c in row[col:end]]
                if "".join(chars).strip() or inverse or under:
                    run_w = (end - col) * cell_w
                    if inverse:                                 # banner strips: colored box, dark text
                        ops.append(f"{rgb(fill)} rg {x:.2f} {base:.2f} {run_w:.2f} {line_h:.2f} re f")
                        text_color = background if theme == "dark" else (1.0, 1.0, 1.0)
                    else:
                        text_color = fill
                    font = "/F2" if bold else "/F1"
                    ops.append(f"BT {font} {size:.2f} Tf {rgb(text_color)} rg 1 0 0 1 {x:.2f} {y:.2f} Tm ({pdf_text(chars)}) Tj ET")
                    if under:
                        ops.append(f"{rgb(text_color)} RG 0.4 w {x:.2f} {y - 1.2:.2f} m {x + run_w:.2f} {y - 1.2:.2f} l S")
                col = end
        return "\n".join(ops)

    # ---- lay the sections out into pages ----
    pages, starts = [], []                                      # pages: (rows, header); starts: first page index of each section
    for heading, rows in sections:
        starts.append(len(pages))
        chunks = [rows[i:i + per_page] for i in range(0, max(1, len(rows)), per_page)]
        for k, chunk in enumerate(chunks):
            more = f"  (continued, {k + 1} of {len(chunks)})" if len(chunks) > 1 and k else ""
            pages.append((chunk, f"{title}   |   {heading}{more}"))

    # ---- the cover: title, when it ran, and a clickable list of sections ----
    stamp = dt.datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    plain = lambda text, code=None, bold=False: [(ch, code, bold, False, False) for ch in text]
    cover = [[], [(" ", "96", True, False, True)] + [(ch, "96", True, False, True) for ch in "KALSHI CLI "] + plain("  firehose", "97", True), [],
             plain(f"  Every command, run on {stamp}.", "90"),
             plain("  Prices are a snapshot from that moment. Nothing here predicts an outcome, and no order was sent.", "90"), [],
             plain("  CONTENTS", "96", True) + plain("   click a line to jump to it", "90"), []]
    first_entry = len(cover)
    cover_lines_per_page = per_page
    cover_pages = max(1, -(-(first_entry + len(sections)) // cover_lines_per_page))
    for i, (heading, _) in enumerate(sections):
        page_no = cover_pages + starts[i] + 1
        label = f"  {i + 1:>3}.  {heading}"
        cover.append(plain(label[:columns - 12], None) + plain(" " + "." * max(2, columns - 12 - len(label[:columns - 12])) + f" {page_no:>3}", "90"))
    cover_chunks = [cover[i:i + cover_lines_per_page] for i in range(0, len(cover), cover_lines_per_page)]
    all_pages = [(chunk, f"{title}   |   contents") for chunk in cover_chunks] + pages
    total = len(all_pages)

    # ---- write the PDF objects ----
    objects = {}                                                # object number -> bytes
    font1, font2, pages_obj, catalog, outlines = 1, 2, 3, 4, 5
    next_id = [6]

    def new_id():
        next_id[0] += 1
        return next_id[0] - 1

    objects[font1] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"
    objects[font2] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>"
    page_ids = [new_id() for _ in range(total)]
    for n, (rows, header) in enumerate(all_pages):
        stream = zlib.compress(draw_rows(rows, f"{header}   |   page {n + 1} of {total}").encode("latin-1", "replace"))
        content = new_id()
        objects[content] = b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream"
        annots = ""
        if n < len(cover_chunks):
            # Each contents line on the cover is a clickable link to its section's first page.
            links = []
            for row_index in range(len(cover_chunks[n])):
                entry = n * cover_lines_per_page + row_index - first_entry
                if 0 <= entry < len(sections):
                    y0 = top - (row_index + 1) * line_h
                    target = page_ids[len(cover_chunks) + starts[entry]]
                    links.append(f"<< /Type /Annot /Subtype /Link /Border [0 0 0] /Rect [{margin:.1f} {y0:.1f} {width - margin:.1f} {y0 + line_h:.1f}] "
                                 f"/Dest [{target} 0 R /Fit] >>")
            annots = " /Annots [" + " ".join(links) + "]"
        objects[page_ids[n]] = (f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {width:.0f} {height:.0f}] "
                                f"/Resources << /Font << /F1 {font1} 0 R /F2 {font2} 0 R >> >> /Contents {content} 0 R{annots} >>").encode("latin-1")
    objects[pages_obj] = (f"<< /Type /Pages /Count {total} /Kids [" + " ".join(f"{i} 0 R" for i in page_ids) + "] >>").encode("latin-1")
    # Bookmarks, one per section, shown in the PDF reader's sidebar.
    item_ids = [new_id() for _ in sections]
    for i, (heading, _) in enumerate(sections):
        target = page_ids[len(cover_chunks) + starts[i]]
        links = f"/Parent {outlines} 0 R" + (f" /Prev {item_ids[i - 1]} 0 R" if i else "") + (f" /Next {item_ids[i + 1]} 0 R" if i + 1 < len(sections) else "")
        objects[item_ids[i]] = f"<< /Title ({pdf_text(heading[:90])}) {links} /Dest [{target} 0 R /Fit] >>".encode("latin-1")
    objects[outlines] = (f"<< /Type /Outlines /First {item_ids[0]} 0 R /Last {item_ids[-1]} 0 R /Count {len(item_ids)} >>".encode("latin-1")
                         if item_ids else b"<< /Type /Outlines /Count 0 >>")
    objects[catalog] = f"<< /Type /Catalog /Pages {pages_obj} 0 R /Outlines {outlines} 0 R /PageMode /UseOutlines >>".encode("latin-1")
    info = new_id()
    objects[info] = f"<< /Title ({pdf_text(title)}) /Creator (kalshi_cli.py firehose) >>".encode("latin-1")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    xref = len(out)
    count = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % count
    for number in range(1, count):
        out += b"%010d 00000 n \n" % offsets[number]
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (count, catalog, info, xref)
    with open(path, "wb") as fh:
        fh.write(out)
    return total


def run_capture(argv, columns, timeout=600):
    """Run this same script with one command, colors forced on, and return (output text, exit code)."""
    env = dict(os.environ, COLUMNS=str(columns), LINES="50", PYTHONIOENCODING="utf-8")
    env.pop("NO_COLOR", None)
    try:
        done = subprocess.run([sys.executable, os.path.abspath(__file__), "--color", "always"] + argv, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "That command took too long and was stopped.", 1
    text = done.stdout
    errors = "\n".join(ln for ln in done.stderr.split("\n") if ln.strip() and "Downloading every open market" not in ln)
    if errors:
        text += ("\n" if text else "") + "\x1b[90m" + errors + "\x1b[0m"
    return text.rstrip("\n"), done.returncode


# The variations `multiply --sweep` runs, in order. Edit this list to change the sweep.
MULTIPLY_SWEEP = [
    "multiply 2x --stake 10",
    "multiply 5x --stake 20 --days 5",
    "multiply 10x --stake 100 --days 7",
    "multiply 3x --stake 20 --route single",
    "multiply 3x --stake 20 --route multi",
    "multiply 2x --stake 20 --days 1",
    "multiply 3x --stake 100 --days 7 --route chain --max-bets 4",
    "multiply 5x --stake 10 --route spread --max-bets 4",
    "multiply 5x --stake 10 --route single --top 10",
    "multiply 10x --stake 20 --days 7 --route single --top 10",
    "multiply 2x --stake 100 --category weather economics",
    "multiply 3x --stake 20 --category politics --days 7",
    "multiply 5x --stake 20 --days 7 --skip-category",
    "multiply 2x --stake 10 --bias 0",
    "multiply 3x --stake 10 --min-volume-24h 2000 --max-spread 2",
    "x 2x -s 10 -d 2 --no-links",
]


def multiply_sweep(a):
    """Run every multiply variation above and write each command plus what it printed to one text file."""
    path = os.path.expanduser(a.sweep)
    line = "=" * 110
    parts = [f"Kalshi CLI multiply sweep - {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} - {len(MULTIPLY_SWEEP)} commands",
             "Dry runs only. Nothing here was sent to the exchange."]
    print(banner("KALSHI", paint("multiply sweep", "bold") + paint(f"  {len(MULTIPLY_SWEEP)} variations into one text file", "gray")) + "\n")
    load_markets(a.cache_minutes, a.refresh)                  # download once here so every run below reuses the saved list
    for i, command in enumerate(MULTIPLY_SWEEP, 1):
        text, code = run_capture(shlex.split(command), 130)
        text = ANSI_RE.sub("", text)                          # a text file wants plain text, so the color codes come out
        parts.append(f"\n{line}\nCOMMAND {i} of {len(MULTIPLY_SWEEP)}: python kalshi_cli.py {command}\n{line}\n{text}")
        mark = paint("ok", "green") if code == 0 else paint(f"exit {code}", "red", "bold")
        print(f"  {i:>2} {paint('python kalshi_cli.py ' + command, 'bold')}  {mark}")
    with open(path, "w", encoding="utf-8-sig") as fh:          # the marker at the start makes Notepad read it as UTF-8
        fh.write("\n".join(parts) + "\n")
    print("\n" + paint("Wrote ", "green", "bold") + f"{path}  ({len(MULTIPLY_SWEEP)} commands, each followed by its output)")


def cmd_firehose(a):
    """Run every command and write the lot to one color PDF."""
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.expanduser(a.out or f"kalshi_firehose_{stamp}.pdf")
    columns = 130
    bank = f"{a.bankroll:g}"
    print(banner("KALSHI", paint("firehose", "bold") + paint("  every command, into one PDF. No order is sent.", "gray")) + "\n")

    def step(argv, sections, note=""):
        """Run one command, show progress, and keep its output for the PDF."""
        heading = "python kalshi_cli.py " + " ".join(shlex.quote(x) if " " in x else x for x in argv)
        print(f"  {cell(len(sections) + 1, 3, 'gray', align='>')} {heading[:100]} ", end="", flush=True)
        started = time.time()
        text, code = run_capture(argv, columns)
        print(paint(f"{time.time() - started:.0f}s", "gray") + ("" if code == 0 else paint(f"  (exit {code})", "yellow")))
        prompt = "\x1b[90mPS> \x1b[0m\x1b[1;97m" + heading + "\x1b[0m" + (f"\x1b[90m   {note}\x1b[0m" if note else "")
        sections.append((heading.replace("python kalshi_cli.py ", ""), ansi_cells(prompt + "\n\n" + text, columns)))

    sections = []
    # ---- 1. the whole exchange ----
    if a.refresh:
        load_markets(a.cache_minutes, True)
    step(["categories"], sections)
    step(["top", "--all", "--top", "15"], sections)
    for preset in sorted(PRESETS):
        step(["top", "--preset", preset, "--top", "8"], sections)
    for name in ("volume", "interest", "gainers", "losers", "newest", "cheapest", "priciest", "tight-spread", "wide-spread"):
        step(["top", "--sort", name, "--top", "6", "--min-volume-24h", "500", "--no-links"], sections)
    for category in ("economics", "politics", "weather", "tech"):
        step(["top", "--category", category, "--top", "8"], sections)
    # ---- 2. edge finders ----
    step(["anomalies", "--top", "5"], sections)
    step(["picks", "--top", "10", "--bankroll", bank, "--seed", "7"], sections)
    step(["best", "5", "--by", "odds", "--bankroll", bank], sections)
    step(["best", "5", "--by", "soonest", "--bankroll", bank], sections)
    step(["grind", "--start", "10", "--target", "40"], sections)
    step(["grind", "--start", "10", "--target", "20", "--stake", "50"], sections)
    step(["multiply", "2x", "--stake", "10"], sections)
    step(["multiply", "5x", "--stake", "20", "--days", "5"], sections)
    step(["multiply", "10x", "--stake", "100", "--days", "7", "--route", "multi"], sections)
    step(["best", "5", "--bankroll", bank], sections)

    # ---- 3. one market, all the way down. Use the one named, or the top-scoring pick just listed. ----
    ticker = a.ticker
    if not ticker:
        try:
            with open(LAST_FILE, encoding="utf-8") as fh:
                ticker = json.load(fh)[0]["ticker"]
        except (OSError, ValueError, IndexError, KeyError):
            ticker = None
    if ticker:
        ticker = ticker.upper()
        event, mid = "", None
        try:
            market = api_get("/markets/" + urllib.parse.quote(ticker))["market"]
            event = market.get("event_ticker", "")
            bid, ask = cents(market.get("yes_bid_dollars")), cents(market.get("yes_ask_dollars"))
            mid = (bid + ask) / 2 if bid is not None and ask is not None else cents(market.get("last_price_dollars"))
        except Exception:
            pass
        step(["show", ticker, "--full-rules"], sections)
        step(["analyze", ticker], sections)
        step(["analyze", ticker, "--interval", "hour", "--days", "3", "--deep"], sections)
        if event:
            step(["event", event], sections)
            step(["ladder", event], sections)
        if a.my_prob is not None:
            prob, note = a.my_prob, ""
        else:
            prob, note = round(mid or 50), "no --my-prob given, so this uses the market's own price: expect no edge"
        step(["plan", ticker, "--my-prob", f"{prob:g}", "--bankroll", bank], sections, note)
        step(["buy", "1", "--take"], sections, "dry run of the plan above; nothing is sent")
        step(["brief", ticker], sections)
        step(["fee", "--price", f"{round(mid or 50):g}", "--contracts", "100"], sections)
    # ---- 4. your own things ----
    step(["watchlist"], sections)
    if os.path.exists(os.path.expanduser(LOG_FILE_DEFAULT)):
        step(["log", "stats"], sections)
    if load_auth():
        step(["positions"], sections)
        step(["orders"], sections)
    step(["examples"], sections)

    pages = write_pdf(path, sections, theme=a.theme, columns=columns)
    print("\n" + paint("Wrote ", "green", "bold") + f"{path}  " + paint(f"({len(sections)} commands, {pages} pages, {os.path.getsize(path) / 1e6:.1f} MB)", "gray"))
    print(paint("The first page is a clickable list of contents; each command is also a bookmark.", "gray"))
    if a.open:
        try:
            webbrowser.open("file://" + os.path.abspath(path).replace("\\", "/"))
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        description="One tool for Kalshi: find markets, find an edge, decide, trade, keep score. "
                    "Trading commands are dry runs unless you add --live.",
        epilog="Start with:  kalshi_cli.py examples     Options for one command:  kalshi_cli.py top -h",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                   help="colored output: auto (default, on when printing to a terminal), always, or never")
    p.add_argument("--demo", action="store_true",
                   help="use Kalshi's practice exchange (demo.kalshi.co) with practice money; needs a demo API key")
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
    g.add_argument("--format", "-f", choices=["table", "json", "csv", "markdown"], default="table",
                   help="output format: table (default, colored), json, csv, or markdown")
    g.add_argument("--out", "-o", metavar="FILE", help="with --format json/csv/markdown: write to this file instead of the screen")
    g.add_argument("--json", action="store_true", help="same as --format json")
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

    # ---- picks ----
    k = sub.add_parser("picks", help="a scored, diversified short list of markets to research, with basket odds",
                       description="Screens every open market against the guide's rules, scores what is left, and draws a "
                                   "diversified set. The estimated probability is the market price plus a small adjustment "
                                   "for the favorite-longshot pattern. It is an assumption, not a forecast.")
    k.add_argument("--top", "-n", type=int, default=5, metavar="N", help="how many picks: 5, 10... (default 5)")
    k.add_argument("--category", "-c", nargs="+", choices=sorted(CATEGORIES), metavar="NAME", help="only these categories")
    k.add_argument("--skip-category", nargs="*", choices=sorted(CATEGORIES), metavar="NAME",
                   help="leave these out (default: sports crypto, as the guide advises; pass with no names to include everything)")
    k.add_argument("--search", "-s", nargs="+", metavar="WORD", help="only markets containing all of these words")
    k.add_argument("--min-price", type=float, default=60, metavar="CENTS", help="lowest limit price to consider (default 60)")
    k.add_argument("--max-price", type=float, default=95, metavar="CENTS", help="highest limit price to consider (default 95)")
    k.add_argument("--closes-within", type=float, default=30, metavar="DAYS", help="resolves within N days (default 30)")
    k.add_argument("--min-volume-24h", type=float, default=500, metavar="N", help="at least N contracts traded in 24h (default 500)")
    k.add_argument("--max-spread", type=float, default=4, metavar="CENTS", help="widest bid-ask gap to trust (default 4)")
    k.add_argument("--bankroll", type=float, default=500, metavar="USD", help="your account size, for sizing (default 500)")
    k.add_argument("--risk", type=float, default=2, metavar="PCT", help="percent of bankroll per pick, 1 to 3 (default 2)")
    k.add_argument("--bias", type=float, default=1.0, metavar="X",
                   help="strength of the favorite-longshot adjustment: 0 = trust the price exactly, 1 = default, 2 = double")
    k.add_argument("--by", choices=["score", "odds", "soonest"], default="score",
                   help="rank by overall score (default), by the best odds of winning, or by what resolves first")
    k.add_argument("--seed", type=int, metavar="N", help="fix the random draw so the same picks come back")
    k.add_argument("--no-random", action="store_true", help="take the top scores in order instead of a weighted random draw")
    k.add_argument("--no-links", action="store_true", help="hide the ticker/link line")
    k.add_argument("--format", "-f", choices=["table", "json", "csv", "markdown"], default="table",
                   help="output format: table (default, colored), json, csv, or markdown")
    k.add_argument("--out", "-o", metavar="FILE", help="with --format json/csv/markdown: write to this file")
    k.add_argument("--json", action="store_true", help="same as --format json")
    k.set_defaults(func=cmd_picks)

    k = sub.add_parser("best", help="the top-scoring market, or the top few: best, best 3, best 5, best 10",
                       description="The highest-scoring markets after the guide's screen, in order. `best` alone gives one. "
                                   "The score is an expected return under a stated assumption, not a prediction.")
    k.add_argument("count", nargs="?", type=int, default=1, help="how many: 1 (default), 3, 5, 10...")
    k.add_argument("--category", "-c", nargs="+", choices=sorted(CATEGORIES), metavar="NAME", help="only these categories")
    k.add_argument("--skip-category", nargs="*", choices=sorted(CATEGORIES), metavar="NAME",
                   help="leave these out (default: sports crypto, as the guide advises; pass with no names to include everything)")
    k.add_argument("--search", "-s", nargs="+", metavar="WORD", help="only markets containing all of these words")
    k.add_argument("--min-price", type=float, default=60, metavar="CENTS", help="lowest limit price to consider (default 60)")
    k.add_argument("--max-price", type=float, default=95, metavar="CENTS", help="highest limit price to consider (default 95)")
    k.add_argument("--closes-within", type=float, default=30, metavar="DAYS", help="resolves within N days (default 30)")
    k.add_argument("--min-volume-24h", type=float, default=500, metavar="N", help="at least N contracts traded in 24h (default 500)")
    k.add_argument("--max-spread", type=float, default=4, metavar="CENTS", help="widest bid-ask gap to trust (default 4)")
    k.add_argument("--bankroll", type=float, default=500, metavar="USD", help="your account size, for sizing (default 500)")
    k.add_argument("--risk", type=float, default=2, metavar="PCT", help="percent of bankroll per pick, 1 to 3 (default 2)")
    k.add_argument("--bias", type=float, default=1.0, metavar="X",
                   help="strength of the favorite-longshot adjustment: 0 = trust the price exactly, 1 = default, 2 = double")
    k.add_argument("--by", choices=["score", "odds", "soonest"], default="score",
                   help="rank by overall score (default), by the best odds of winning, or by what resolves first")
    k.add_argument("--no-links", action="store_true", help="hide the ticker/link line")
    k.add_argument("--format", "-f", choices=["table", "json", "csv", "markdown"], default="table",
                   help="output format: table (default, colored), json, csv, or markdown")
    k.add_argument("--out", "-o", metavar="FILE", help="with --format json/csv/markdown: write to this file")
    k.add_argument("--json", action="store_true", help="same as --format json")
    k.set_defaults(func=cmd_best, seed=None)

    # ---- analyze ----
    z = sub.add_parser("analyze", help="price history, statistics and a chart for one market, with drill-down",
                       description="Statistics for one market over time. Add --book, --trades N or --deep to drill down.")
    z.add_argument("market", help="ticker, or a row number from the last `top` / `picks` list")
    z.add_argument("--days", type=float, default=14, metavar="N", help="how far back to look (default 14; at most 7 with --interval hour)")
    z.add_argument("--interval", choices=["day", "hour"], default="day", help="one point per day (default) or per hour")
    z.add_argument("--book", action="store_true", help="drill down: the order book with depth on each side")
    z.add_argument("--trades", type=int, default=0, metavar="N", help="drill down: the last N trades and who was buying")
    z.add_argument("--deep", action="store_true", help="drill down into everything: order book and the last 15 trades")
    z.add_argument("--height", type=int, default=8, metavar="ROWS", help="chart height in rows (default 8)")
    z.add_argument("--format", "-f", choices=["table", "json"], default="table", help="table (default) or json")
    z.add_argument("--out", "-o", metavar="FILE", help="with --format json: write to this file")
    z.set_defaults(func=cmd_analyze)

    # ---- trading (optional; everything is a dry run unless --live is given) ----
    def trading(parser):
        parser.add_argument("--live", action="store_true", help="really send it (default is a dry run that sends nothing)")
        parser.add_argument("--yes", action="store_true", help="skip the typed confirmation (for scripts; use with care)")

    b = sub.add_parser("buy", help="preview or place a limit BUY order (dry run unless --live)")
    b.add_argument("market", help="ticker, or a row number from the last `top` / `picks` list")
    b.add_argument("--side", choices=["yes", "no"], help="which contract to buy (taken from the pick when you use a row number)")
    b.add_argument("--price", type=float, metavar="CENTS", help="your limit price in whole cents")
    b.add_argument("--count", type=int, metavar="N", help="number of contracts")
    b.add_argument("--spend", type=float, metavar="USD", help="or: spend about this many dollars")
    b.add_argument("--take", action="store_true", help="allow the order to fill at once against the ask (pays the taker fee)")
    b.add_argument("--my-prob", type=float, metavar="PCT", help="YOUR probability of YES, to check your edge")
    b.add_argument("--bankroll", type=float, metavar="USD", help="your account size, to check the 3%% sizing rule")
    b.add_argument("--max-cost", type=float, metavar="USD", help=f"per-order cap for this order (default ${DEFAULT_CAP:g} or KALSHI_MAX_ORDER_USD)")
    trading(b)
    b.set_defaults(func=cmd_buy)

    b = sub.add_parser("sell", help="preview or place a limit SELL of contracts you hold (dry run unless --live)")
    b.add_argument("market", help="ticker, or a row number from the last `positions` list")
    b.add_argument("--side", choices=["yes", "no"], required=True, help="which contracts you are selling")
    b.add_argument("--count", type=int, required=True, metavar="N")
    b.add_argument("--price", type=float, metavar="CENTS", help="lowest price you will accept, in whole cents")
    trading(b)
    b.set_defaults(func=cmd_sell)

    b = sub.add_parser("orders", help="list your orders (needs API key)")
    b.add_argument("--status", choices=["resting", "executed", "canceled"], default="resting")
    b.set_defaults(func=cmd_orders)

    b = sub.add_parser("cancel", help="cancel a resting order (dry run unless --live)")
    b.add_argument("order_id", nargs="?")
    b.add_argument("--all", action="store_true", help="cancel every resting order")
    trading(b)
    b.set_defaults(func=cmd_cancel)

    sub.add_parser("positions", help="cash balance and what you hold now (needs API key)").set_defaults(func=cmd_positions)

    # ---- edge finders ----
    kinds = ["overround", "ladder", "underround", "move", "surge", "dislocation"]
    e = sub.add_parser("anomalies", help="scan for prices that break arithmetic or break their own pattern",
                       description="overround: outcome sets whose YES bids add past $1.  ladder: thresholds priced out of order.  "
                                   "underround: YES asks that add to under $1.  move: today's move against the market's own normal "
                                   "day.  surge: volume at several times its usual pace.  dislocation: a quote far from the last trade.")
    e.add_argument("--type", "-t", nargs="+", choices=kinds, metavar="KIND", help="which checks to run (default: all): " + ", ".join(kinds))
    e.add_argument("--category", "-c", nargs="+", choices=sorted(CATEGORIES), metavar="NAME", help="only these categories")
    e.add_argument("--skip-category", nargs="*", choices=sorted(CATEGORIES), metavar="NAME",
                   help="leave these out (default: sports crypto; pass with no names to scan everything)")
    e.add_argument("--min-edge", type=float, default=0.5, metavar="CENTS", help="smallest profit per set, after fees, worth listing (default 0.5)")
    e.add_argument("--min-volume-24h", type=float, default=500, metavar="N", help="ignore markets quieter than this (default 500)")
    e.add_argument("--checks", type=int, default=20, metavar="N", help="how many of the biggest movers to test against their own history (default 20)")
    e.add_argument("--top", "-n", type=int, default=5, metavar="N", help="how many to show per section (default 5)")
    e.add_argument("--format", "-f", choices=["table", "json", "csv", "markdown"], default="table")
    e.add_argument("--out", "-o", metavar="FILE", help="with --format json/csv/markdown: write to this file")
    e.set_defaults(func=cmd_anomalies)

    e = sub.add_parser("ladder", aliases=["dist"], help="an event's prices as a probability distribution",
                       description="For a threshold ladder: the chance of landing in each range, and the implied median. "
                                   "For named outcomes: fair odds with the built-in margin removed.")
    e.add_argument("event", help="event ticker, market ticker, or a row number from the last list")
    e.add_argument("--top", "-n", type=int, default=15, metavar="N", help="how many outcomes to list (default 15)")
    e.set_defaults(func=cmd_ladder)

    e = sub.add_parser("plan", help="your probability in; edge, Kelly size, checklist and a ready order out")
    e.add_argument("market", help="ticker, or a row number from the last list")
    e.add_argument("--my-prob", type=float, required=True, metavar="PCT", help="YOUR probability that the market resolves YES")
    e.add_argument("--bankroll", type=float, default=500, metavar="USD", help="your account size (default 500)")
    e.add_argument("--side", choices=["yes", "no"], help="only consider this side")
    e.add_argument("--kelly", type=float, default=0.25, metavar="X", help="share of full Kelly to bet (default 0.25, a quarter)")
    e.add_argument("--max-risk", type=float, default=3, metavar="PCT", help="never size above this percent of bankroll (default 3)")
    e.set_defaults(func=cmd_plan)

    e = sub.add_parser("brief", help="write a research prompt for an AI assistant about one market")
    e.add_argument("market", help="ticker, or a row number from the last list")
    e.add_argument("--out", "-o", metavar="FILE", help="save the prompt to a file")
    e.add_argument("--copy", action="store_true", help="also copy it to the clipboard")
    e.set_defaults(func=cmd_brief)

    e = sub.add_parser("watchlist", help="keep a list of markets and see how each has moved since you added it")
    e.add_argument("action", nargs="?", choices=["list", "add", "remove", "clear"], default="list")
    e.add_argument("market", nargs="?", help="ticker or row number, for add and remove")
    e.add_argument("--note", help="a reminder to yourself, for add")
    e.set_defaults(func=cmd_watchlist)

    e = sub.add_parser("daily", help="the morning run-through: watchlist, anomalies, top picks, scorecard")
    e.add_argument("--picks", type=int, default=3, metavar="N", help="how many top picks to include (default 3)")
    e.add_argument("--bankroll", type=float, default=500, metavar="USD")
    e.set_defaults(func=cmd_daily)

    e = sub.add_parser("grind", help="compound a small stake through a chain of likely winners, with the real odds",
                       description="Chains heavy favorites that resolve one after another, rolling the bankroll forward, "
                                   "and simulates the chain under several staking plans.")
    e.add_argument("--start", type=float, default=10, metavar="USD", help="starting bankroll (default 10)")
    e.add_argument("--target", type=float, default=40, metavar="USD", help="goal (default 40)")
    e.add_argument("--stake", type=float, default=100, metavar="PCT", help="percent of the bankroll to put on each step (default 100)")
    e.add_argument("--min-price", type=float, default=85, metavar="CENTS", help="lowest price to consider (default 85)")
    e.add_argument("--max-price", type=float, default=97, metavar="CENTS", help="highest price to consider (default 97)")
    e.add_argument("--closes-within", type=float, default=3, metavar="DAYS", help="only markets resolving within N days (default 3)")
    e.add_argument("--min-volume-24h", type=float, default=1000, metavar="N", help="at least N contracts traded in 24h (default 1000)")
    e.add_argument("--max-spread", type=float, default=3, metavar="CENTS", help="widest bid-ask gap to accept (default 3)")
    e.add_argument("--steps", type=int, default=15, metavar="N", help="longest chain to build (default 15)")
    e.add_argument("--category", "-c", nargs="+", choices=sorted(CATEGORIES), metavar="NAME", help="only these categories")
    e.add_argument("--skip-category", nargs="*", choices=sorted(CATEGORIES), metavar="NAME",
                   help="leave these out (default: sports crypto; pass with no names to include everything)")
    e.add_argument("--bias", type=float, default=1.0, metavar="X", help="strength of the favorite-longshot adjustment (0 = trust the price)")
    e.add_argument("--no-links", action="store_true", help="hide the ticker/link line")
    e.set_defaults(func=cmd_grind)

    e = sub.add_parser("multiply", aliases=["x"], help="turn a stake into 2x / 3x / 5x / 10x inside a week: one bet or several",
                       description="Say what you want to put down and the multiple you want back. Lays out a single bet, a chain "
                                   "of bets in a row, and a spread of bets placed together, with the real odds of each and the "
                                   "orders to place. Everything resolves inside --days, which is never more than 7.")
    e.add_argument("multiple", nargs="?", type=parse_multiple, default=2.0, metavar="MULTIPLE",
                   help="the return you are after: 2x (default), 3x, 5x, 10x, or any number from 1.1 to 50")
    e.add_argument("--stake", "-s", type=float, default=10, metavar="USD", help="dollars to put down: 10 (default), 20, 100...")
    e.add_argument("--days", "-d", type=float, default=MULTIPLY_DEFAULT_DAYS, metavar="N",
                   help="everything must resolve within N days (default 3, never more than 7)")
    e.add_argument("--route", "-r", choices=["all", "single", "multi", "chain", "spread"], default="all",
                   help="all (default), single = one bet, multi = chain and spread, chain = bets in a row, spread = bets at once")
    e.add_argument("--max-bets", type=int, default=3, choices=[2, 3, 4], metavar="N",
                   help="most bets a chain or spread may use: 2, 3 (default) or 4")
    e.add_argument("--top", "-n", type=int, default=5, metavar="N", help="how many single bets to list (default 5)")
    e.add_argument("--min-volume-24h", type=float, default=300, metavar="N", help="at least N contracts traded in 24h (default 300)")
    e.add_argument("--max-spread", type=float, default=4, metavar="CENTS", help="widest bid-ask gap to accept (default 4)")
    e.add_argument("--category", "-c", nargs="+", choices=sorted(CATEGORIES), metavar="NAME", help="only these categories")
    e.add_argument("--skip-category", nargs="*", choices=sorted(CATEGORIES), metavar="NAME",
                   help="leave these out (default: sports crypto; pass with no names to include everything)")
    e.add_argument("--bias", type=float, default=1.0, metavar="X", help="strength of the favorite-longshot adjustment (0 = trust the price)")
    e.add_argument("--no-links", action="store_true", help="hide the ticker/link line")
    e.add_argument("--format", "-f", choices=["table", "json", "csv", "markdown"], default="table")
    e.add_argument("--out", "-o", metavar="FILE", help="with --format json/csv/markdown: write to this file")
    e.add_argument("--sweep", nargs="?", const="multiply_results.txt", metavar="FILE",
                   help="run 16 multiply variations and write each command with its output to a text file "
                        "(default multiply_results.txt). The other options are ignored")
    e.set_defaults(func=cmd_multiply)

    e = sub.add_parser("fee", help="fee calculator for one order")
    e.add_argument("--price", required=True, type=float, metavar="CENTS")
    e.add_argument("--contracts", required=True, type=float, metavar="N")
    e.set_defaults(func=cmd_fee)

    e = sub.add_parser("examples", help="example commands with what each one does")
    e.add_argument("--search", "-s", nargs="+", metavar="WORD", help="only examples containing these words")
    e.add_argument("--markdown", action="store_true", help="print as a Markdown document")
    e.set_defaults(func=cmd_examples)

    e = sub.add_parser("serve", help="run the dashboard in your browser, on this computer only")
    e.add_argument("--port", type=int, default=8765)
    e.add_argument("--allow-live", action="store_true", help="let the dashboard send real orders (each still needs a typed confirmation)")
    e.add_argument("--no-browser", action="store_true", help="do not open a browser tab automatically")
    e.set_defaults(func=cmd_serve)

    e = sub.add_parser("firehose", help="run EVERY command and bind the output into one color PDF",
                       description="Runs every command this tool has, with representative options, and writes what each printed "
                                   "into one PDF that looks like the terminal: same layout, colors and bars. The first page is a "
                                   "clickable list of contents. Trading commands appear as dry runs; nothing is sent.")
    e.add_argument("--out", "-o", metavar="FILE", help="where to write the PDF (default: kalshi_firehose_DATE_TIME.pdf here)")
    e.add_argument("--ticker", metavar="TICKER", help="the market to take all the way down (default: the top-scoring pick)")
    e.add_argument("--my-prob", type=float, metavar="PCT", help="your probability of YES for that market, used by the plan")
    e.add_argument("--bankroll", type=float, default=500, metavar="USD", help="account size for sizing (default 500)")
    e.add_argument("--theme", choices=["dark", "light"], default="dark", help="dark like the terminal (default), or light for printing")
    e.add_argument("--open", action="store_true", help="open the PDF when it is done")
    e.set_defaults(func=cmd_firehose)

    # ---- trade log ----
    lg = sub.add_parser("log", help="the trade log and scorecard: init, add, settle, sold, stats, fills")
    lg.add_argument("--file", metavar="PATH", help="path to the trade log CSV (default: $KALSHI_LOG or ./trade_log.csv)")
    ls = lg.add_subparsers(dest="log_cmd", required=True)
    ls.add_parser("init", help="create an empty log").set_defaults(func=log_init)
    x = ls.add_parser("add", help="record a trade you made")
    x.add_argument("--ticker", required=True, help="Kalshi market ticker")
    x.add_argument("--side", required=True, choices=["yes", "no"], help="which contract you bought")
    x.add_argument("--price", required=True, type=float, help="price you paid per contract for your side, in cents (1-99)")
    x.add_argument("--contracts", required=True, type=int, help="number of contracts")
    x.add_argument("--my-prob", required=True, type=float,
                   help="YOUR probability the market resolves YES, in percent. Write it down before you look at the price.")
    x.add_argument("--order-type", choices=["limit", "market"], default="limit")
    x.add_argument("--bankroll", type=float, help="your account size right now, in dollars (for the sizing rule)")
    x.add_argument("--fee", type=float, help="actual entry fee in dollars, if different from the estimate")
    x.add_argument("--niche", default="", help="your category label, e.g. econ, weather, tech")
    x.add_argument("--thesis", default="", help="one line on why your number differs from the market")
    x.add_argument("--rules-read", action="store_true", help="confirm you read the market's resolution rules")
    x.add_argument("--paper", action="store_true", help="practice trade, no money (kept out of the scorecard by default)")
    x.add_argument("--title", default="", help="market title, if you do not want it pulled from the API")
    x.add_argument("--notes", default="")
    x.add_argument("--offline", action="store_true", help="do not call the API")
    x.set_defaults(func=log_add)
    x = ls.add_parser("settle", help="close trades whose markets have resolved")
    x.add_argument("--id", type=int, help="settle one trade by hand")
    x.add_argument("--result", choices=["win", "loss"], help="used with --id")
    x.set_defaults(func=log_settle)
    x = ls.add_parser("sold", help="record selling a position before settlement")
    x.add_argument("--id", required=True, type=int)
    x.add_argument("--exit-price", required=True, type=float, help="price you sold at, in cents")
    x.add_argument("--exit-fee", type=float, help="actual exit fee in dollars (default: taker estimate)")
    x.set_defaults(func=log_sold)
    x = ls.add_parser("stats", help="the scorecard")
    x.add_argument("--deposits", type=float, help="total dollars you have deposited (needed for the stop rules)")
    x.add_argument("--include-paper", action="store_true", help="count paper trades too")
    x.add_argument("--json", action="store_true", help="print the scorecard as JSON")
    x.set_defaults(func=log_stats)
    x = ls.add_parser("fills", help="download raw fills to CSV for your records (needs API key)")
    x.add_argument("--out", default="kalshi_fills_raw.csv")
    x.set_defaults(func=log_fills)
    return p


def finish_args(args):
    """Small fix-ups shared by the command line, `daily` and the dashboard."""
    if getattr(args, "log_cmd", None):
        args.log = os.path.expanduser(args.file or LOG_FILE_DEFAULT)
        if args.log_cmd == "add":
            if not 1 <= args.price <= 99:
                sys.exit("--price is in cents and must be between 1 and 99.")
            if not 0 <= args.my_prob <= 100:
                sys.exit("--my-prob is a percent between 0 and 100.")


def main(argv=None):
    # Market titles can contain characters an old Windows console cannot print.
    # Replace those instead of crashing.
    global CACHE_FILE
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    setup_color(args.color)
    if args.demo:
        global BASE
        BASE = DEMO_BASE
        CACHE_FILE = CACHE_FILE.replace(".json", "_demo.json")     # never mix demo and real market lists
    try:
        finish_args(args)
        args.func(args)
    except urllib.error.HTTPError as err:
        hint = {401: "Kalshi rejected the API key. Check KALSHI_API_KEY_ID and the .pem file.",
                404: "Nothing found with that ticker. Tickers are case-insensitive but must be exact."}.get(err.code, "")
        sys.exit(paint(f"Kalshi API error {err.code}.", "red", "bold") + f" {hint}")
    except urllib.error.URLError as err:
        sys.exit(f"Could not reach Kalshi: {err.reason}")
    except BrokenPipeError:
        pass                                       # output was piped to something that closed early


if __name__ == "__main__":
    main()
