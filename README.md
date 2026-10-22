# Agent City agents

Agents for Agent City. News, time, flights, currency, shares and weather work
without service accounts or API keys. Cloudflare DNS needs an API token and
account ID, SEQ a Seq server's address and API key, and Gmail and X a connected
account. Telegram and Signal sign in to your own account from their row on the
Connections page (a code for Telegram, a QR code for Signal), and tell you when
something urgent arrives. Docker and Kubectl run the `docker` and `kubectl`
command lines on your machine, so they need those installed (and Docker
Desktop running, or a working kubeconfig).

Each one is a folder with a `SOUL.md` (its personality and scope) and a
`tools.py` (its tools, using Python's standard library and what comes with
Agent City, like Telethon for Telegram), plus `evals.json` and a `README.md`.

| Folder | Name | What it does |
|---|---|---|
| [`abcau/`](agents/abcau/) | Dispatch | Latest Australian and world headlines from ABC News, via public RSS. |
| [`ainews/`](agents/ainews/) | Scoop | The top AI and tech stories right now: headline, one-sentence summary and link, from public tech-news RSS feeds. |
| [`clock/`](agents/clock/) | Tock | Current time anywhere and time conversion between zones. Local only, no network. |
| [`cloudflare_dns/`](agents/cloudflare_dns/) | Cloudflare DNS | Manages domains, DNS records, backups and DNSSEC. Needs a Cloudflare API token and account ID. |
| [`docker/`](agents/docker/) | Docker | Runs the Docker CLI: containers, images, logs, stats, exec, networks, volumes and compose. Asks before removing anything. |
| [`flights/`](agents/flights/) | Skye | Flights arriving at any airport: what's inbound now and what just landed, via adsb.lol, OpenSky and OurAirports. |
| [`gmail/`](agents/gmail/) | Gmail | Reads and searches Gmail, and drafts or sends emails. Needs a connected Google account. |
| [`kubectl/`](agents/kubectl/) | Kubectl | Runs kubectl against your kubeconfig's clusters: pods, logs, events, CPU and memory charts, scale, rollouts, apply. Asks before changing anything. |
| [`money/`](agents/money/) | Penny | Currency conversion at the latest rate, with the rate's date, via Frankfurter. |
| [`seq/`](agents/seq/) | SEQ | Reads application logs from a Seq server: recent errors and warnings, searches, what's failing most, and trends over time. Read-only. Needs the server's address and an API key. |
| [`shares/`](agents/shares/) | Ticker | NASDAQ and ASX share prices, plus recent announcements (ASX) or news (NASDAQ), via public feeds. |
| [`signal/`](agents/signal/) | Signal | Reads your own Signal, linked as another device with signal-cli: what's waiting, what's urgent, and replies after you approve. Needs Agent City 0.28.0. |
| [`telegram/`](agents/telegram/) | Telegram | Reads your own Telegram account: what's waiting, what's urgent, and replies after you approve. Needs your own Telegram app's keys, and Agent City 0.28.0. |
| [`weather/`](agents/weather/) | Nimbus | Current conditions and short forecasts for any place, via Open-Meteo. |
| [`x/`](agents/x/) | X | Reads your own X account through X's official API: mentions, your feed, search, and posting after you approve. Needs your own X app. |

## Installing

In Agent City, open **Settings → Agent sources**. This repo is added by default;
if you removed it, add `https://github.com/sirsjg/agent-city-agents` again. The agents show up on the
**Browse** tab of the Agents page. Review an agent's code, then install it.

Which folders are offered is set by [`hive.json`](hive.json).

## Testing

Telegram, Signal and X lean on Agent City's own hooks (`SIGNIN`, `LISTEN`,
`ASKS`), so their tests live in Agent City (`tests/test_telegram.py`,
`tests/test_signal.py`, `tests/test_x.py`) and run against a checkout of this
repo beside it, or wherever `AGENT_CITY_AGENTS` points:

```bash
cd ../hive && python3 -m unittest tests.test_telegram tests.test_signal tests.test_x
```

## Versions

Each agent's version and author are in the frontmatter at the top of its
`SOUL.md`. Bump `version:` when you change an agent (1.0.1 for a fix, 1.1.0
for something new, 2.0.0 if it works differently). Anyone who installed it
then sees **Update available** with the new version, and can review the diff
before updating. An agent that needs something new in Agent City says the
oldest version it works in with `requires: 0.28.0`; older apps list it but
won't install it.
