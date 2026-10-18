"""Third-party calls (Nominatim, OSRM, Open-Meteo) and the trip-planning pipeline."""

import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from functools import lru_cache
from urllib.parse import urlparse

import requests

from . import risk

METERS_PER_MILE = 1609.344
OSRM = "https://router.project-osrm.org/route/v1/driving/"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
HEATMAP_HOURS = 48
MAX_HEAT_POINTS = 100
WEATHER_GRID_DEG = 0.1  # ~7 mi: finer than the 10-mile checkpoint spacing
WEATHER_CACHE_S = 30 * 60

http = requests.Session()
http.headers["User-Agent"] = "truck-route-safety/1.0 (coding assignment demo)"


class UpstreamError(Exception):
    """A third-party API failed or returned nothing usable; message is user-facing."""


def _get_json(url, params):
    host = urlparse(url).hostname
    try:
        resp = http.get(url, params=params, timeout=25)
    except requests.RequestException as e:
        raise UpstreamError(f"Could not reach {host}.") from e
    if resp.status_code >= 400:
        try:
            body = resp.json()
            reason = body.get("reason") or body.get("message")
        except ValueError:
            reason = None
        raise UpstreamError(f"{host}: {reason or f'HTTP {resp.status_code}'}")
    return resp.json()


@lru_cache(maxsize=512)
def geocode(query):
    hits = _get_json("https://nominatim.openstreetmap.org/search", {"q": query, "format": "json", "limit": 1})
    if not hits:
        raise UpstreamError(f"Could not find a location for '{query}'.")
    return {"name": hits[0]["display_name"], "lat": float(hits[0]["lat"]), "lon": float(hits[0]["lon"])}


# --- Routing -----------------------------------------------------------------

def _osrm(points, alternatives=False):
    coords = ";".join(f"{p['lon']:.5f},{p['lat']:.5f}" for p in points)
    data = _get_json(OSRM + coords, {
        "alternatives": "3" if alternatives else "false",
        "overview": "full",
        "geometries": "geojson",
        "annotations": "distance,duration",
    })
    if data.get("code") != "Ok":
        raise UpstreamError(f"No drivable route found ({data.get('code')}).")
    routes = []
    for r in data["routes"]:
        # Leg geometries share their join point, so concatenated annotations line up with the overview.
        routes.append({
            "coords": [(lat, lon) for lon, lat in r["geometry"]["coordinates"]],
            "seg_m": [d for leg in r["legs"] for d in leg["annotation"]["distance"]],
            "seg_s": [t for leg in r["legs"] for t in leg["annotation"]["duration"]],
            "duration_s": r["duration"],
        })
    return routes


def _cells(route):
    return {(round(lat, 2), round(lon, 2)) for lat, lon in route["coords"]}


def _is_distinct(candidate, routes):
    c = _cells(candidate)
    return all(len(c & _cells(r)) / len(c | _cells(r)) < 0.85 for r in routes)


def get_routes(origin, dest):
    """Up to 3 distinct routes: OSRM's own alternatives, topped up with via-point detours
    either side of the straight line when OSRM returns fewer."""
    routes = _osrm([origin, dest], alternatives=True)[:3]
    mid_lat, mid_lon = (origin["lat"] + dest["lat"]) / 2, (origin["lon"] + dest["lon"]) / 2
    # Perpendicular to origin->dest in a locally flat (lon scaled by cos lat) frame.
    k = math.cos(math.radians(mid_lat))
    dx, dy = (dest["lon"] - origin["lon"]) * k, dest["lat"] - origin["lat"]
    for frac in (0.12, -0.12, 0.25, -0.25, 0.4, -0.4):
        if len(routes) >= 3:
            break
        via = {"lat": mid_lat + dx * frac, "lon": mid_lon - dy * frac / k}
        try:
            candidate = _osrm([origin, via, dest])[0]
        except UpstreamError:
            continue  # via point not routable (sea, border...), try the next one
        # ponytail: fixed 1.5x detour cap, tune if trucks need wider alternatives
        if candidate["duration_s"] < 1.5 * routes[0]["duration_s"] and _is_distinct(candidate, routes):
            routes.append(candidate)
    return routes


def sample_checkpoints(route, interval_mi):
    """Points every `interval_mi` along the route (plus origin and destination),
    each with miles from origin and seconds from departure."""
    step = interval_mi * METERS_PER_MILE
    coords = route["coords"]
    out = [(coords[0], 0.0, 0.0)]
    dist = elapsed = 0.0
    target = step
    for i, (d, t) in enumerate(zip(route["seg_m"], route["seg_s"])):
        while d > 0 and dist + d >= target:
            f = (target - dist) / d
            (lat1, lon1), (lat2, lon2) = coords[i], coords[i + 1]
            out.append(((lat1 + f * (lat2 - lat1), lon1 + f * (lon2 - lon1)), target, elapsed + f * t))
            target += step
        dist += d
        elapsed += t
    if dist - out[-1][1] > 0.1 * METERS_PER_MILE:
        out.append((coords[-1], dist, elapsed))
    return [{"lat": lat, "lon": lon, "mile": m / METERS_PER_MILE, "offset_s": s} for (lat, lon), m, s in out]


def _thin(coords, max_points=1500):
    """Stride-decimate the display polyline; full geometry is only needed for sampling."""
    stride = max(1, len(coords) // max_points)
    return [[round(lat, 5), round(lon, 5)] for lat, lon in coords[::stride] + [coords[-1]]]


# --- Weather -----------------------------------------------------------------

_weather_cache = {}  # (lat, lon, start, hours) -> (fetched_at, series)


def fetch_hourly(points, start, hours):
    """Hourly wind (mph), rain+showers (in/hr) and snowfall (in/hr) for each (lat, lon),
    from `start` (UTC, on the hour) for `hours`+1 hours.
    Points are snapped to a 0.1 deg grid and deduped, and results cached for 30 min,
    because Open-Meteo bills every location as one call against a per-IP limit."""
    keys = [(round(lat, 1), round(lon, 1), start, hours) for lat, lon in points]
    now = time.monotonic()
    for k in [k for k, (t, _) in _weather_cache.items() if now - t > WEATHER_CACHE_S]:
        del _weather_cache[k]
    missing = sorted({k for k in keys if k not in _weather_cache})

    params = {
        "hourly": "wind_speed_10m,rain,showers,snowfall",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "GMT",
        "start_hour": start.strftime("%Y-%m-%dT%H:%M"),
        "end_hour": (start + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M"),
    }

    def batch(chunk):
        data = _get_json(OPEN_METEO, {
            **params,
            "latitude": ",".join(f"{k[0]:.1f}" for k in chunk),
            "longitude": ",".join(f"{k[1]:.1f}" for k in chunk),
        })
        return data if isinstance(data, list) else [data]

    chunks = [missing[i:i + 100] for i in range(0, len(missing), 100)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [loc for part in pool.map(batch, chunks) for loc in part]
    for k, loc in zip(missing, results):
        h = loc["hourly"]
        # Null = model has no value for that hour; treat as no precipitation / calm.
        _weather_cache[k] = (now, {
            "wind": [w or 0.0 for w in h["wind_speed_10m"]],
            "rain": [(r or 0.0) + (s or 0.0) for r, s in zip(h["rain"], h["showers"])],
            "snow": [s or 0.0 for s in h["snowfall"]],
        })
    return [_weather_cache[k][1] for k in keys]


def corridor_grid(routes):
    """Regular lat/lon lattice cells within one cell of any route: the heatmap corridor."""
    lats = [lat for r in routes for lat, _ in r["coords"]]
    lons = [lon for r in routes for _, lon in r["coords"]]
    cell = max(0.1, max(max(lats) - min(lats), max(lons) - min(lons)) / 25)
    while True:
        cells = set()
        for r in routes:
            for lat, lon in r["coords"][:: max(1, len(r["coords"]) // 400)]:
                ci, cj = round(lat / cell), round(lon / cell)
                cells.update((ci + a, cj + b) for a in (-1, 0, 1) for b in (-1, 0, 1))
        if len(cells) <= MAX_HEAT_POINTS:
            return cell, sorted((i * cell, j * cell) for i, j in cells)
        cell *= 1.2


# --- Pipeline ----------------------------------------------------------------

def plan_trip(origin_q, dest_q, departure, load_lb, interval_mi):
    origin, dest = geocode(origin_q), geocode(dest_q)
    raw_routes = get_routes(origin, dest)

    start = departure.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    per_route = [sample_checkpoints(r, interval_mi) for r in raw_routes]
    trip_hours = math.ceil(max(cps[-1]["offset_s"] for cps in per_route) / 3600) + 1
    cell, heat_points = corridor_grid(raw_routes)
    cp_points = [(c["lat"], c["lon"]) for cps in per_route for c in cps]
    window = 24 * math.ceil(max(trip_hours, HEATMAP_HOURS) / 24)  # whole days, so cache keys repeat
    weather = fetch_hourly(cp_points + heat_points, start, window)
    cp_weather, heat_weather = weather[: len(cp_points)], weather[len(cp_points):]

    routes, w = [], iter(cp_weather)
    for n, (raw, cps) in enumerate(zip(raw_routes, per_route)):
        for c in cps:
            eta = departure + timedelta(seconds=c["offset_s"])
            hour = round((eta - start).total_seconds() / 3600)
            series = next(w)
            c["wind_mph"], c["rain_in_hr"], c["snow_in_hr"] = (series[k][hour] for k in ("wind", "rain", "snow"))
            c["level"], c["reasons"] = risk.classify(c["wind_mph"], c["rain_in_hr"], c["snow_in_hr"], load_lb)
            c["level_name"] = risk.LEVEL_NAMES[c["level"]]
            c["eta"] = eta.isoformat()
            c["mile"] = round(c["mile"], 1)
            c["lat"], c["lon"] = round(c["lat"], 5), round(c["lon"], 5)
            del c["offset_s"]
        routes.append({
            "name": f"Route {'ABC'[n]}",
            "distance_mi": round(sum(raw["seg_m"]) / METERS_PER_MILE, 1),
            "duration_h": round(raw["duration_s"] / 3600, 2),
            "arrival": (departure + timedelta(seconds=raw["duration_s"])).isoformat(),
            "geometry": _thin(raw["coords"]),
            "checkpoints": cps,
            "summary": risk.summarize(cps),
        })

    best, reason = risk.recommend(routes)
    warning = None
    if routes[best]["summary"]["max_level"] == risk.NO_TRAVEL:
        warning = "Every route crosses No Travel conditions. Consider delaying departure."

    def heat(i):  # (overall, wind, rain, snow) indices per hour for heat point i
        s = heat_weather[i]
        return [risk.heat_indices(s["wind"][h], s["rain"][h], s["snow"][h], load_lb) for h in range(HEATMAP_HOURS + 1)]

    heat_rows = [heat(i) for i in range(len(heat_points))]
    layers = {
        name: [[round(hour[k], 2) for hour in row] for row in heat_rows]
        for k, name in enumerate(("overall", "wind", "rain", "snow"))
    }
    return {
        "origin": origin,
        "destination": dest,
        "departure": departure.isoformat(),
        "load_lb": load_lb,
        "interval_mi": interval_mi,
        "recommended": best,
        "reason": reason,
        "warning": warning,
        "routes": routes,
        "heatmap": {
            "start": start.isoformat(),
            "hours": HEATMAP_HOURS,
            "cell_deg": round(cell, 4),
            "points": [[round(lat, 4), round(lon, 4)] for lat, lon in heat_points],
            "layers": layers,
        },
    }
