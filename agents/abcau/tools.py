"""Tools for the abcau agent.

Each tool has two parts:
  - a schema in OpenAI function-calling format (listed in TOOLS)
  - a Python implementation (registered in HANDLERS, keyed by the same name)

Reads the ABC News (Australia) Top Stories RSS feed for general news - no API
key needed, and no HTML scraping, so it keeps working when the site changes its
layout. The Top Stories feed is general news, not a tech or sport feed.
"""

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

SOURCE = "ABC News"
FEED_URL = "https://www.abc.net.au/news/feed/45910/rss.xml"


def _text(el):
    return (el.text or "").strip() if el is not None else ""


def _clean(raw):
    """Strip tags and entities from a feed description."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def _first_sentence(text, limit=220):
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    sentence = match.group(1) if match else text
    return sentence if len(sentence) <= limit else sentence[:limit].rsplit(" ", 1)[0] + "…"


def _parse_date(raw):
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def get_headlines(count=5):
    """Fetch the latest general news headlines from ABC News."""
    count = max(1, min(int(count), 10))

    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "hive-abcau/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            root = ET.fromstring(resp.read())
    except Exception as e:
        raise RuntimeError(f"Could not reach the {SOURCE} feed ({e}). Try again later.") from e

    stories, seen = [], set()
    for item in root.iter("item"):
        title = _clean(_text(item.find("title")))
        link = _text(item.find("link"))
        if not title or not link or title.lower() in seen:
            continue
        seen.add(title.lower())
        stories.append({
            "headline": title,
            "summary": _first_sentence(_clean(_text(item.find("description")))) or title,
            "link": link,
            "published": _parse_date(_text(item.find("pubDate"))),
        })

    stories.sort(key=lambda s: s["published"], reverse=True)
    return {
        "source": SOURCE,
        "stories": [
            {**s, "published": s["published"].strftime("%Y-%m-%d %H:%M UTC")}
            for s in stories[:count]
        ],
    }


# --- OpenAI function-calling schemas -----------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_headlines",
            "description": "Get the latest general news headlines from ABC News (Australian and world top stories, not a tech feed). Each story has a headline, a one-sentence summary and a link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "How many stories to return, 1-10 (default 5).",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": [],
            },
        },
    },
]

HANDLERS = {
    "get_headlines": get_headlines,
}


# --- how results are shown in the browser (core/views.py) --------------------

VIEWS = {
    "get_headlines": {"type": "cards", "title": "{source} headlines", "rows": "stories", "text": "headline", "subtitle": "published",
                      "body": "summary", "link": "link"},
}
