"""Tools for the shares agent.

Each tool has two parts:
  - a schema in OpenAI function-calling format (listed in TOOLS)
  - a Python implementation (registered in HANDLERS, keyed by the same name)

Prices come from Yahoo Finance's public chart endpoint. ASX announcements come
from the ASX's own announcements feed; NASDAQ news comes from Yahoo Finance's
search endpoint. No API keys needed. Prices can be delayed up to 15 minutes.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
ASX_ANNOUNCEMENTS_URL = "https://asx.api.markitdigital.com/asx-research/1.0/companies/{code}/announcements"
HEADERS = {"User-Agent": "Mozilla/5.0 (hive shares agent)", "Accept": "application/json"}

EXCHANGES = ("NASDAQ", "ASX")


def _get_json(url, params=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"The data source returned an error (HTTP {e.code}). Try again shortly.")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach the data source: {e.reason}. Try again shortly.")


def _parse(ticker, exchange):
    """Return (symbol, exchange), e.g. ('BHP', 'asx'), accepting 'BHP.AX' or 'ASX:BHP' forms."""
    ticker = ticker.strip().upper()
    exchange = (exchange or "").strip().upper()
    if ":" in ticker:
        prefix, ticker = ticker.split(":", 1)
        exchange = exchange or prefix
    if ticker.endswith(".AX"):
        ticker, exchange = ticker[:-3], exchange or "ASX"
    if not re.fullmatch(r"[A-Z0-9.\-]{1,10}", ticker):
        raise ValueError(f"'{ticker}' doesn't look like a ticker symbol. Use something like 'AAPL' (NASDAQ) or 'BHP' (ASX).")
    if exchange not in EXCHANGES:
        raise ValueError("Say which exchange: 'NASDAQ' or 'ASX'.")
    return ticker, exchange


def _yahoo_symbol(ticker, exchange):
    return f"{ticker}.AX" if exchange == "ASX" else ticker


def _when(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d")


def get_share_price(ticker, exchange):
    """Latest price for a NASDAQ or ASX listed share."""
    symbol, exchange = _parse(ticker, exchange)
    data = _get_json(CHART_URL + urllib.parse.quote(_yahoo_symbol(symbol, exchange)),
                     {"range": "1mo", "interval": "1d"})
    result = ((data or {}).get("chart") or {}).get("result")
    if not result:
        raise ValueError(f"Could not find {symbol} on {exchange}. Check the ticker and exchange.")
    meta = result[0]["meta"]
    price = meta.get("regularMarketPrice")
    # Daily closes over the month: the last is today (or the latest session), so the one
    # before it is the previous close. (chartPreviousClose is the close before the whole range.)
    quote = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
    history = [{"date": _when(t), "close": round(c, 4)}
               for t, c in zip(result[0].get("timestamp") or [], quote.get("close") or []) if c is not None]
    previous = history[-2]["close"] if len(history) >= 2 else meta.get("chartPreviousClose")
    out = {
        "ticker": symbol,
        "exchange": exchange,
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "price": price,
        "currency": meta.get("currency"),
        "previous_close": previous,
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "52_week_high": meta.get("fiftyTwoWeekHigh"),
        "52_week_low": meta.get("fiftyTwoWeekLow"),
        "as_of": _when(meta["regularMarketTime"]) if meta.get("regularMarketTime") else None,
        "note": "Prices may be delayed up to 15 minutes.",
    }
    if price is not None and previous:
        out["change"] = round(price - previous, 4)
        out["change_pct"] = round((price - previous) / previous * 100, 2)
    if price is not None and len(history) >= 2:
        out["month_change_pct"] = round((price - history[0]["close"]) / history[0]["close"] * 100, 2)
        out["_history"] = history  # for the chart only; the model gets the summary above
    return out


def find_ticker(company):
    """Look up the NASDAQ/ASX ticker for a company name."""
    company = company.strip()
    if not company:
        raise ValueError("Give a company name, e.g. 'Apple' or 'Corporate Travel Management'.")
    data = _get_json(SEARCH_URL, {"q": company, "quotesCount": 10, "newsCount": 0})
    matches = []
    for q in (data or {}).get("quotes") or []:
        symbol = q.get("symbol") or ""
        if symbol.endswith(".AX"):
            matches.append({"ticker": symbol[:-3], "exchange": "ASX", "name": q.get("longname") or q.get("shortname")})
        elif q.get("exchDisp") == "NASDAQ" or q.get("exchange") in ("NMS", "NGM", "NCM"):
            matches.append({"ticker": symbol, "exchange": "NASDAQ", "name": q.get("longname") or q.get("shortname")})
    if not matches:
        raise ValueError(f"No NASDAQ or ASX listing found for '{company}'. It may not be listed on either exchange.")
    return {"query": company, "matches": matches[:5]}


def _asx_announcements(code, limit):
    data = _get_json(ASX_ANNOUNCEMENTS_URL.format(code=urllib.parse.quote(code.lower())))
    items = (((data or {}).get("data") or {}).get("items")) or []
    if not items:
        raise ValueError(f"No ASX announcements found for {code}. Check the ticker.")
    return [
        {
            "date": (item.get("date") or "")[:10],
            "headline": item.get("headline"),
            "price_sensitive": bool(item.get("isPriceSensitive")),
            "link": item.get("url") or None,
        }
        for item in items[:limit]
    ]


def _nasdaq_news(symbol, limit):
    data = _get_json(SEARCH_URL, {"q": symbol, "newsCount": limit, "quotesCount": 0})
    news = (data or {}).get("news") or []
    if not news:
        raise ValueError(f"No recent news found for {symbol}. Check the ticker.")
    return [
        {
            "date": _when(n["providerPublishTime"]) if n.get("providerPublishTime") else "",
            "headline": n.get("title"),
            "source": n.get("publisher"),
            "link": n.get("link"),
        }
        for n in news[:limit]
    ]


def get_announcements(ticker, exchange, limit=5):
    """Recent company announcements (ASX) or news headlines (NASDAQ) for a share."""
    symbol, exchange = _parse(ticker, exchange)
    limit = max(1, min(int(limit), 10))
    if exchange == "ASX":
        return {"ticker": symbol, "exchange": exchange, "kind": "official ASX announcements",
                "announcements": _asx_announcements(symbol, limit)}
    return {"ticker": symbol, "exchange": exchange, "kind": "news headlines (NASDAQ has no single announcements feed)",
            "announcements": _nasdaq_news(symbol, limit)}


# --- OpenAI function-calling schemas -----------------------------------------

_TICKER = {
    "type": "string",
    "description": "Ticker symbol only, e.g. 'AAPL' or 'BHP'. Not the company name.",
}
_EXCHANGE = {
    "type": "string",
    "enum": list(EXCHANGES),
    "description": "'NASDAQ' for US shares like AAPL, MSFT, NVDA; 'ASX' for Australian shares like BHP, CBA, CSL.",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_ticker",
            "description": "Find the ticker symbol and exchange for a company name. Use when the user gives a name you don't already know the ticker for, or when a price lookup fails.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Company name, e.g. 'SpaceX' or 'Corporate Travel Management'.",
                    },
                },
                "required": ["company"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_share_price",
            "description": "Get the latest share price, day change and 52-week range for a NASDAQ or ASX listed company.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": _TICKER, "exchange": _EXCHANGE},
                "required": ["ticker", "exchange"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_announcements",
            "description": "Get the most recent announcements for a NASDAQ or ASX listed company (official ASX announcements, or news headlines for NASDAQ). Only call when asked about announcements or news.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": _TICKER,
                    "exchange": _EXCHANGE,
                    "limit": {
                        "type": "integer",
                        "description": "How many to return, 1-10 (default 5).",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["ticker", "exchange"],
            },
        },
    },
]

HANDLERS = {
    "find_ticker": find_ticker,
    "get_share_price": get_share_price,
    "get_announcements": get_announcements,
}


# --- how results are shown in the browser (core/views.py) --------------------

VIEWS = {
    "get_share_price": [
        {"type": "stats", "title": "{name} ({ticker})", "items": [
            {"label": "Price", "value": "price", "unit": " {currency}", "format": "number",
             "delta": "change_pct", "delta_format": "signed_percent"},
            {"label": "Day range", "value": "{day_low} – {day_high}"},
            {"label": "52-week high", "value": "52_week_high", "format": "number"},
            {"label": "52-week low", "value": "52_week_low", "format": "number"},
            {"label": "Past month", "value": "month_change_pct", "format": "signed_percent"},
        ]},
        {"type": "chart", "kind": "area", "title": "{ticker}, past month", "rows": "_history",
         "x": "date", "x_format": "date", "y": "close", "format": "number"},
    ],
    "get_announcements": {"type": "list", "title": "{ticker} {kind}", "rows": "announcements",
                          "text": "headline", "detail": "date", "link": "link"},
    "find_ticker": {"type": "table", "rows": "matches",
                    "columns": {"ticker": "Ticker", "exchange": "Exchange", "name": "Company"}},
}
