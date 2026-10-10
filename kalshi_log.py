#!/usr/bin/env python3
"""
kalshi_log.py - trade log and scorekeeper for the Kalshi Strategy and Roadmap guide.

WHAT THIS SCRIPT DOES
  * Keeps one CSV trade log at a path you choose (--log PATH).
  * Pulls market details and results from Kalshi's public API (no login needed).
  * Scores the log against the rules in the guide: the 100-trade test, the
    1%-3% sizing rule, the 5-point edge rule, the monthly and hard stops,
    and progress toward the $1,000 / $5,000 / $10,000 / $25,000 milestones.
  * Builds a small local dashboard (one HTML file, optionally served on localhost).
  * Optionally downloads your raw fills and balance (needs a Kalshi API key).

WHAT THIS SCRIPT DOES NOT DO
  * It never places, changes or cancels an order. Every API call it makes is a GET.
    You place trades in the Kalshi app, then log them here.

REQUIREMENTS
  * Python 3.9+. Standard library only for everything except `fills` and `balance`,
    which need:  pip install cryptography

QUICK START
  python kalshi_log.py --log ~/kalshi/trade_log.csv init
  python kalshi_log.py --log ~/kalshi/trade_log.csv add --ticker TICKER --side yes \
         --price 62 --contracts 20 --my-prob 70 --bankroll 500 --niche econ --thesis "..."
  python kalshi_log.py --log ~/kalshi/trade_log.csv settle
  python kalshi_log.py --log ~/kalshi/trade_log.csv stats --deposits 500
  python kalshi_log.py --log ~/kalshi/trade_log.csv dashboard --deposits 500 --serve 8765

  Tip: set KALSHI_LOG once and you can drop --log from every command.
"""
import argparse
import base64
import csv
import datetime as dt
import html
import json
import math
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

# Kalshi REST API roots. PROD is real money, DEMO is Kalshi's practice environment.
PROD_BASE = "https://external-api.kalshi.com/trade-api/v2"
DEMO_BASE = "https://external-api.demo.kalshi.co/trade-api/v2"

# Column order of the trade log. If you add a column, add it at the end so
# older logs still load (missing columns are filled with blanks on read).
COLUMNS = [
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


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

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


# ----------------------------------------------------------------------------
# Reading and writing the log
# ----------------------------------------------------------------------------

def load_log(path):
    """Return the log as a list of dicts. Exits with a clear message if it is missing."""
    if not os.path.exists(path):
        sys.exit(f"No log found at {path}. Create one with:  kalshi_log.py --log \"{path}\" init")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    # Make sure every row has every column, even if the file is from an older version.
    for row in rows:
        for col in COLUMNS:
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
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------
# Kalshi API (read-only)
# ----------------------------------------------------------------------------

def api_get(base, endpoint, params=None, auth=None, timeout=20):
    """
    GET base + endpoint and return parsed JSON.
    `auth` is None for public endpoints, or a (key_id, private_key) pair for
    account endpoints, in which case the three signature headers are added.
    """
    url = base + endpoint
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    headers = {"Accept": "application/json", "User-Agent": "kalshi-log/1.0"}
    if auth:
        headers.update(sign_headers(auth[0], auth[1], "GET", base + endpoint))
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sign_headers(key_id, private_key, method, url_without_query):
    """
    Build Kalshi's auth headers. Kalshi signs the string
        <timestamp in milliseconds><HTTP METHOD><path starting at /trade-api/v2, no query string>
    with your private key: RSA keys use RSA-PSS + SHA-256 (salt = digest length),
    Ed25519 keys sign the bytes directly. The signature is sent base64-encoded.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    ts = str(int(dt.datetime.now().timestamp() * 1000))
    path = urllib.parse.urlparse(url_without_query).path
    message = f"{ts}{method.upper()}{path}".encode("utf-8")
    if isinstance(private_key, Ed25519PrivateKey):
        raw = private_key.sign(message)
    else:
        raw = private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(raw).decode("utf-8"),
    }


def load_auth(args):
    """
    Find the API key id and private key file. Command-line flags win, then the
    environment variables KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH.
    The key file never leaves your machine; only signatures are sent.
    """
    key_id = args.key_id or os.environ.get("KALSHI_API_KEY_ID")
    key_path = args.key_file or os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if not key_id or not key_path:
        sys.exit("This command needs an API key. Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH "
                 "(or pass --key-id and --key-file). See README.md, 'API key setup'.")
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        sys.exit("This command needs the cryptography package:  pip install cryptography")
    with open(os.path.expanduser(key_path), "rb") as fh:
        private_key = serialization.load_pem_private_key(fh.read(), password=None)
    return key_id, private_key


def fetch_market(base, ticker):
    """Return the market dict for a ticker, or None if the API cannot be reached."""
    try:
        return api_get(base, "/markets/" + urllib.parse.quote(ticker))["market"]
    except urllib.error.HTTPError as err:
        print(f"  (API returned {err.code} for {ticker})")
    except Exception as err:  # network down, DNS, timeout, bad JSON
        print(f"  (could not reach the API: {err})")
    return None


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

def cmd_init(args):
    """Create an empty log with the header row. Refuses to overwrite an existing log."""
    if os.path.exists(args.log):
        sys.exit(f"{args.log} already exists. Not overwriting it.")
    save_log(args.log, [])
    print(f"Created {args.log}")


def cmd_lookup(args):
    """Show a market's title, prices, close time and resolution rules."""
    m = fetch_market(args.base, args.ticker)
    if not m:
        sys.exit(1)
    print(f"{m['ticker']}  [{m.get('status')}]")
    print(f"  {m.get('title') or m.get('yes_sub_title')}")
    print(f"  YES bid/ask: {dollars_to_cents(m.get('yes_bid_dollars'))} / {dollars_to_cents(m.get('yes_ask_dollars'))} cents"
          f"   last: {dollars_to_cents(m.get('last_price_dollars'))}")
    print(f"  closes: {m.get('close_time')}   result: {m.get('result') or '(not settled)'}")
    if m.get("rules_primary"):
        print("  RULES: " + m["rules_primary"])
    if args.rules and m.get("rules_secondary"):
        print("  MORE:  " + m["rules_secondary"])


def cmd_add(args):
    """Append one trade to the log and check it against the guide's rules."""
    rows = load_log(args.log)

    # --- pull what we can from the API so you do not have to type it ---
    market = None if args.offline else fetch_market(args.base, args.ticker)
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

    row = dict.fromkeys(COLUMNS, "")
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

    print(f"Logged #{row['id']} ({row['mode']}): {args.contracts} {args.side.upper()} @ {args.price:g}c  {title}")
    print(f"  cost ${cost:.2f}  fee ${fee:.2f}  your win probability {win_prob:g}%  edge {edge:+g} pts"
          + (f"  {pct}% of bankroll" if pct != "" else ""))

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
        print("  WARNING: " + w)


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


def cmd_settle(args):
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
        print(f"#{row['id']} {args.result.upper()}  P&L ${pnl:+.2f}")
        changed = 1
    else:
        for row in rows:
            if row["status"] != "open":
                continue
            m = fetch_market(args.base, row["ticker"])
            if not m or m.get("result") not in ("yes", "no"):
                continue                      # not settled yet (or scalar market): leave open
            won = m["result"] == row["side"]
            payout = fnum(row["contracts"]) * 1.0 if won else 0.0
            pnl = close_row(row, "win" if won else "loss", payout, closed_at=m.get("settlement_ts") or None)
            print(f"#{row['id']} {row['ticker']} resolved {m['result'].upper()} -> "
                  f"{'WIN' if won else 'LOSS'}  P&L ${pnl:+.2f}")
            changed += 1
    if changed:
        save_log(args.log, rows)
    still_open = sum(1 for r in rows if r["status"] == "open")
    print(f"{changed} trade(s) closed. {still_open} still open.")


def cmd_sell(args):
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
    print(f"#{row['id']} SOLD @ {args.exit_price:g}c  exit fee ${exit_fee:.2f}  P&L ${pnl:+.2f}")


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


def cmd_stats(args):
    """Print the scorecard."""
    s = compute_stats(load_log(args.log), args.deposits, args.include_paper)
    money = lambda v: f"${v:+,.2f}"
    print(f"Trades: {s['n_closed']} closed, {s['n_open']} open (${s['open_cost']:,.2f} at risk)")
    print(f"Profit after fees: {money(s['pnl'])}   this month: {money(s['month_pnl'])}   fees paid: ${s['fees']:,.2f}")
    if s["roi"] is not None:
        print(f"Return on money staked: {s['roi']:+.1f}%   (staked ${s['staked']:,.2f})")
    if s["win_rate"] is not None:
        print(f"Win rate: {s['win_rate']:.1f}% ({s['wins']}/{s['decided']})   average price paid: {s['avg_price']:.1f}c"
              f"   <- win rate must beat average price to profit")
    if s["brier_me"] is not None:
        verdict = "you are beating the market's prices" if s["brier_me"] < s["brier_mkt"] else "the market's prices are beating you"
        print(f"Calibration (Brier, lower is better): you {s['brier_me']:.4f} vs market {s['brier_mkt']:.4f}"
              f" over {s['n_scored']} trades -> {verdict}")
    print(f"Rule slips: {s['market_orders']} market orders, {s['oversized']} over {MAX_PCT_PER_TRADE:g}% size, "
          f"{s['thin_edge']} under {MIN_EDGE_PTS:g}-pt edge (of {s['n_total']} trades)")
    print(f"100-trade test: {s['gate']}")
    for m in MILESTONES:
        pct = max(0.0, min(100.0, 100 * s["pnl"] / m))
        print(f"  ${m:>6,} milestone: {pct:5.1f}%  {'REACHED' if s['pnl'] >= m else ''}")
    if s["by_niche"]:
        print("By niche: " + ", ".join(f"{k} {v['n']} trades {money(v['pnl'])}" for k, v in sorted(s["by_niche"].items())))
    if not args.deposits:
        print("(Pass --deposits AMOUNT to check the monthly stop and hard stop.)")
    for stop in s["stops"]:
        print("*** " + stop)


def cmd_fee(args):
    """Fee calculator for one order."""
    fee = taker_fee_usd(args.contracts, args.price)
    cost = args.contracts * args.price / 100.0
    print(f"{args.contracts} contracts at {args.price}c: cost ${cost:.2f}")
    print(f"  market order fee: ${fee:.2f} ({100 * fee / cost:.2f}% of cost) -> you need the true odds to be "
          f"{args.price + 100 * fee / args.contracts:.2f}%+ to break even")
    print("  limit order fee:  $0.00 on most markets (about a quarter of the market-order fee on some)")


def cmd_size(args):
    """How many contracts fit the 1%-3% rule, and what the edge is."""
    win_prob = args.my_prob if args.side == "yes" else 100.0 - args.my_prob
    edge = win_prob - args.price
    print(f"Your win probability {win_prob:g}% vs price {args.price}c -> edge {edge:+g} pts"
          + ("" if edge >= MIN_EDGE_PTS else f"  (below the {MIN_EDGE_PTS:g}-pt minimum: skip)"))
    for pct in (1.0, 2.0, MAX_PCT_PER_TRADE):
        dollars = args.bankroll * pct / 100.0
        n = int(dollars // (args.price / 100.0))
        print(f"  {pct:g}% of ${args.bankroll:,.0f} = ${dollars:,.2f} -> {n} contracts")


def cmd_fills(args):
    """
    Download your raw fills from Kalshi into a separate CSV (needs an API key).
    This is your tax record and a way to reconcile the log against what actually
    filled. It writes exactly what the API returns and does not touch the trade log.
    """
    auth = load_auth(args)
    fills, cursor = [], ""
    while True:                                # the API returns pages; follow the cursor to the end
        page = api_get(args.base, "/portfolio/fills", {"limit": 1000, "cursor": cursor}, auth=auth)
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
    print(f"Wrote {len(fills)} fills to {args.out}")


def cmd_balance(args):
    """Show your Kalshi cash balance (needs an API key). The API reports cents."""
    data = api_get(args.base, "/portfolio/balance", auth=load_auth(args))
    print(f"Cash balance: ${fnum(data.get('balance')) / 100:,.2f}")


def cmd_dashboard(args):
    """Write the dashboard as one self-contained HTML file; optionally serve it on localhost."""
    rows = load_log(args.log)
    s = compute_stats(rows, args.deposits, args.include_paper)
    page = render_dashboard(rows, s, args.log)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print(f"Wrote {args.out}")
    if args.serve:
        # Serve only on 127.0.0.1 so nothing outside this computer can reach it.
        # The page is rebuilt from the log on every refresh.
        import http.server

        log_path, deposits, paper = args.log, args.deposits, args.include_paper

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                fresh = load_log(log_path)
                body = render_dashboard(fresh, compute_stats(fresh, deposits, paper), log_path).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):   # keep the terminal quiet
                pass

        print(f"Dashboard at http://127.0.0.1:{args.serve}  (Ctrl+C to stop)")
        try:
            http.server.HTTPServer(("127.0.0.1", args.serve), Handler).serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


def render_dashboard(rows, s, log_path):
    """Build the dashboard HTML. No external files, scripts or network calls."""
    esc = html.escape
    money = lambda v: f"{'-' if v < 0 else ''}${abs(v):,.2f}"

    def tile(label, value, sub=""):
        return f'<div class="tile"><div class="lab">{esc(label)}</div><div class="val">{esc(value)}</div><div class="sub">{esc(sub)}</div></div>'

    tiles = [
        tile("Profit after fees", money(s["pnl"]), f"this month {money(s['month_pnl'])}"),
        tile("100-trade test", (f"{s['n_closed']} / {GATE_TRADES}" if s["n_closed"] < GATE_TRADES else s["gate"].split(" -")[0]),
             f"{s['n_open']} open, must be profitable after fees"),
        tile("Win rate", f"{s['win_rate']:.1f}%" if s["win_rate"] is not None else "n/a",
             f"needs to beat {s['avg_price']:.1f}c average price" if s["avg_price"] is not None else ""),
        tile("Calibration", (f"{s['brier_me']:.3f} vs {s['brier_mkt']:.3f}" if s["brier_me"] is not None else "n/a"),
             ("you vs market, lower is better" if s["brier_me"] is not None else "")),
        tile("Return on stake", f"{s['roi']:+.1f}%" if s["roi"] is not None else "n/a", f"fees paid {money(s['fees'])}"),
        tile("Rule slips", str(s["market_orders"] + s["oversized"] + s["thin_edge"]),
             f"{s['market_orders']} market, {s['oversized']} oversized, {s['thin_edge']} thin edge"),
    ]

    # --- running profit line (inline SVG, one series, zero line for reference) ---
    curve = [0.0] + s["curve"]
    W, H, L, R, T, B = 720, 220, 56, 12, 12, 28
    lo, hi = min(curve + [0]), max(curve + [0])
    if hi == lo:
        hi = lo + 1
    x = lambda i: L + (W - L - R) * (i / max(1, len(curve) - 1))
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(curve))
    chart = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Running profit after each closed trade">'
             f'<line x1="{L}" x2="{W - R}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="zero"/>'
             f'<text x="{L - 6}" y="{y(hi) + 4:.1f}" class="ax" text-anchor="end">{money(hi)}</text>'
             f'<text x="{L - 6}" y="{y(lo) + 4:.1f}" class="ax" text-anchor="end">{money(lo)}</text>'
             f'<text x="{L}" y="{H - 8}" class="ax">trade 0</text>'
             f'<text x="{W - R}" y="{H - 8}" class="ax" text-anchor="end">trade {len(curve) - 1}</text>'
             f'<polyline points="{pts}" class="line"/></svg>') if len(curve) > 1 else "<p class='sub'>No closed trades yet.</p>"

    bars = "".join(
        f'<div class="ms"><span>${m:,}</span><div class="bar"><div style="width:{max(0, min(100, 100 * s["pnl"] / m)):.1f}%"></div></div>'
        f'<span>{max(0, min(100, 100 * s["pnl"] / m)):.0f}%</span></div>' for m in MILESTONES)

    stops = "".join(f'<div class="stop">&#9888; {esc(t)}</div>' for t in s["stops"])
    if not s["deposits"]:
        stops += '<div class="sub">Stops not checked: run with --deposits AMOUNT.</div>'

    recent = sorted(rows, key=lambda r: int(fnum(r["id"])), reverse=True)[:25]
    trs = "".join(
        f"<tr><td>{esc(str(r['id']))}</td><td>{esc(r['opened_at'][:10])}</td><td>{esc((r['title'] or r['ticker'])[:60])}</td>"
        f"<td>{esc(r['side'].upper())} {esc(str(r['contracts']))} @ {esc(str(r['price_cents']))}c</td>"
        f"<td>{esc(str(r['edge_pts']))}</td><td>{esc(r['mode'])}</td><td>{esc(r['result'] or r['status'])}</td>"
        f"<td class='num'>{esc(money(fnum(r['pnl_usd'])) if r['pnl_usd'] != '' else '')}</td></tr>" for r in recent)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Kalshi Log</title>
<style>
:root{{--bg:#f9f9f7;--card:#fcfcfb;--ink:#0b0b0b;--sec:#52514e;--line:#e4e3df;--blue:#2a78d6;--warn:#d03b3b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0d0d0d;--card:#1a1a19;--ink:#f0efec;--sec:#c3c2b7;--line:#383835;--blue:#3987e5;--warn:#e66767}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,Segoe UI,Helvetica,Arial,sans-serif}}
main{{max-width:980px;margin:0 auto;padding:20px 16px 40px}}
h1{{font-size:22px;margin:0 0 2px}} h2{{font-size:15px;margin:26px 0 8px}}
.sub{{color:var(--sec);font-size:12.5px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:16px}}
.tile,.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}}
.lab{{color:var(--sec);font-size:12.5px}} .val{{font-size:22px;font-weight:650;margin:2px 0}}
svg{{width:100%;height:auto}} .line{{fill:none;stroke:var(--blue);stroke-width:2}}
.zero{{stroke:var(--sec);stroke-width:1;stroke-dasharray:3 3}} .ax{{fill:var(--sec);font-size:11px}}
.ms{{display:grid;grid-template-columns:70px 1fr 44px;gap:10px;align-items:center;margin:7px 0;font-variant-numeric:tabular-nums}}
.bar{{height:8px;background:var(--line);border-radius:4px;overflow:hidden}} .bar div{{height:100%;background:var(--blue);border-radius:4px}}
.stop{{color:var(--warn);font-weight:600;margin:6px 0}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--sec);font-weight:600}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
</style></head><body><main>
<h1>Kalshi Log</h1><div class="sub">{esc(log_path)} &middot; built {esc(now_iso())}</div>
<div class="tiles">{''.join(tiles)}</div>
{('<h2>Stops</h2><div class="card">' + stops + '</div>') if stops else ''}
<h2>Running profit after fees</h2><div class="card">{chart}</div>
<h2>Milestones</h2><div class="card">{bars}</div>
<h2>Last 25 trades</h2><div class="card scroll"><table><tr><th>#</th><th>Opened</th><th>Market</th><th>Position</th><th>Edge</th><th>Mode</th><th>Result</th><th class="num">P&amp;L</th></tr>{trs}</table></div>
</main></body></html>"""


# ----------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        description="Trade log and scorekeeper for the Kalshi Strategy and Roadmap guide. Read-only: never places orders.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log", default=os.environ.get("KALSHI_LOG", "trade_log.csv"),
                   help="path to the trade log CSV (default: $KALSHI_LOG or ./trade_log.csv)")
    p.add_argument("--demo", action="store_true", help="use Kalshi's demo (practice) API instead of production")
    p.add_argument("--key-id", help="API key id (or set KALSHI_API_KEY_ID). Only for fills / balance.")
    p.add_argument("--key-file", help="path to your private key .pem (or set KALSHI_PRIVATE_KEY_PATH)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create an empty log").set_defaults(func=cmd_init)

    s = sub.add_parser("lookup", help="show a market's prices and rules")
    s.add_argument("ticker")
    s.add_argument("--rules", action="store_true", help="also print the secondary rules text")
    s.set_defaults(func=cmd_lookup)

    s = sub.add_parser("add", help="log a trade")
    s.add_argument("--ticker", required=True, help="Kalshi market ticker")
    s.add_argument("--side", required=True, choices=["yes", "no"], help="which contract you bought")
    s.add_argument("--price", required=True, type=float, help="price you paid per contract for your side, in cents (1-99)")
    s.add_argument("--contracts", required=True, type=int, help="number of contracts")
    s.add_argument("--my-prob", required=True, type=float,
                   help="YOUR probability the market resolves YES, in percent. Write it down before you look at the price.")
    s.add_argument("--order-type", choices=["limit", "market"], default="limit")
    s.add_argument("--bankroll", type=float, help="your account size right now, in dollars (for the sizing rule)")
    s.add_argument("--fee", type=float, help="actual entry fee in dollars, if different from the estimate")
    s.add_argument("--niche", default="", help="your category label, e.g. econ, weather, tech")
    s.add_argument("--thesis", default="", help="one line on why your number differs from the market")
    s.add_argument("--rules-read", action="store_true", help="confirm you read the market's resolution rules")
    s.add_argument("--paper", action="store_true", help="practice trade, no money (kept out of stats by default)")
    s.add_argument("--title", default="", help="market title, if you do not want it pulled from the API")
    s.add_argument("--notes", default="")
    s.add_argument("--offline", action="store_true", help="do not call the API")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("settle", help="close trades whose markets have resolved")
    s.add_argument("--id", type=int, help="settle one trade by hand")
    s.add_argument("--result", choices=["win", "loss"], help="used with --id")
    s.set_defaults(func=cmd_settle)

    s = sub.add_parser("sell", help="record selling a position before settlement")
    s.add_argument("--id", required=True, type=int)
    s.add_argument("--exit-price", required=True, type=float, help="price you sold at, in cents")
    s.add_argument("--exit-fee", type=float, help="actual exit fee in dollars (default: taker estimate)")
    s.set_defaults(func=cmd_sell)

    for name, func, text in (("stats", cmd_stats, "print the scorecard"),
                             ("dashboard", cmd_dashboard, "write (and optionally serve) the HTML dashboard")):
        s = sub.add_parser(name, help=text)
        s.add_argument("--deposits", type=float, help="total dollars you have deposited (needed for the stop rules)")
        s.add_argument("--include-paper", action="store_true", help="count paper trades too")
        if name == "dashboard":
            s.add_argument("--out", default="dashboard.html", help="where to write the HTML file")
            s.add_argument("--serve", type=int, metavar="PORT", help="also serve it at http://127.0.0.1:PORT")
        s.set_defaults(func=func)

    s = sub.add_parser("fee", help="fee calculator")
    s.add_argument("--price", required=True, type=float)
    s.add_argument("--contracts", required=True, type=float)
    s.set_defaults(func=cmd_fee)

    s = sub.add_parser("size", help="position size under the 1%%-3%% rule")
    s.add_argument("--bankroll", required=True, type=float)
    s.add_argument("--price", required=True, type=float)
    s.add_argument("--my-prob", required=True, type=float, help="your probability of YES, percent")
    s.add_argument("--side", choices=["yes", "no"], default="yes")
    s.set_defaults(func=cmd_size)

    s = sub.add_parser("fills", help="download raw fills to CSV (needs API key)")
    s.add_argument("--out", default="kalshi_fills_raw.csv")
    s.set_defaults(func=cmd_fills)

    sub.add_parser("balance", help="show cash balance (needs API key)").set_defaults(func=cmd_balance)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.log = os.path.expanduser(args.log)
    args.base = DEMO_BASE if args.demo else PROD_BASE
    # Basic sanity checks shared by several commands.
    price = getattr(args, "price", None)
    if price is not None and not (1 <= price <= 99):
        sys.exit("--price is in cents and must be between 1 and 99.")
    prob = getattr(args, "my_prob", None)
    if prob is not None and not (0 <= prob <= 100):
        sys.exit("--my-prob is a percent between 0 and 100.")
    try:
        args.func(args)
    except urllib.error.HTTPError as err:
        # 401 = key id or key file not accepted. 429 = too many requests, wait a minute.
        hint = {401: "Kalshi rejected the API key. Check the key id and the .pem file.",
                429: "Kalshi is rate limiting. Wait a minute and try again."}.get(err.code, "")
        sys.exit(f"Kalshi API error {err.code}. {hint}")
    except urllib.error.URLError as err:
        sys.exit(f"Could not reach Kalshi: {err.reason}")


if __name__ == "__main__":
    main()
