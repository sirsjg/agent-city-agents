# shares — Ticker

Share prices for NASDAQ and ASX listed companies, plus their recent
announcements. Prices come from Yahoo Finance's public chart endpoint (may be
delayed up to 15 minutes). ASX announcements come from the ASX's own feed;
NASDAQ has no single announcements feed, so those are news headlines from
Yahoo Finance. No API keys needed.

| Tool | Does |
|---|---|
| `find_ticker(company)` | Looks up the ticker and exchange (NASDAQ or ASX) for a company name |
| `get_share_price(ticker, exchange)` | Latest price, currency, day change (from the previous close), day and 52-week range, change over the past month. The browser shows these as stats and a one-month price chart. |
| `get_announcements(ticker, exchange, limit?)` | Recent announcements (ASX) or news headlines (NASDAQ), newest first |

`exchange` is `NASDAQ` or `ASX`. Tickers are symbols (`AAPL`, `BHP`); `BHP.AX` and `ASX:BHP` forms also work.

```bash
python3 chat.py shares                       # from the hive root
python3 chat.py shares "What's BHP trading at?"  # one-shot
```
