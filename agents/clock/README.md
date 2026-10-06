# clock — Tock

Tells the time anywhere and converts times between timezones. Uses Python's
built-in timezone database, so there are no network calls and tools respond instantly.

| Tool | Does |
|---|---|
| `get_time(timezone)` | Current time, date, weekday, UTC offset and daylight-saving status |
| `convert_time(time, from_timezone, to_timezone, on_date?)` | Converts a wall-clock time between zones |

Timezones are IANA names (`Asia/Tokyo`). Bare city names that match a zone
(`Tokyo`, `New York`) also work as a fallback.

```bash
python3 chat.py clock                       # from the hive root
python3 chat.py clock "Time in Tokyo?"      # one-shot
```

Needs `AGENT_REASONING=low` on local reasoning models. With `none`, it invents
times about half the time instead of calling its tools.
