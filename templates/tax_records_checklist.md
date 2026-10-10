# Tax records checklist

The IRS has not said how prediction market profits are taxed, and Kalshi's forms do not calculate cost basis. Keep your own records and ask a tax preparer. This is not tax advice.

Each month:
- [ ] Set aside 25% to 30% of that month's profit
- [ ] Save the Kalshi monthly statement
- [ ] Record deposits and withdrawals with dates

Each January:
- [ ] Export the full trade history from Kalshi
- [ ] Run `python kalshi_log.py fills --out fills_YEAR.csv` if you have an API key
- [ ] Save a copy of `trade_log.csv` as of December 31
- [ ] Total the year: gross winnings, gross losses, fees, net (keep winnings and losses as separate totals)
- [ ] Collect any 1099 forms (1099-B, 1099-MISC for bonuses, 1099-INT for interest)
- [ ] Give all of it to a tax preparer and ask which treatment they will use
