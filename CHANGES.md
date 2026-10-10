# Changes since the last handoff

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
- `docs/firehose_sample.pdf`: a sample of the PDF that `firehose` produces
- `docs/EXAMPLES.md`: 77 example commands with explanations
- `README.md`: rewritten, with a clickable table of contents, every command and option, and the math
- `prompts/00_MASTER_PROMPT.txt`: version 2.0, describes the all-in-one tool
- `prompts/04_log_and_script.txt` is now `prompts/04_log_and_tool.txt`; modules 05 and 06 updated to match
- `templates/`: commands updated from `kalshi_log.py` to `kalshi_cli.py log`

## Unchanged

The guide PDF, the MP3, the narration text, `trade_log_template.csv`, `sample_trade_log.csv`, `.gitignore`, `prompts_used.txt`.
