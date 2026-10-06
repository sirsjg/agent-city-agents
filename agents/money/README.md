# money — Penny

Converts an amount between currencies at the latest rate, and reports the date
of that rate. Uses the free [Frankfurter](https://frankfurter.dev) API (European
Central Bank reference rates, no API key). The ECB publishes once per working
day, so the rate date can lag on weekends and holidays.

| Tool | Does |
|---|---|
| `convert(amount, from, to)` | Converts between two currencies; returns the result, the rate and the rate date |

Currencies are three-letter ISO codes (`USD`, `EUR`, `GBP`). Common symbols and
names (`$`, `euros`) are mapped as a fallback. Only the currencies the ECB
publishes are supported.

```bash
python3 chat.py money                              # from the hive root
python3 chat.py money "How much is 100 dollars in euros?"   # one-shot
```
