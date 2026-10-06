# Agent City agents

Agents for Hive that work out of the box:
none of them needs an API key. Each one is a folder with a `SOUL.md` (its
personality and scope) and a `tools.py` (its tools, using only Python's
standard library), plus `evals.json` and a `README.md`.

| Folder | Name | What it does |
|---|---|---|
| [`abcau/`](agents/abcau/) | Dispatch | Latest Australian and world headlines from ABC News, via public RSS. |
| [`ainews/`](agents/ainews/) | Scoop | The top AI and tech stories right now: headline, one-sentence summary and link, from public tech-news RSS feeds. |
| [`clock/`](agents/clock/) | Tock | Current time anywhere and time conversion between zones. Local only, no network. |
| [`flights/`](agents/flights/) | Skye | Flights arriving at any airport: what's inbound now and what just landed, via adsb.lol, OpenSky and OurAirports. |
| [`money/`](agents/money/) | Penny | Currency conversion at the latest rate, with the rate's date, via Frankfurter. |
| [`shares/`](agents/shares/) | Ticker | NASDAQ and ASX share prices, plus recent announcements (ASX) or news (NASDAQ), via public feeds. |
| [`weather/`](agents/weather/) | Nimbus | Current conditions and short forecasts for any place, via Open-Meteo. |

## Installing

In Hive, open **Settings → Agent sources** and add
`https://github.com/sirsjg/agent-city-agents`. The agents show up on the
**Browse** tab of the Agents page. Review an agent's code, then install it.

Which folders are offered is set by [`hive.json`](hive.json).
