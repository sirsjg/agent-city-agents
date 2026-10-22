"""Tools for the seq agent.

Each tool has two parts:
  - a schema in OpenAI function-calling format (listed in TOOLS)
  - a Python implementation (registered in HANDLERS, keyed by the same name)

Reads application logs from a Seq server (https://datalust.co/seq) with its
HTTP API. Read-only: it needs a Seq API key that has the Read permission.
The server's address is the SEQ_URL key.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

NEEDS = {"SEQ_URL": "Your Seq server's address, like https://seq.example.com",
         "SEQ_API_KEY": "A Seq API key with Read permission: in Seq, Settings > API Keys > Add API Key"}
LEVELS = ("Verbose", "Debug", "Information", "Warning", "Error", "Fatal")


def _get(path, params):
    base = os.environ["SEQ_URL"].strip().rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise RuntimeError("SEQ_URL should be your Seq server's address, starting with https://. Fix it on the Connections page.")
    req = urllib.request.Request(
        f"{base}{path}?{urllib.parse.urlencode(params)}",
        # Cloudflare (error 1010) blocks urllib's default User-Agent
        headers={"X-Seq-ApiKey": os.environ["SEQ_API_KEY"], "Accept": "application/json", "User-Agent": "hive-seq/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            raise RuntimeError(
                f"Seq answered {e.code} at {base}{path}: {detail or 'no details'}. "
                "Check SEQ_API_KEY on the Connections page (the key needs Read permission, "
                "and the address must be Seq itself, not a login page in front of it)."
            )
        raise RuntimeError(f"Seq returned {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Seq at {base}: {e.reason}")


def _quote(text):
    return "'" + str(text).replace("'", "''") + "'"


def _utc(minutes_ago):
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _minutes(value, default):
    return max(1, min(int(value or default), 60 * 24 * 14))


def _level_clause(level):
    if not level:
        return None
    match = next((l for l in LEVELS if l.lower() == str(level).lower()), None)
    if not match:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}")
    return f"@Level = {_quote(match)}"


def search_logs(text=None, level=None, minutes=60, limit=20, filter=None):
    """Recent log events, newest first, optionally narrowed by text, level or a Seq filter."""
    limit = max(1, min(int(limit or 20), 50))
    minutes = _minutes(minutes, 60)
    clauses = [c for c in (
        _level_clause(level),
        _quote(text) if text else None,
        f"({filter})" if filter else None,
    ) if c]
    params = {
        "count": limit,
        "render": "true",
        "fromDateUtc": _utc(minutes),
        "toDateUtc": _utc(0),
    }
    if clauses:
        params["filter"] = " and ".join(clauses)
    events = _get("/api/events", params)
    rows = []
    for e in events:
        props = {p["Name"]: p["Value"] for p in e.get("Properties", [])}
        row = {
            "time": e.get("Timestamp"),
            "level": e.get("Level") or "Information",
            "message": (e.get("RenderedMessage") or "")[:300],
            "app": props.get("Application") or props.get("SourceContext") or props.get("Service"),
        }
        if e.get("Exception"):
            row["exception"] = e["Exception"].strip().splitlines()[0][:200]
        rows.append(row)
    return {"window_minutes": minutes, "count": len(rows), "events": rows}


def error_summary(minutes=60, level="Error", limit=10):
    """The most frequent messages at a level (default Error) in a time window."""
    limit = max(1, min(int(limit or 10), 25))
    minutes = _minutes(minutes, 60)
    level = level or "Error"
    sql = (f"select count(*) as n from stream where {_level_clause(level)} "
           f"group by @MessageTemplate as message order by n desc limit {limit}")
    data = _get("/api/data", {"q": sql, "rangeStartUtc": _utc(minutes), "rangeEndUtc": _utc(0)})
    # Seq answers {"Columns": [...], "Rows": [[...], ...]}; find each column by name, not position
    cols = [str(c).lower() for c in data.get("Columns", [])]
    n_at = cols.index("n") if "n" in cols else 1
    m_at = cols.index("message") if "message" in cols else 0
    rows = [{"count": int(r[n_at]), "message": str(r[m_at])[:300]} for r in data.get("Rows", [])]
    return {"window_minutes": minutes, "level": level, "total": sum(r["count"] for r in rows), "top_messages": rows}


def log_trend(minutes=180, level=None, text=None):
    """How many log events there were over time, in even buckets, optionally for one level or text."""
    minutes = _minutes(minutes, 180)
    bucket = "1m" if minutes <= 60 else "5m" if minutes <= 360 else "15m" if minutes <= 1440 else "1h" if minutes <= 4320 else "6h"
    clauses = [c for c in (_level_clause(level), _quote(text) if text else None) if c]
    where = f" where {' and '.join(clauses)}" if clauses else ""
    sql = f"select count(*) as n from stream{where} group by time({bucket}) order by time"
    data = _get("/api/data", {"q": sql, "rangeStartUtc": _utc(minutes), "rangeEndUtc": _utc(0)})
    # Seq answers time-grouped queries as {"Columns": ["n"], "Slices": [{"Time": ..., "Rows": [[n]]}, ...]}
    series = [{"time": s["Time"], "count": sum(int(r[0]) for r in s.get("Rows", []))}
              for s in data.get("Slices", [])]
    counts = [p["count"] for p in series]
    peak = max(series, key=lambda p: p["count"]) if series else None
    return {
        "window_minutes": minutes,
        "level": level or "all levels",
        "bucket": bucket,
        "total": sum(counts),
        "peak": peak["count"] if peak else 0,
        "peak_time": peak["time"] if peak else None,
        "_series": series,
    }


# --- OpenAI function-calling schemas -----------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_logs",
            "description": "Get recent application log events from Seq, newest first. Use for 'show me the latest errors', 'any warnings about X', 'what happened in the last hour'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Words to look for in the message, e.g. 'timeout' or 'payment'. Leave out to see everything."},
                    "level": {"type": "string", "enum": list(LEVELS), "description": "Only events at this level, e.g. 'Error'. Leave out for all levels."},
                    "minutes": {"type": "integer", "description": "How far back to look, in minutes (default 60; a day is 1440)."},
                    "limit": {"type": "integer", "description": "How many events to return, 1-50 (default 20)."},
                    "filter": {"type": "string", "description": "Optional raw Seq filter expression, e.g. \"Application = 'api' and StatusCode >= 500\". Only when the user gives one."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "error_summary",
            "description": "Count the most frequent log messages at a level (default Error) in a time window. Use for 'what is failing the most' or 'how many errors today'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "integer", "description": "How far back to look, in minutes (default 60; a day is 1440)."},
                    "level": {"type": "string", "enum": list(LEVELS), "description": "Level to count (default Error)."},
                    "limit": {"type": "integer", "description": "How many top messages, 1-25 (default 10)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_trend",
            "description": "Count log events over time (a chart). Use for 'when did the errors start', 'is it getting worse', 'show errors over the last day'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "integer", "description": "How far back to look, in minutes (default 180; a day is 1440)."},
                    "level": {"type": "string", "enum": list(LEVELS), "description": "Only count this level, e.g. 'Error'. Leave out to count everything."},
                    "text": {"type": "string", "description": "Only count events containing these words. Leave out for all."},
                },
                "required": [],
            },
        },
    },
]

HANDLERS = {
    "search_logs": search_logs,
    "error_summary": error_summary,
    "log_trend": log_trend,
}


# --- how results are shown in the browser (core/views.py) --------------------

VIEWS = {
    "search_logs": {
        "type": "table", "title": "Log events, last {window_minutes} min", "rows": "events",
        "columns": [
            {"field": "time", "label": "Time", "format": "datetime"},
            {"field": "level", "label": "Level"},
            {"field": "app", "label": "App"},
            {"field": "message", "label": "Message"},
        ],
    },
    "error_summary": {
        "type": "table", "title": "Top {level} messages, last {window_minutes} min", "rows": "top_messages",
        "columns": [
            {"field": "count", "label": "Count", "format": "int"},
            {"field": "message", "label": "Message"},
        ],
    },
    "log_trend": [
        {"type": "stats", "items": [
            {"label": "Events", "value": "{total}", "format": "int"},
            {"label": "Peak per {bucket}", "value": "{peak}", "format": "int"},
        ]},
        {"type": "chart", "title": "{level} events, last {window_minutes} min", "rows": "_series",
         "x": "time", "y": {"count": "Events"}, "kind": "bar", "x_format": "time"},
    ],
}
