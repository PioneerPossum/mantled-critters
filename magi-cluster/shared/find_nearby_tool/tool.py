"""
Real find_nearby tool: searches OpenStreetMap (via the public Overpass API)
for places matching a free-text query (brand/category name) near a given
point, picks the nearest by real haversine distance, then reuses the same
OSRM-based routing this package's `directions` tool uses to route there.

No API key required for either Overpass or OSRM. Coverage for the routing
leg is limited to wherever OSRM's data covers (currently Illinois only, see
magi-osrm-routing.md) -- a nearest place found outside that area still gets
reported (name + straight-line distance), just without turn-by-turn.

NOTE (2026-09-18): magi_llm_api.py does NOT call this module's run() in
production -- tool execution for this project is centralized on
repositorium's magi-tools-api.service (~/magi/dashboard/tools_api.py,
run_find_nearby()), the same architecture as the math/weather/directions
tools (see magi-local-llm-feasibility.md Session 12). This file exists so
the vendored tools/{math,weather,directions,find_nearby} tree stays
structurally consistent (name/description/schema/TOOLS_SCHEMA/run all
present) and magi_llm_api.py can still import it for tool metadata (what's
shown to the model) -- and so a real, working standalone implementation
exists here too, not just a stub.

Location handling: lat/lon are NOT part of this tool's schema -- the model
is never asked to supply them (see the description below and
magi-local-llm-feasibility.md's weather-tool location-hallucination lesson,
which is exactly the failure mode not repeated here). In production,
magi_llm_api.py's remote runner for this tool injects the real lat/lon the
dashboard frontend captured via navigator.geolocation for this chat message,
overriding anything the model might otherwise have guessed. This standalone
run() still accepts lat/lon as plain required numeric input for the same
reason -- it's a real implementation, just never the one actually invoked.
"""

from __future__ import annotations

import time
from math import asin, cos, radians, sin, sqrt
from typing import Any

import requests

# --- OSRM (self-hosted, repositorium) -- same instance the directions tool uses ---
OSRM_URL: str = "http://192.168.8.184:5000"

# --- Overpass API (public OSM feature-query service) ---
OVERPASS_URL: str = "https://overpass-api.de/api/interpreter"
OVERPASS_USER_AGENT: str = "magi-cluster-find-nearby-tool/1.0 (Gil's magi-cluster project)"
DEFAULT_RADIUS_M = 15000
WIDE_RADIUS_M = 45000
MAX_RESULTS = 30

name: str = "find_nearby"

display_description: str = (
    "Find the nearest place/business matching a search term (brand or category) and get "
    "driving directions to it, using OpenStreetMap search plus self-hosted OSRM routing."
)

description: str = (
    "Find the nearest place and get directions. 'query' = brand/category name only, e.g. "
    "'Best Buy'. Location is automatic; never ask for it."
)

schema: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "e.g. 'Best Buy'."}},
    "required": ["query"],
}

TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema,
        },
    }
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(a))


def _overpass_escape(text: str) -> str:
    text = text.replace("\\", "").replace('"', "")
    for ch in ";[]{}()":
        text = text.replace(ch, "")
    return text.strip()


def _overpass_search(query: str, lat: float, lon: float, radius_m: int) -> dict[str, Any]:
    escaped = _overpass_escape(query)
    if not escaped:
        return {"ok": False, "error": "empty search query after sanitizing"}
    ql = (
        "[out:json][timeout:25];"
        f'(node(around:{radius_m},{lat},{lon})["name"~"{escaped}",i];'
        f'node(around:{radius_m},{lat},{lon})["brand"~"{escaped}",i];);'
        f"out center {MAX_RESULTS};"
    )
    try:
        r = requests.post(
            OVERPASS_URL, data={"data": ql},
            headers={"User-Agent": OVERPASS_USER_AGENT}, timeout=40,
        )
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"Place search service error: {e}"}
    except ValueError as e:
        return {"ok": False, "error": f"Place search service returned an unexpected response: {e}"}
    if data.get("remark"):
        return {"ok": False, "error": f"Place search service error: {data['remark']}"}
    return {"ok": True, "elements": data.get("elements", [])}


def _geocode(place: str, timeout: int = 8) -> dict[str, Any]:
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": place, "format": "json", "limit": 1},
            headers={"User-Agent": OVERPASS_USER_AGENT},
            timeout=timeout,
        )
        r.raise_for_status()
        results = r.json()
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"Geocoding service error: {e}"}
    if not results:
        return {"ok": False, "error": f"Could not find a location matching '{place}'."}
    top = results[0]
    return {"ok": True, "lat": float(top["lat"]), "lon": float(top["lon"]), "display_name": top.get("display_name", place)}


def _format_duration(minutes: float) -> str:
    if minutes >= 60:
        hours, mins = int(minutes // 60), int(round(minutes % 60))
        return f"{hours} hr {mins} min" if mins else f"{hours} hr"
    return f"{int(round(minutes))} min"


def _drive_route(geo_origin: dict[str, Any], geo_dest: dict[str, Any]) -> dict[str, Any]:
    try:
        coords = f"{geo_origin['lon']},{geo_origin['lat']};{geo_dest['lon']},{geo_dest['lat']}"
        r = requests.get(
            f"{OSRM_URL}/route/v1/driving/{coords}",
            params={"overview": "false", "steps": "true"}, timeout=10,
        )
        r.raise_for_status()
        route_data = r.json()
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"Routing service error: {e}"}

    if route_data.get("code") != "Ok" or not route_data.get("routes"):
        return {"ok": False, "error": "No driving route found -- outside this instance's Illinois-only road coverage."}

    route = route_data["routes"][0]
    distance_km = route["distance"] / 1000.0
    distance_mi = distance_km * 0.621371
    duration_str = _format_duration(route["duration"] / 60.0)
    result = (
        f"Driving from {geo_origin['display_name']} to {geo_dest['display_name']}: "
        f"{distance_mi:.1f} mi ({distance_km:.1f} km), about {duration_str}."
    )
    o = f"{geo_origin['lat']},{geo_origin['lon']}"
    d = f"{geo_dest['lat']},{geo_dest['lon']}"
    return {
        "ok": True, "result": result,
        "apple_maps_url": f"https://maps.apple.com/?saddr={o}&daddr={d}",
        "google_maps_url": f"https://www.google.com/maps/dir/?api=1&origin={o}&destination={d}",
    }


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    query = str(input_data.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "'query' is required."}
    try:
        lat = float(input_data.get("lat"))
        lon = float(input_data.get("lon"))
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": "I don't have your location — try enabling location access, or tell me a location to search near.",
        }

    search = _overpass_search(query, lat, lon, DEFAULT_RADIUS_M)
    if not search.get("ok"):
        return search
    elements = search["elements"]
    used_radius = DEFAULT_RADIUS_M
    if not elements:
        search = _overpass_search(query, lat, lon, WIDE_RADIUS_M)
        if not search.get("ok"):
            return search
        elements = search["elements"]
        used_radius = WIDE_RADIUS_M
    if not elements:
        return {"ok": False, "error": f"No place matching '{query}' found within {used_radius // 1000}km of your location."}

    matches = []
    for el in elements:
        if el.get("type") == "node":
            el_lat, el_lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            el_lat, el_lon = center.get("lat"), center.get("lon")
        if el_lat is None or el_lon is None:
            continue
        place_name = el.get("tags", {}).get("name") or el.get("tags", {}).get("brand") or "(unnamed)"
        dist_km = _haversine_km(lat, lon, el_lat, el_lon)
        matches.append({"name": place_name, "lat": el_lat, "lon": el_lon, "distance_km": round(dist_km, 2)})
    if not matches:
        return {"ok": False, "error": f"Found results for '{query}' but none had usable coordinates."}

    matches.sort(key=lambda m: m["distance_km"])
    nearest = matches[0]
    geo_origin = {"lat": lat, "lon": lon, "display_name": "your current location"}
    geo_dest = {"lat": nearest["lat"], "lon": nearest["lon"], "display_name": nearest["name"]}
    route = _drive_route(geo_origin, geo_dest)
    if not route.get("ok"):
        return {
            "ok": True,
            "result": f"Nearest match for '{query}': {nearest['name']} ({nearest['distance_km']:.1f} km away, straight-line). Could not compute driving directions: {route.get('error')}",
            "chosen_place": nearest,
        }
    return {
        "ok": True,
        "result": f"Nearest {query} match: {nearest['name']}. " + route["result"],
        "chosen_place": nearest,
        "apple_maps_url": route.get("apple_maps_url"),
        "google_maps_url": route.get("google_maps_url"),
    }
