# Pre-trade checklist

Run this before every order. If any box stays empty, skip the trade.

- [ ] The market is in my niche: ____________
- [ ] I wrote my probability of YES **before** looking at the price: ____ %
- [ ] Market price for my side: ____ cents. My edge: ____ points (needs 5 or more)
- [ ] The price is 15 cents or higher (no longshots)
- [ ] I read the market rules. Resolution source: ____________  Cutoff time: ____________
- [ ] I can say in one sentence why my number differs from the market: ____________
- [ ] Size is 1% to 3% of bankroll: $____ of $____ = ____ %
- [ ] Related open positions that would lose with this one total under 20% of bankroll
- [ ] It is a limit order at my price, not a market order
- [ ] It is not a combo, a live sports market, a 15-minute crypto market, or a perpetual future
- [ ] I am not trading to win back a loss or reacting to news I just saw
- [ ] No stop is active (monthly 15%, hard 40%)

Then log it:

    python kalshi_log.py add --ticker ____ --side ____ --price ____ --contracts ____ --my-prob ____ --bankroll ____ --niche ____ --rules-read --thesis "____"
