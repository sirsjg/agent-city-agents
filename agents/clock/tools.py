"""Tools for the clock agent.

Each tool has two parts:
  - a schema in OpenAI function-calling format (listed in TOOLS)
  - a Python implementation (registered in HANDLERS, keyed by the same name)

Uses Python's built-in timezone database (zoneinfo), so no network calls.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo, available_timezones

# "new york" -> "America/New_York", so a bare city name still works when the
# model forgets to send an IANA name (small models sometimes do).
_CITY_INDEX = {
    tz.rsplit("/", 1)[-1].replace("_", " ").lower(): tz
    for tz in available_timezones()
    if "/" in tz and not tz.startswith("Etc/")
}


def _zone(name):
    name = name.strip()
    if name in available_timezones():
        return ZoneInfo(name)
    tz = _CITY_INDEX.get(name.lower())
    if tz:
        return ZoneInfo(tz)
    raise ValueError(
        f"Unknown timezone '{name}'. Use an IANA name such as 'Europe/London' "
        "or 'America/Los_Angeles'."
    )


def _describe(dt):
    return {
        "timezone": str(dt.tzinfo),
        "time": dt.strftime("%I:%M %p").lstrip("0"),
        "weekday": dt.strftime("%A"),
        "date": dt.date().isoformat(),
        "utc_offset": dt.strftime("%z")[:3] + ":" + dt.strftime("%z")[3:],
        "daylight_saving": bool(dt.dst()),
    }


def get_time(timezone):
    """Current time and date in a timezone."""
    return _describe(datetime.now(_zone(timezone)))


def convert_time(time, from_timezone, to_timezone, on_date=None):
    """Convert a wall-clock time from one timezone to another."""
    src, dst = _zone(from_timezone), _zone(to_timezone)
    day = date.fromisoformat(on_date) if on_date else datetime.now(src).date()
    for fmt in ("%H:%M", "%I:%M %p", "%I%p", "%I %p", "%I:%M%p"):
        try:
            clock = datetime.strptime(time.strip().upper(), fmt).time()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"Couldn't read the time '{time}'. Use 24-hour 'HH:MM', e.g. '15:00'.")
    start = datetime.combine(day, clock, tzinfo=src)
    return {"from": _describe(start), "to": _describe(start.astimezone(dst))}


# --- OpenAI function-calling schemas -----------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current local time, date and weekday in a timezone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone name, e.g. 'Asia/Tokyo' or 'America/New_York'.",
                    },
                },
                "required": ["timezone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_time",
            "description": "Convert a time of day from one timezone to another, handling daylight saving.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {
                        "type": "string",
                        "description": "Time to convert in 24-hour format, e.g. '15:00'.",
                    },
                    "from_timezone": {
                        "type": "string",
                        "description": "IANA timezone the time is in, e.g. 'Australia/Sydney'.",
                    },
                    "to_timezone": {
                        "type": "string",
                        "description": "IANA timezone to convert to, e.g. 'Europe/London'.",
                    },
                    "on_date": {
                        "type": "string",
                        "description": "Optional date as YYYY-MM-DD. Defaults to today in from_timezone.",
                    },
                },
                "required": ["time", "from_timezone", "to_timezone"],
            },
        },
    },
]

HANDLERS = {
    "get_time": get_time,
    "convert_time": convert_time,
}
