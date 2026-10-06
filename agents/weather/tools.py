"""Tools for the weather agent.

Each tool has two parts:
  - a schema in OpenAI function-calling format (listed in TOOLS)
  - a Python implementation (registered in HANDLERS, keyed by the same name)

Uses the free Open-Meteo API (https://open-meteo.com) - no API key required.
"""

import json
from datetime import date
import urllib.parse
import urllib.request

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes -> human-readable text
WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "light freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "light freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "light snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with light hail", 99: "thunderstorm with heavy hail",
}


def _get_json(url, params):
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(full_url, timeout=10) as resp:
        return json.load(resp)


def _day_label(iso_date, today):
    delta = (date.fromisoformat(iso_date) - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    return date.fromisoformat(iso_date).strftime("%A")


def get_weather(location, days=2):
    """Look up current conditions and a daily forecast for a named place."""
    # Always fetch at least today + tomorrow so "tomorrow" questions work even
    # if the model asks for days=1 (small models often do).
    days = max(2, min(int(days), 7))

    geo = _get_json(GEOCODE_URL, {"name": location, "count": 1, "language": "en"})
    if not geo.get("results"):
        return {"error": f"Could not find a place called '{location}'."}
    place = geo["results"][0]

    data = _get_json(FORECAST_URL, {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max",
        "forecast_days": days,
        "timezone": "auto",
    })

    cur = data["current"]
    daily = data["daily"]
    today = date.fromisoformat(daily["time"][0])  # first day is the place's local today
    return {
        "location": ", ".join(p for p in (place.get("name"), place.get("admin1"), place.get("country")) if p),
        "local_time": cur["time"],
        "current": {
            "conditions": WMO_CODES.get(cur["weather_code"], "unknown"),
            "temperature_c": cur["temperature_2m"],
            "feels_like_c": cur["apparent_temperature"],
            "humidity_pct": cur["relative_humidity_2m"],
            "precipitation_mm": cur["precipitation"],
            "wind_kmh": cur["wind_speed_10m"],
        },
        "forecast": [
            {
                "day": _day_label(daily["time"][i], today),
                "date": daily["time"][i],
                "conditions": WMO_CODES.get(daily["weather_code"][i], "unknown"),
                "high_c": daily["temperature_2m_max"][i],
                "low_c": daily["temperature_2m_min"][i],
                "rain_chance_pct": daily["precipitation_probability_max"][i],
            }
            for i in range(len(daily["time"]))
        ],
    }


# --- OpenAI function-calling schemas -----------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather conditions and a daily forecast for a city or place anywhere in the world.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City or place name, e.g. 'Dublin' or 'Paris, France'.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "How many days of forecast to return, 2-7 (default 2: today and tomorrow). Only ask for more if the user asks about a later day. Each day is labelled today, tomorrow, or its weekday.",
                        "minimum": 2,
                        "maximum": 7,
                    },
                },
                "required": ["location"],
            },
        },
    },
]

HANDLERS = {
    "get_weather": get_weather,
}


# --- how results are shown in the browser (core/views.py) --------------------

VIEWS = {
    "get_weather": [
        {"type": "stats", "title": "{location}, now: {current.conditions}", "from": "current", "items": [
            {"label": "Temperature", "value": "temperature_c", "unit": "°C"},
            {"label": "Feels like", "value": "feels_like_c", "unit": "°C"},
            {"label": "Humidity", "value": "humidity_pct", "unit": "%"},
            {"label": "Wind", "value": "wind_kmh", "unit": " km/h"},
            {"label": "Rain", "value": "precipitation_mm", "unit": " mm"},
        ]},
        {"type": "table", "title": "Forecast", "rows": "forecast", "columns": [
            {"field": "day", "label": "Day"},
            {"field": "conditions", "label": "Conditions"},
            {"field": "high_c", "label": "High", "unit": "°"},
            {"field": "low_c", "label": "Low", "unit": "°"},
            {"field": "rain_chance_pct", "label": "Rain", "unit": "%"},
        ]},
    ],
}
