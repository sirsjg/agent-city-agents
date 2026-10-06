# flights — Skye

Tells you about flights arriving at any airport you name (IATA/ICAO code or name), using open data with no API key: OurAirports for airport lookup, adsb.lol for live aircraft and routes, OpenSky Network for recent landings. Live tracking, not airline timetables, so it won't know delays or gates.

| Tool | Does |
|---|---|
| `get_inbound_flights(airport, radius_nm)` | Flights in the air heading to the airport, soonest first: aircraft type, distance, minutes out, and origin. Routes come from adsb.lol, or, if that's down, from adsbdb.com for descending aircraft pointed at the airport. Small, private and some regional flights have no known origin. |
| `get_recent_arrivals(airport, hours)` | Flights that landed in the last few hours, with origin airport (OpenSky history can lag). |

```bash
python3 chat.py flights                 # from the hive root
python3 chat.py flights "What's landing at Heathrow soon?" # one-shot
```
