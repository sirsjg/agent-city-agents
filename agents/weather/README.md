# weather — Nimbus

The hive's first agent. Answers weather questions using live data from
Open-Meteo (free, no API key).

| Tool | Does |
|---|---|
| `get_weather(location, days?)` | Current conditions plus a daily forecast. Always returns at least today and tomorrow (up to 7 days), each labelled today / tomorrow / weekday. |

```bash
python3 chat.py weather                                   # from the hive root
python3 chat.py weather "Umbrella in Melbourne tomorrow?" # one-shot
```

Needs the tool reliably: use `AGENT_REASONING=low` on local reasoning models.
With `none`, it occasionally answers without checking and invents weather.
