# Changes since the last handoff

## Latest: search, market reading, links, chains

- `search WORD...`: every open market matching your keywords with no top-N cut. Options: `--any`, `--phrase`, `--not`,
  `--regex`, `--in`, `--tag`, `--series`, `--under`, `--over`, `--closes-within`, `--group`, `--count`, `--tickers`,
  `--limit`, `--sort`, `--format`. `top --top 0` also means no cut
- `flow`: the trade tape for one market, for every market matching a search, or for the whole exchange. `--since 12:00`
  starts at a local time of day
- `pressure`: order books that lean hard to one side
- `rewards`: markets with a Kalshi incentive program, with pool size and period
- `sources`: settlement source, contract terms, fee type and fee changes for a market or series
- `tags`, `status`: Kalshi's own tags; whether the exchange is open
- `chain`: several commands in a row, row numbers carried over
- `selftest`: 13 arithmetic checks and 30 real commands, pass or FAIL for each
- Clickable links in the terminal for every market address and ticker (`--links never` turns them off)
- A colored banner the first time the tool runs, when run with no arguments, and with `banner`
- Dashboard: chains with `;`, a queue mode that builds a chain from button clicks, recent-command chips, Clear,
  new buttons for search, flow, pressure, rewards, tags, status and sources; every link opens in a new tab
- `serve --browser chrome|edge|firefox|brave|default|PATH`. Chrome is the default
- 32 new examples (122 total), 7 more runs in `firehose`, README, master prompt and demo updated

## Latest: `multiply`

- New command `multiply` (short form `x`): give a stake and a multiple, e.g. `multiply 5x --stake 20`.
  It lays out a **single** bet, a **chain** of bets in a row and a **spread** of bets placed together,
  then a side-by-side table with the odds of each and the ready `buy` lines (dry runs)
- `--days N` sets how far out the bets may resolve: default 3, capped at 7
- `--route all|single|multi|chain|spread` picks which routes to show; `--max-bets 2|3|4` limits the multi-bet routes
- `--format json|csv|markdown` and `--out FILE` work as on the other list commands
- `multiply --sweep [FILE]`: one command that runs 16 variations and writes each command plus its output to a text file
- Added in: the built-in help, 8 new entries in `examples` and `docs/EXAMPLES.md`, a Multiply form on the
  dashboard, three runs in `firehose`, the README (command reference, math section, example table),
  the master prompt and module 04, and the demo GIF

Last handoff: `kalshi_scan.py`, the re-recorded MP3 and the README.

## Renamed and merged

- The project is now called **Kalshi CLI** (was Kalshi toolkit). The file `kalshi_cli.py`, the folder and the repo name are unchanged

- `kalshi_scan.py` is now `kalshi_cli.py`
- `kalshi_log.py` is merged into it and removed. Its commands live under `log`:
  `log init`, `log add`, `log settle`, `log sold` (was `sell`), `log stats`, `log fills`
- `lookup` is now `show`, `size` is now `plan`, `balance` is now `positions`
- The old static `dashboard` HTML file is replaced by `serve`

## New commands

- `analyze`: price history, statistics and a text chart, with `--book`, `--trades N` and `--deep` drill-downs
- `ladder`: an event's prices as a probability distribution
- `anomalies`: prices that break arithmetic (outcome sets, threshold ladders) or their own pattern (moves, volume, quotes)
- `best [N]`: the top-scoring market or the top few; `--by odds` and `--by soonest`
- `picks`: a diversified random draw from the best, with a simulated basket
- `grind`: compound a small stake through a chain of likely winners, with the real odds
- `brief`: a research prompt for an AI assistant
- `plan`: your probability in; edge, Kelly size, checklist, verdict and a ready order out
- `watchlist`, `daily`, `fee`, `examples`
- `buy`, `sell`, `orders`, `cancel`, `positions`: optional trading, dry run unless `--live`
- `serve`: the same tool as a local dashboard
- `firehose`: runs every command and binds the output into one color PDF with clickable contents

## New everywhere

- Row numbers from the last list work in place of tickers
- Colored output and a cleaner layout, with `--color auto/always/never`
- An "In plain words" block under most outputs, and a "Next" line
- `--format table/json/csv/markdown` and `--out FILE` on the list commands
- `--demo` for Kalshi's practice exchange
- Retries on dropped connections while downloading the market list

## New and changed files

- `requirements.txt`: the one dependency, with install notes; `.gitignore` now excludes `.venv/`
- `README.md`: new Install section with the one-line install and virtual environment steps

- `docs/demo.gif`: every command, then the dashboard
- `docs/dashboard.png`: dashboard screenshot
- `docs/banner.svg`: the color title banner at the top of the README
- `docs/firehose_sample.pdf`: a sample of the PDF that `firehose` produces
- `docs/EXAMPLES.md`: 77 example commands with explanations
- `README.md`: rewritten, with a clickable table of contents, every command and option, and the math
- `prompts/00_MASTER_PROMPT.txt`: version 2.0, describes the all-in-one tool
- `prompts/04_log_and_script.txt` is now `prompts/04_log_and_tool.txt`; modules 05 and 06 updated to match
- `templates/`: commands updated from `kalshi_log.py` to `kalshi_cli.py log`

## Unchanged

The guide PDF, the MP3, the narration text, `trade_log_template.csv`, `sample_trade_log.csv`, `.gitignore`, `prompts_used.txt`.
