# Agent City agents

Agents for Agent City. News, time, flights, currency, shares and weather work
without service accounts or API keys. Cloudflare DNS needs an API token and
account ID, and Gmail needs a connected Google account.

Each one is a folder with a `SOUL.md` (its
personality and scope) and a `tools.py` (its tools, using only Python's
standard library), plus `evals.json` and a `README.md`.

| Folder | Name | What it does |
|---|---|---|
| [`abcau/`](agents/abcau/) | Dispatch | Latest Australian and world headlines from ABC News, via public RSS. |
| [`ainews/`](agents/ainews/) | Scoop | The top AI and tech stories right now: headline, one-sentence summary and link, from public tech-news RSS feeds. |
| [`clock/`](agents/clock/) | Tock | Current time anywhere and time conversion between zones. Local only, no network. |
| [`cloudflare_dns/`](agents/cloudflare_dns/) | Cloudflare DNS | Manages domains, DNS records, backups and DNSSEC. Needs a Cloudflare API token and account ID. |
| [`flights/`](agents/flights/) | Skye | Flights arriving at any airport: what's inbound now and what just landed, via adsb.lol, OpenSky and OurAirports. |
| [`gmail/`](agents/gmail/) | Gmail | Reads and searches Gmail, and drafts or sends emails. Needs a connected Google account. |
| [`money/`](agents/money/) | Penny | Currency conversion at the latest rate, with the rate's date, via Frankfurter. |
| [`shares/`](agents/shares/) | Ticker | NASDAQ and ASX share prices, plus recent announcements (ASX) or news (NASDAQ), via public feeds. |
| [`weather/`](agents/weather/) | Nimbus | Current conditions and short forecasts for any place, via Open-Meteo. |

## Installing

In Agent City, open **Settings → Agent sources**. This repo is added by default;
if you removed it, add `https://github.com/sirsjg/agent-city-agents` again. The agents show up on the
**Browse** tab of the Agents page. Review an agent's code, then install it.

Which folders are offered is set by [`hive.json`](hive.json).

## Versions

Each agent's version and author are in the frontmatter at the top of its
`SOUL.md`. Bump `version:` when you change an agent (1.0.1 for a fix, 1.1.0
for something new, 2.0.0 if it works differently). Anyone who installed it
then sees **Update available** with the new version, and can review the diff
before updating.
