"""Tools for the ainews agent.

Each tool has two parts:
  - a schema in OpenAI function-calling format (listed in TOOLS)
  - a Python implementation (registered in HANDLERS, keyed by the same name)

Reads the public RSS/Atom feeds of a few tech news sites - no API key needed,
and no HTML scraping, so it keeps working when the sites change their layout.
"""

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

# (source name, feed url, True if the feed covers more than AI and needs filtering)
FEEDS = [
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/", False),
    ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", False),
    ("VentureBeat", "https://venturebeat.com/category/ai/feed/", False),
    ("Wired", "https://www.wired.com/feed/tag/ai/latest/rss", False),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/", True),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/technology-lab", True),
]

AI_WORDS = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|llm|chatbot|openai|anthropic|claude|gemini|"
    r"chatgpt|gpt|nvidia|machine learning|neural|deepmind|copilot|generative|agentic)\b",
    re.I,
)
ATOM = "{http://www.w3.org/2005/Atom}"
MAX_PER_SOURCE = 2  # so one busy site can't fill the whole list


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
        dt = parsedate_to_datetime(raw) if "," in raw else datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fetch_feed(feed):
    source, url, needs_filter = feed
    req = urllib.request.Request(url, headers={"User-Agent": "hive-ainews/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            root = ET.fromstring(resp.read())
    except Exception:
        return []  # one dead feed shouldn't sink the answer

    stories = []
    for item in list(root.iter("item")) + list(root.iter(ATOM + "entry")):
        title = _clean(_text(item.find("title")) or _text(item.find(ATOM + "title")))
        link = _text(item.find("link"))
        if not link:
            atom_link = item.find(ATOM + "link")
            link = atom_link.get("href", "") if atom_link is not None else ""
        body = (_text(item.find("description")) or _text(item.find(ATOM + "summary"))
                or _text(item.find(ATOM + "content")))
        summary = _clean(body)
        published = (_text(item.find("pubDate")) or _text(item.find(ATOM + "published"))
                     or _text(item.find(ATOM + "updated")))
        if not title or not link:
            continue
        if needs_filter and not AI_WORDS.search(f"{title} {summary}"):
            continue
        stories.append({
            "headline": title,
            "summary": _first_sentence(summary) or title,
            "link": link,
            "source": source,
            "published": _parse_date(published),
        })
    return stories


def get_ai_news(count=5):
    """Fetch the latest AI stories from several tech news sites."""
    count = max(1, min(int(count), 10))

    with ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
        results = list(pool.map(_fetch_feed, FEEDS))
    if not any(results):
        raise RuntimeError("Could not reach any of the news feeds. Check the connection and try again.")

    # Newest first, but cap each source so the list mixes outlets.
    all_stories = sorted((s for stories in results for s in stories),
                         key=lambda s: s["published"], reverse=True)
    per_source, picked, seen = {}, [], set()
    for story in all_stories:
        key = story["headline"].lower()
        if key in seen or per_source.get(story["source"], 0) >= MAX_PER_SOURCE:
            continue
        seen.add(key)
        per_source[story["source"]] = per_source.get(story["source"], 0) + 1
        picked.append(story)
        if len(picked) == count:
            break

    return {
        "stories": [
            {**s, "published": s["published"].strftime("%Y-%m-%d %H:%M UTC")} for s in picked
        ],
        "sources_checked": [name for (name, _, _), r in zip(FEEDS, results) if r],
    }


# --- OpenAI function-calling schemas -----------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_ai_news",
            "description": "Get the latest AI news stories from top tech sites (TechCrunch, The Verge, VentureBeat, Wired, MIT Technology Review, Ars Technica). Each story has a headline, a one-sentence summary, a link and its source.",
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
    "get_ai_news": get_ai_news,
}


# --- how results are shown in the browser (core/views.py) --------------------

VIEWS = {
    "get_ai_news": {"type": "cards", "title": "Latest AI news", "rows": "stories", "text": "headline", "subtitle": "source",
                    "body": "summary", "link": "link"},
}
