# Kalshi toolkit

Everything the guide refers to, in one folder.

| File | What it is |
|---|---|
| `guide/Kalshi_Strategy_Roadmap.pdf` | The guide (8 pages, clickable contents) |
| `guide/Kalshi_Strategy_Roadmap_Audio.mp3` | Audio version, 10.6 minutes, female voice |
| `guide/audio_narration_script.txt` | The text the audio was read from |
| `kalshi_scan.py` | Market browser: find markets from the command line, then open them in the app |
| `kalshi_log.py` | The trade log script |
| `trade_log_template.csv` | Empty log with the right columns |
| `sample_trade_log.csv` | 40 made-up trades so you can try `stats` and `dashboard` right away |
| `templates/pre_trade_checklist.md` | Run before every order |
| `templates/risk_rules_card.md` | Sizing and stop rules on one page |
| `templates/roadmap_phase_gates.md` | Phase checklist and deposits record |
| `templates/review_templates.md` | Weekly review and 100-trade block review |
| `templates/niche_research_worksheet.md` | Data sources and rules quirks for your niche |
| `templates/tax_records_checklist.md` | What to save for tax time |
| `prompts/00_MASTER_PROMPT.txt` | One prompt that rebuilds the whole package |
| `prompts/01` to `07` | The same build split into seven standalone prompts: research, guide, audio, script, docs, test, deliver |
| `prompts_used.txt` | The original six prompts, verbatim |
| `CHANGES.md` | What changed since the last handoff |

## Find markets: `kalshi_scan.py`

Browse Kalshi without the app. It pulls every open market, lets you filter and rank them, and prints each market's ticker and kalshi.com link so you can go straight to it. It is read-only and never places an order. No API key is needed.

    python kalshi_scan.py categories                          # what is open, with counts
    python kalshi_scan.py top --category economics --top 10   # top 10 in a category
    python kalshi_scan.py top --all --under 10 --top 20       # 20 busiest contracts under 10 cents
    python kalshi_scan.py top --search fed rate --top 5       # keyword search
    python kalshi_scan.py top --preset guide                  # markets that fit the guide's rules
    python kalshi_scan.py show KXFEDDECISION-26OCT-H0         # one market in detail, live
    python kalshi_scan.py event KXFEDDECISION-26OCT           # every outcome in one event
    python kalshi_scan.py watch TICKER --every 60             # print a line when the price moves
    python kalshi_scan.py open TICKER                         # open the page in your browser

The first `top` or `categories` run downloads about 140,000 markets and takes 1 to 2 minutes. The list is then saved for 30 minutes, so the next queries are instant. Put `--refresh` before the command to re-download. `show`, `event` and `watch` are always live.

### Filters for `top`

| Group | Flags |
|---|---|
| What to look at | `--category NAME...`, `--skip-category NAME...`, `--search WORD...`, `--any-word`, `--exclude WORD...`, `--series TICKER...` |
| Price in cents | `--under`, `--over`, `--min-price`, `--max-price`, `--side yes/no/either` |
| Activity | `--min-volume-24h`, `--min-volume`, `--min-interest`, `--min-move`, `--max-spread`, `--min-spread` |
| Timing | `--closes-within DAYS`, `--closes-after DAYS`, `--opened-within DAYS` |
| Ranking | `--sort`, `--top N`, `--one-per-event` |
| Output | `--csv FILE`, `--json`, `--open N` (opens the first N in your browser), `--no-links` |

Categories: ai, business, commodities, companies, crypto, economics, elections, entertainment, financials, health, mentions, politics, social, sports, tech, transportation, weather, world.

Sorts: hot, volume, interest, movers, gainers, losers, closing, newest, cheapest, priciest, wide-spread, tight-spread.

Presets (`--preset NAME`, and any flag you add overrides it):

| Preset | What it finds |
|---|---|
| `hot` | Most traded in the last 24 hours |
| `longshots` | Contracts under 10 cents |
| `favorites` | Contracts at 85 cents and up |
| `coinflips` | Contracts between 40 and 60 cents |
| `movers` | Biggest price change since yesterday |
| `closing-soon` | Closing in the next 2 days |
| `new` | Opened in the last 2 days |
| `wide` | Bid-ask gap of 5 to 20 cents with real volume: room to post a limit order |
| `tight` | Bid-ask gap of 2 cents or less |
| `guide` | Fits the guide: 60 to 95 cents, closes within 30 days, active, no sports or crypto |

### Reading the table

- **BUY**: the side and price you would pay right now.
- **YES b/a**: best bid and ask for YES.
- **CHG**: change in the last price since yesterday, in cents.
- **SPRD**: gap between bid and ask.
- **PAYS**: what $1 becomes if that side wins, before fees.
- **ENDS**: time until the market closes.
- The second line is the ticker, category and link.

`show` adds the fee, the break-even probability for each side, the rules and the order book.

### API key as an environment variable

Only the `account` command (balance and positions) needs it. In PowerShell:

    setx KALSHI_API_KEY_ID "your-key-id"
    setx KALSHI_PRIVATE_KEY_PATH "C:\path\to\kalshi-key.pem"

Open a new PowerShell window afterwards. For the current window only, use `$env:KALSHI_API_KEY_ID = "..."` instead. Also run `pip install cryptography` once. Both scripts read the same two variables.

## Log trades: `kalshi_log.py`

Needs Python 3.9 or newer. No installs for normal use.

The script is read-only. It never places, changes or cancels an order. You place trades in the Kalshi app and log them here.

Pick where the log lives and tell the script once:

    # Windows PowerShell
    $env:KALSHI_LOG = "$HOME\kalshi\trade_log.csv"
    # macOS / Linux
    export KALSHI_LOG=~/kalshi/trade_log.csv

Or pass `--log PATH` on any command.

### Daily use

    python kalshi_log.py init                      # once, creates the log
    python kalshi_log.py lookup TICKER             # prices and resolution rules
    python kalshi_log.py size --bankroll 500 --price 62 --my-prob 70
    python kalshi_log.py add --ticker TICKER --side yes --price 62 --contracts 20 --my-prob 70 --bankroll 500 --niche econ --rules-read --thesis "why I disagree with the price"
    python kalshi_log.py settle                    # closes every trade whose market has resolved
    python kalshi_log.py sell --id 7 --exit-price 71   # if you sold early
    python kalshi_log.py stats --deposits 500
    python kalshi_log.py dashboard --deposits 500 --serve 8765   # then open http://127.0.0.1:8765

The ticker is the last part of the market's web address on Kalshi.

`--my-prob` is always your probability that the market resolves YES, even when you buy NO. The script works out your edge from it.

Add `--paper` to `add` for practice trades. They stay out of the stats unless you pass `--include-paper`.

`add` warns when a trade breaks a rule from the guide (edge under 5 points, price under 15 cents, size over 3%, market order, rules not read). It still logs the trade.

Try it on the sample first:

    python kalshi_log.py --log sample_trade_log.csv stats --deposits 500

### What the scorecard tells you

- **Profit after fees** and progress to each milestone.
- **Win rate vs average price paid.** You profit only if the first is higher.
- **Calibration.** Your probabilities scored against the market's prices. If yours are not better, the profit is probably luck.
- **100-trade test.** PASS, FAIL or how far along you are.
- **Stops.** The monthly 15% and hard 40% rules, checked against `--deposits`.
- **Rule slips.** How often you broke your own rules.

### Fees in the log

Market orders get the standard fee estimate (7% x price x (1 - price), rounded up to the cent). Limit orders are logged at $0, which is right for most markets. If the app shows a different fee, pass `--fee AMOUNT` and the log uses that.

### Every write is backed up

Each change copies the log to `trade_log.csv.bak` first. You can also open the CSV in Excel, but close it before running the script.

## The Kalshi API

Yes, there is one, and it is free.

- **Public data, no login:** markets, prices, rules, results. `lookup`, `add` and `settle` use this.
- **Your account, API key needed:** balance, fills, positions, and placing orders. `balance` and `fills` use the first two.

### API key setup (only for `balance` and `fills`)

1. In your Kalshi account settings, create an API key. Kalshi gives you a key ID and a private key file. Save the file somewhere private. Kalshi does not show it again.
2. `pip install cryptography`
3. Set two variables:

       KALSHI_API_KEY_ID=your-key-id
       KALSHI_PRIVATE_KEY_PATH=/path/to/kalshi-key.pem

4. `python kalshi_log.py balance`

An API key can place orders on your account. Treat the key file like a password and keep it out of shared folders and Git.

### Placing orders from the script

The API supports it. This version leaves it out on purpose. Order code that has not been run against a real account can lose money on a bug, and the guide's method is slow, deliberate limit orders, which the app handles well. If you want it later, the safe route is to build it against Kalshi's demo environment (the script already has a `--demo` flag) and require a typed confirmation per order.

## What was tested

- `kalshi_scan.py`: `categories`, `top` with every preset and filter group, `show`, `event` and `watch` against the live Kalshi API on October 10, 2026. The link format was checked against kalshi.com.
- **Not tested:** `kalshi_scan.py account`, since it needs your key. `--open` and `open` were not run here because this build machine has no browser to launch.

- `lookup`, `add` and `settle` against the live Kalshi API on October 10, 2026, using open and settled Fed markets.
- Fee math against Kalshi's published example (100 contracts at 55 cents = $1.74).
- The signing code for `balance` and `fills` against test keys, and that Kalshi answers a bad key with a clean 401.
- **Not tested:** `balance` and `fills` with a real account, since that needs your key. Run `balance` first; if it prints your balance, `fills` will work too.

This is general information, not financial, legal or tax advice.
