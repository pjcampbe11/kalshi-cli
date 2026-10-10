# Example commands

80 commands, grouped by what you are trying to do. Each is run as `python kalshi_cli.py <command>`.

A row number from the last list (`1`, `2`, ...) works anywhere a ticker does. Replace `TICKER` and `ORDER_ID` with real values.

## Start here

| Command | What it does |
|---|---|
| `python kalshi_cli.py examples` | This list. Add --search WORD to filter it. |
| `python kalshi_cli.py categories` | Every category with open markets, counts, and its share of today's volume. |
| `python kalshi_cli.py daily` | The morning run-through: watchlist, anomalies, top picks and your scorecard in one go. |
| `python kalshi_cli.py serve` | Open the dashboard at http://127.0.0.1:8765. Same commands, same output, with buttons. |
| `python kalshi_cli.py firehose --open` | Run EVERY command and bind the output into one color PDF with clickable contents, then open it. |
| `python kalshi_cli.py --refresh categories` | Put --refresh before any command to re-download the market list instead of using the saved copy. |

## Find markets

| Command | What it does |
|---|---|
| `python kalshi_cli.py top --category economics --top 10` | The 10 most traded economics markets in the last 24 hours. |
| `python kalshi_cli.py top --all --top 100` | The 100 busiest markets on the whole exchange. |
| `python kalshi_cli.py top --all --under 10 --top 20` | The 20 busiest contracts costing 10 cents or less. Longshots; the guide says to skip them. |
| `python kalshi_cli.py top --category politics elections --over 85 --top 15` | Heavy favorites (85 cents and up) in two categories at once. |
| `python kalshi_cli.py top --search fed rate --top 5` | Keyword search. A market must contain every word. |
| `python kalshi_cli.py top --search bitcoin ethereum --any-word --exclude daily` | Match either word, and drop anything containing 'daily'. |
| `python kalshi_cli.py top --skip-category sports crypto --top 20` | The busiest markets outside sports and crypto. |
| `python kalshi_cli.py top --preset movers --min-volume-24h 5000` | Biggest price changes since yesterday, in markets that actually trade. |
| `python kalshi_cli.py top --preset closing-soon --category weather` | Weather markets that resolve within two days. |
| `python kalshi_cli.py top --preset wide --top 10` | Bid-ask gaps of 5 to 20 cents with real volume: room to rest a limit order. |
| `python kalshi_cli.py top --preset guide --top 10` | Markets that fit the guide: 60 to 95 cents, resolving within 30 days, active. |
| `python kalshi_cli.py top --side no --min-price 60 --max-price 80 --closes-within 7` | NO contracts priced 60 to 80 cents that resolve this week. |
| `python kalshi_cli.py top --category tech ai --sort interest --one-per-event` | Tech and AI ranked by open interest, one market per event. |
| `python kalshi_cli.py top --series KXFED --sort closing` | Every market in one series, soonest first. |
| `python kalshi_cli.py top --category economics --top 3 --open 3` | Show the top 3 and open each one's kalshi.com page in your browser. |

## Look closer

| Command | What it does |
|---|---|
| `python kalshi_cli.py show KXFEDDECISION-26OCT-H0` | One market live: both sides, fee, break-even, rules and the order book. |
| `python kalshi_cli.py show 2` | The same for row 2 of whatever list you printed last. Row numbers work everywhere. |
| `python kalshi_cli.py show 2 --full-rules` | Include the secondary rules text. Read it before trading anything. |
| `python kalshi_cli.py event KXFEDDECISION-26OCT` | Every outcome in one event, with the prices added up. |
| `python kalshi_cli.py ladder KXFEDDECISION-26OCT` | The event as a probability distribution: fair odds with the margin removed, or the implied median for a threshold ladder. |
| `python kalshi_cli.py analyze KXFEDDECISION-26OCT-H0` | Fourteen days of price history as a chart, with range, typical move, trend and volume. |
| `python kalshi_cli.py analyze 1 --interval hour --days 3` | The same at hourly resolution for the last three days. |
| `python kalshi_cli.py analyze 1 --deep` | Drill all the way down: adds the order book depth and the last 15 trades with who was buying. |
| `python kalshi_cli.py analyze 1 --trades 40 --book` | Pick the drill-downs yourself: the last 40 trades and the book. |
| `python kalshi_cli.py watch KXFEDDECISION-26OCT-H0 --every 30` | Re-check every 30 seconds and print a line when the price moves. Ctrl+C stops it. |
| `python kalshi_cli.py open 1` | Open row 1's page on kalshi.com. |

## Find an edge

| Command | What it does |
|---|---|
| `python kalshi_cli.py anomalies` | Scan everything for prices that break arithmetic or break their own pattern. |
| `python kalshi_cli.py anomalies --type overround ladder` | Only the arithmetic breaks: outcome sets and threshold ladders that guarantee a profit on paper. |
| `python kalshi_cli.py anomalies --type move --checks 40` | Moves measured against each market's own normal day, checking the 40 biggest movers. |
| `python kalshi_cli.py anomalies --category economics weather --top 10` | Limit the scan to two categories and show 10 per section. |
| `python kalshi_cli.py --refresh anomalies --type surge dislocation` | Fresh prices, then volume surges and quotes that have left the last trade behind. |
| `python kalshi_cli.py best` | The single top-scoring market after the guide's screen. |
| `python kalshi_cli.py best 3` | The top three by score. Also: best 5, best 10. |
| `python kalshi_cli.py best 5 --category economics weather --bankroll 2500` | Top five inside two categories, sized for a $2,500 bankroll. |
| `python kalshi_cli.py best 5 --by odds` | The five with the best odds of winning, instead of the best score. Small payouts, high hit rate. |
| `python kalshi_cli.py best 5 --by soonest --closes-within 2` | The five that resolve first, so the money comes back quickly. |
| `python kalshi_cli.py grind --start 10 --target 40` | Chain likely winners to compound $10 toward $40, with the real odds of the whole chain. |
| `python kalshi_cli.py grind --start 10 --target 16 --stake 50 --closes-within 2` | A shorter climb, risking half the bankroll per step instead of all of it. |
| `python kalshi_cli.py grind --start 25 --target 100 --min-price 90 --skip-category` | Only 90c-and-up favorites, with sports and crypto included. |
| `python kalshi_cli.py picks --top 10 --bankroll 500` | Ten picks as a weighted random draw from the best 60, one per event, with basket odds. |
| `python kalshi_cli.py picks --top 5 --seed 7` | Fix the random draw so the same five come back. |
| `python kalshi_cli.py picks --top 5 --bias 0` | Turn the pricing-pattern adjustment off: what the picks look like if the market is simply right. |
| `python kalshi_cli.py picks --top 8 --closes-within 3 --min-volume-24h 5000` | Only liquid markets that resolve within three days. |

## Decide

| Command | What it does |
|---|---|
| `python kalshi_cli.py brief KXFEDDECISION-26OCT-H0 --copy` | Write a research prompt for an AI assistant and copy it to the clipboard. Paste it, get a probability back. |
| `python kalshi_cli.py brief 1 --out research.txt` | Save the research prompt to a file instead. |
| `python kalshi_cli.py plan KXFEDDECISION-26OCT-H0 --my-prob 92 --bankroll 500` | Your probability in, edge, expected value, Kelly size, checklist and verdict out. |
| `python kalshi_cli.py plan 1 --my-prob 30 --bankroll 2500 --kelly 0.5 --max-risk 2` | Half Kelly instead of a quarter, capped at 2% of bankroll. |
| `python kalshi_cli.py fee --price 55 --contracts 100` | What a market order would cost in fees, and the break-even it implies. |
| `python kalshi_cli.py watchlist add KXFEDDECISION-26OCT-H0 --note "wait for CPI"` | Remember a market and the price you first saw. |
| `python kalshi_cli.py watchlist` | Your saved markets with live prices and the move since you added each. |

## Trade

| Command | What it does |
|---|---|
| `python kalshi_cli.py buy 1` | After plan or picks: preview the suggested order for row 1. A dry run; nothing is sent. |
| `python kalshi_cli.py buy KXFEDDECISION-26OCT-H0 --side yes --price 83 --count 10 --my-prob 92 --bankroll 500` | Preview a specific limit order with the edge and sizing checks. |
| `python kalshi_cli.py buy 1 --spend 15` | Size by dollars instead of contracts. |
| `python kalshi_cli.py buy 1 --live` | Send it for real. Asks you to type BUY first. Needs your API key. |
| `python kalshi_cli.py --demo buy 1 --live` | The same against Kalshi's practice exchange, with practice money and a demo key. |
| `python kalshi_cli.py orders` | Orders still resting on the book. |
| `python kalshi_cli.py cancel ORDER_ID --live` | Cancel one resting order. Without --live it only shows what it would do. |
| `python kalshi_cli.py positions` | Cash balance and what you hold, with what each would fetch now. |
| `python kalshi_cli.py sell 1 --side yes --count 5 --price 66` | Preview selling 5 contracts of row 1 from positions. Checked against what you hold. |

## Keep score

| Command | What it does |
|---|---|
| `python kalshi_cli.py log init` | Create the trade log (trade_log.csv, or the path in KALSHI_LOG). |
| `python kalshi_cli.py log add --ticker KXFEDDECISION-26OCT-H0 --side yes --price 83 --contracts 10 --my-prob 92 --bankroll 500 --niche econ --rules-read --thesis "no hike signalled"` | Record a trade with your probability and reasoning. |
| `python kalshi_cli.py log add --ticker TICKER --side no --price 70 --contracts 5 --my-prob 20 --paper` | A practice trade that stays out of the scorecard. |
| `python kalshi_cli.py log settle` | Look up results and close every trade whose market has resolved. |
| `python kalshi_cli.py log sold --id 7 --exit-price 71` | Record selling a position before it settled. |
| `python kalshi_cli.py log stats --deposits 500` | The scorecard: profit, win rate, calibration, the 100-trade test, milestones and stops. |
| `python kalshi_cli.py log --file ~/kalshi/2026.csv stats --json` | A different log file, with the scorecard as JSON. |
| `python kalshi_cli.py log fills --out fills_2026.csv` | Download your raw fills from Kalshi for tax records. Needs your API key. |

## Output

| Command | What it does |
|---|---|
| `python kalshi_cli.py top -c weather -n 20 --format markdown --out weather.md` | Any list as a Markdown table in a file. Also csv and json. |
| `python kalshi_cli.py picks -n 10 --format csv --out picks.csv` | Picks as a spreadsheet. |
| `python kalshi_cli.py analyze 1 --format json` | Statistics and price history as JSON for another program. |
| `python kalshi_cli.py --color never top --category economics` | Plain text with no color codes. Color also switches off when you pipe to a file. |
| `python kalshi_cli.py serve --port 9000` | The dashboard on a different port. |
| `python kalshi_cli.py firehose --theme light --out report.pdf` | The everything-report on a white background, for printing. |
| `python kalshi_cli.py --refresh firehose --ticker KXFEDDECISION-26OCT-H0 --my-prob 92 --bankroll 500 --open` | Fresh prices, a market of your choosing, and your own probability in the plan. |
