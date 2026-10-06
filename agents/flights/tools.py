"""Tools for the flights agent.

Each tool has two parts:
  - a schema in OpenAI function-calling format (listed in TOOLS)
  - a Python implementation (registered in HANDLERS, keyed by the same name)

Open data, no API keys:
  - OurAirports (https://ourairports.com/data/) to turn a code or name into an airport.
  - adsb.lol (https://adsb.lol) for live aircraft positions and their routes.
  - OpenSky Network (https://opensky-network.org) for flights that already landed.
"""

import csv
import io
import json
import math
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
AIRPORTS_CACHE = Path(tempfile.gettempdir()) / "hive-ourairports.csv"
AIRPORTS_MAX_AGE = 30 * 86400
ADSB_POINT_URL = "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}"
ADSB_ROUTES_URL = "https://api.adsb.lol/api/0/routeset"
ADSBDB_CALLSIGN_URL = "https://api.adsbdb.com/v0/callsign/{callsign}"
OPENSKY_ARRIVALS_URL = "https://opensky-network.org/api/flights/arrival"

AIRPORT_RANK = {"large_airport": 0, "medium_airport": 1, "small_airport": 2}
_airports = None


def _fetch(url, data=None, timeout=20):
    headers = {"User-Agent": "hive-flights-agent"}
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_json(url, what, data=None, timeout=20):
    body = _fetch(url, data=data, timeout=timeout)
    try:
        return json.loads(body)
    except ValueError:
        raise RuntimeError(f"{what} returned something unexpected: {body[:120]!r}. Try again shortly.") from None


def _load_airports():
    global _airports
    if _airports is not None:
        return _airports
    if not AIRPORTS_CACHE.exists() or time.time() - AIRPORTS_CACHE.stat().st_mtime > AIRPORTS_MAX_AGE:
        AIRPORTS_CACHE.write_bytes(_fetch(AIRPORTS_URL, timeout=60))
    rows = csv.DictReader(io.StringIO(AIRPORTS_CACHE.read_text(encoding="utf-8")))
    _airports = [r for r in rows if r["type"] in AIRPORT_RANK]
    return _airports


def _find_airport(query):
    """Match an ICAO code, IATA code, or airport/city name to one airport."""
    q = query.strip().lower()
    if not q:
        raise ValueError("Give an airport code (like SYD or YSSY) or a name.")
    airports = _load_airports()
    for field in ("icao_code", "ident", "iata_code"):
        for a in airports:
            if a[field].lower() == q and (field != "iata_code" or a["scheduled_service"] == "yes"):
                return a
    hits = [a for a in airports if a["scheduled_service"] == "yes"
            and (q in a["name"].lower() or q == a["municipality"].lower())]
    if not hits:
        hits = [a for a in airports if q in a["name"].lower() or q in a["municipality"].lower()]
    if not hits:
        raise ValueError(f"Could not find an airport matching '{query}'. Try its IATA or ICAO code.")
    hits.sort(key=lambda a: AIRPORT_RANK[a["type"]])
    return hits[0]


def _airport_info(a):
    return {
        "name": a["name"],
        "city": a["municipality"],
        "country": a["iso_country"],
        "iata": a["iata_code"],
        "icao": a["ident"],
    }


def _distance_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(h))


def _adsbdb_route(callsign):
    """Origin and destination filed for a callsign (adsbdb.com), or None if unknown."""
    try:
        data = _fetch_json(ADSBDB_CALLSIGN_URL.format(callsign=urllib.parse.quote(callsign)), "adsbdb", timeout=6)
    except (RuntimeError, OSError):
        return None
    route = (data.get("response") or {}).get("flightroute") if isinstance(data, dict) else None
    if not isinstance(route, dict) or not route.get("origin") or not route.get("destination"):
        return None
    return route


def get_inbound_flights(airport, radius_nm=200):
    """Flights in the air right now that are heading to an airport, soonest first."""
    radius_nm = max(25, min(int(radius_nm), 250))
    a = _find_airport(airport)
    lat, lon, icao = float(a["latitude_deg"]), float(a["longitude_deg"]), a["ident"]

    url = ADSB_POINT_URL.format(lat=lat, lon=lon, radius=radius_nm)
    aircraft = _fetch_json(url, "The aircraft feed (adsb.lol)").get("ac") or []
    flying = [p for p in aircraft
              if (p.get("flight") or "").strip() and p.get("lat") is not None
              and p.get("lon") is not None and p.get("alt_baro") != "ground"]
    if not flying:
        return {"airport": _airport_info(a), "inbound": [], "note": f"No tracked flights within {radius_nm} nm right now."}

    planes = [{"callsign": p["flight"].strip(), "lat": p["lat"], "lng": p["lon"]} for p in flying]
    # Only the nearest aircraft can be landing soon, and the route service dislikes big batches.
    planes.sort(key=lambda p: _distance_nm(p["lat"], p["lng"], lat, lon))
    route_by_callsign = {}
    try:
        for i in range(0, min(len(planes), 100), 20):
            routes = _fetch_json(ADSB_ROUTES_URL, "The route lookup (adsb.lol)", data={"planes": planes[i:i + 20]}, timeout=8)
            route_by_callsign.update({r.get("callsign"): r for r in routes if isinstance(r, dict)})
    except (RuntimeError, OSError):
        route_by_callsign = None  # no route data: judge by flight path instead

    inbound = []
    for p in flying:
        callsign = p["flight"].strip()
        dist = _distance_nm(p["lat"], p["lon"], lat, lon)
        speed = p.get("gs")
        alt = p["alt_baro"] if isinstance(p.get("alt_baro"), (int, float)) else None
        entry = {
            "flight": callsign,
            "aircraft_type": p.get("t"),
            "distance_nm": round(dist),
            "altitude_ft": alt,
            "speed_kt": round(speed) if speed else None,
            "eta_minutes": round(dist / speed * 60) if speed and speed > 50 else None,
        }
        if route_by_callsign is not None:
            route = route_by_callsign.get(callsign)
            if not route or route.get("plausible") == 0:
                continue
            codes = (route.get("airport_codes") or "").split("-")
            if len(codes) < 2 or codes[-1] != icao:
                continue
            origin = next((x for x in route.get("_airports") or [] if x.get("icao") == codes[0]), {})
            entry["from"] = origin.get("name") or codes[0]
            entry["from_city"] = origin.get("location")
        else:
            # Likely landing: close, low, descending, and pointed at the airport.
            track = p.get("track")
            if track is None or alt is None or dist > 80 or alt > 12000 or (p.get("baro_rate") or 0) > -200:
                continue
            bearing = math.degrees(math.atan2(
                math.sin(math.radians(lon - p["lon"])) * math.cos(math.radians(lat)),
                math.cos(math.radians(p["lat"])) * math.sin(math.radians(lat))
                - math.sin(math.radians(p["lat"])) * math.cos(math.radians(lat)) * math.cos(math.radians(lon - p["lon"]))))
            if abs((track - bearing + 180) % 360 - 180) > 35:
                continue
        inbound.append(entry)

    inbound.sort(key=lambda f: (f["eta_minutes"] is None, f["eta_minutes"] or 0, f["distance_nm"]))
    inbound = inbound[:10]
    if route_by_callsign is None:
        # Look up each likely arrival's route (adsbdb.com); drop any filed for a different destination.
        kept = []
        for entry in inbound:
            route = _adsbdb_route(entry["flight"])
            if route and route["destination"].get("icao_code") not in (None, icao):
                continue
            if route:
                origin = route["origin"]
                entry["from"] = origin.get("name")
                entry["from_city"] = origin.get("municipality")
                entry["from_country"] = origin.get("country_name")
            kept.append(entry)
        inbound = kept
    result = {"airport": _airport_info(a), "searched_radius_nm": radius_nm, "count": len(inbound), "inbound": inbound}
    if route_by_callsign is None:
        result["note"] = ("These are descending aircraft heading for the airport, so say they appear to be landing. "
                          "A flight with no 'from' has no known origin: never guess one.")
    elif not inbound:
        result["note"] = "No flights with a known route to this airport were found within range right now."
    return result


def get_recent_arrivals(airport, hours=6):
    """Flights that already landed at an airport in the last few hours."""
    hours = max(1, min(int(hours), 24))
    a = _find_airport(airport)
    end = int(time.time())
    params = {"airport": a["ident"], "begin": end - hours * 3600, "end": end}
    try:
        flights = json.loads(_fetch(f"{OPENSKY_ARRIVALS_URL}?{urllib.parse.urlencode(params)}"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            flights = []
        else:
            raise RuntimeError(f"OpenSky returned an error ({e.code}). Try again shortly.") from e

    arrivals = []
    for f in sorted(flights, key=lambda f: f["lastSeen"], reverse=True)[:15]:
        arrivals.append({
            "flight": (f.get("callsign") or "").strip() or "unknown",
            "from_icao": f.get("estDepartureAirport"),
            "landed_utc": time.strftime("%H:%M", time.gmtime(f["lastSeen"])),
        })
    result = {"airport": _airport_info(a), "hours": hours, "count": len(flights), "arrivals": arrivals}
    if not arrivals:
        result["note"] = "No landings found. OpenSky history can lag by hours; try get_inbound_flights for live traffic."
    return result


# --- OpenAI function-calling schemas -----------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_inbound_flights",
            "description": "List flights currently in the air that are heading to an airport, soonest to land first, with origin, aircraft type and minutes until arrival. Use this for 'what's coming in' questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "airport": {
                        "type": "string",
                        "description": "Airport code or name, e.g. 'SYD', 'YSSY' or 'Heathrow'.",
                    },
                    "radius_nm": {
                        "type": "integer",
                        "description": "How far out to look, in nautical miles, 25-250 (default 200).",
                        "minimum": 25,
                        "maximum": 250,
                    },
                },
                "required": ["airport"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_arrivals",
            "description": "List flights that already landed at an airport in the last few hours, with where they came from. Use this only when asked what has landed or arrived already.",
            "parameters": {
                "type": "object",
                "properties": {
                    "airport": {
                        "type": "string",
                        "description": "Airport code or name, e.g. 'LHR' or 'Changi'.",
                    },
                    "hours": {
                        "type": "integer",
                        "description": "How many hours back to look, 1-24 (default 6).",
                        "minimum": 1,
                        "maximum": 24,
                    },
                },
                "required": ["airport"],
            },
        },
    },
]

HANDLERS = {
    "get_inbound_flights": get_inbound_flights,
    "get_recent_arrivals": get_recent_arrivals,
}


# --- how results are shown in the browser (core/views.py) --------------------

VIEWS = {
    "get_inbound_flights": {
        "type": "table", "title": "Inbound to {airport.name}", "rows": "inbound",
        "columns": [
            {"field": "flight", "label": "Flight"},
            {"field": "from_city", "label": "From"},
            {"field": "aircraft_type", "label": "Aircraft"},
            {"field": "distance_nm", "label": "Distance", "unit": " nm", "format": "int"},
            {"field": "altitude_ft", "label": "Altitude", "unit": " ft", "format": "int"},
            {"field": "eta_minutes", "label": "ETA", "unit": " min", "format": "int"},
        ],
    },
    "get_recent_arrivals": {
        "type": "table", "title": "Landed at {airport.name}, past {hours} hours", "rows": "arrivals",
        "columns": {"flight": "Flight", "from_icao": "From", "landed_utc": "Landed (UTC)"},
    },
}
