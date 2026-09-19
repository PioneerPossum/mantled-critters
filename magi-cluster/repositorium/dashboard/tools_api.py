#!/usr/bin/env python3
"""magi-tools-api — runs on repositorium only.

Central, single-source-of-truth implementation of the magi-cluster local-LLM
agent's tools (math, weather, directions), exposed as a small internal HTTP
API instead of being vendored separately into each node's ~/hailo-apps
checkout under gen_ai_apps/agent_tools_example/tools/. Reason this exists:
melchior and balthazar's copies of things have silently drifted apart before
(magi_detect.py's headless-display fix, caught in the 2026-09-16 dedup
session) and the weather tool specifically has been patched twice in place on
both nodes' vendored trees (Session 10's location-hallucination fix, Session
11's include_rain silent-omission fix, magi-local-llm-feasibility.md) —
fixes that live outside this repo and would be silently lost on a future
`hailo-apps` git pull/reinstall on either node. Centralizing execution here
means there is exactly one copy of the tool logic to keep correct, and both
nodes' magi_llm_api.py just make an HTTP call instead of importing/running
the logic locally.

Endpoints:
  POST /tools/weather     {"location": "...", "future_days": 0}
      -> {"ok": true, "result": "..."} or {"ok": false, "error": "..."}
  POST /tools/math        {"expression": "..."} or {"default": true}
      -> {"ok": true, "result": "..."} or {"ok": false, "error": "..."}
  POST /tools/directions  {"origin": "...", "destination": "..."}
      -> {"ok": true, "result": "..."} or {"ok": false, "error": "..."}
  GET  /health             -> {"ok": true, "ts": ...}

Same input/output shape as the local tool functions'
`run(input_data) -> dict` that melchior/balthazar's magi_llm_api.py used to
call directly (see hailo-apps' agent_tools_example/tools/{math,weather,directions}/tool.py,
which this file's logic is a faithful, unmodified port of — including the
Session 11 weather fix: precipitation is ALWAYS included, no include_rain
flag, no code path can return a rain-free successful result).

The directions tool (added 2026-09-17, see magi-osrm-routing.md) geocodes
place names via the public Nominatim API, then routes via this box's own
locally-hosted OSRM instance (osrm-backend.service, port 5000 — same box,
so the OSRM leg of this call is a localhost hop, not a LAN round-trip).
Coverage is Illinois-only for now (see that memory doc for how to widen it).

No Postgres, no auth (LAN-only, same access model as the rest of magi-cluster's
internal services) — this box just executes tool logic and returns JSON.
"""
from __future__ import annotations

import ast
import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

import requests
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("magi_tools_api")

app = Flask(__name__)


# ============================================================================
# Math tool — ported verbatim from
# hailo_apps/python/gen_ai_apps/agent_tools_example/tools/math/tool.py
# ============================================================================

class SafeExpressionEvaluator(ast.NodeVisitor):
    """Safe AST visitor that only allows mathematical operations.

    Only permits:
    - Numbers (ast.Constant)
    - Binary operations: +, -, *, /, //, %, **
    - Unary operations: +, -
    """

    def __init__(self):
        self.safe_ops = {
            ast.Add: lambda a, b: a + b,
            ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b,
            ast.Div: lambda a, b: a / b,
            ast.FloorDiv: lambda a, b: a // b,
            ast.Mod: lambda a, b: a % b,
            ast.Pow: lambda a, b: a**b,
        }
        self.safe_unary_ops = {
            ast.UAdd: lambda a: +a,
            ast.USub: lambda a: -a,
        }

    def visit_Constant(self, node: ast.Constant) -> float:
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Invalid constant type: {type(node.value).__name__}")

    def visit_BinOp(self, node: ast.BinOp) -> float:
        if type(node.op) not in self.safe_ops:
            raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")

        left = self.visit(node.left)
        right = self.visit(node.right)

        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ZeroDivisionError("Division by zero")

        return self.safe_ops[type(node.op)](left, right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> float:
        if type(node.op) not in self.safe_unary_ops:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

        operand = self.visit(node.operand)
        return self.safe_unary_ops[type(node.op)](operand)

    def generic_visit(self, node: ast.AST) -> None:
        raise ValueError(f"Unsupported expression component: {type(node).__name__}")


def _safe_evaluate_expression(expression: str) -> float:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression syntax: {e.msg}") from e

    evaluator = SafeExpressionEvaluator()
    try:
        result = evaluator.visit(tree.body)
        return float(result)
    except (ValueError, ZeroDivisionError):
        raise
    except Exception as e:
        raise ValueError(f"Error evaluating expression: {str(e)}") from e


def _validate_math_input(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("default") is True:
        return {"ok": True, "data": {"expression": None}}

    try:
        from pydantic import BaseModel, Field

        class MathInput(BaseModel):
            expression: str = Field(
                description="Mathematical expression to evaluate", min_length=1
            )

        data = MathInput(**payload).model_dump()
        return {"ok": True, "data": data}
    except Exception:
        try:
            expression = str(payload.get("expression", "")).strip()
            if not expression:
                return {
                    "ok": False,
                    "error": "Either 'expression' or 'default' must be provided",
                }
            return {"ok": True, "data": {"expression": expression}}
        except Exception as inner_exc:
            return {"ok": False, "error": f"Validation failed: {inner_exc}"}


def run_math(input_data: dict[str, Any]) -> dict[str, Any]:
    if input_data.get("default") is True:
        return {
            "ok": True,
            "error": "I couldn't translate your question to the supported operations.",
        }

    validated = _validate_math_input(input_data)
    if not validated.get("ok"):
        return validated

    expression = validated["data"]["expression"]

    try:
        result = _safe_evaluate_expression(expression)

        if result == int(result):
            result_str = str(int(result))
        else:
            result_str = f"{result:.10g}"

        formatted_result = f"{expression} = {result_str}"
        return {"ok": True, "result": formatted_result}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except ZeroDivisionError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"Unexpected error: {str(e)}"}


# ============================================================================
# Weather tool — ported verbatim from
# hailo_apps/python/gen_ai_apps/agent_tools_example/tools/weather/{tool.py,api.py}
# INCLUDING the Session 11 (2026-09-17) fix: precipitation is ALWAYS included,
# no include_rain flag exists anywhere in this code path.
# ============================================================================

COUNTRY_ABBREVIATIONS = {
    "UK": "United Kingdom",
    "USA": "United States",
    "US": "United States",
    "UAE": "United Arab Emirates",
}

_PLACEHOLDER_LOCATIONS = {
    "here", "your location", "your current location", "current location",
    "unknown", "n/a", "none", "not specified", "not provided", "unspecified",
    "my location", "wherever i am", "this location", "the user's location",
}

TEMPERATURE_UNIT = "celsius"


def format_time_for_tts(iso_time_str: str) -> str:
    try:
        time_str_clean = iso_time_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(time_str_clean)
        hour = dt.hour
        minute = dt.minute

        if hour == 0:
            hour_12 = 12
            period = "AM"
        elif hour < 12:
            hour_12 = hour
            period = "AM"
        elif hour == 12:
            hour_12 = 12
            period = "PM"
        else:
            hour_12 = hour - 12
            period = "PM"

        if minute == 0:
            return f"{hour_12} {period}"
        return f"{hour_12}:{minute:02d} {period}"
    except (ValueError, AttributeError):
        logger.warning("Failed to parse time string: %s", iso_time_str)
        return iso_time_str


def format_date_for_tts(iso_date_str: str, future_days: int = 0) -> str:
    try:
        date_obj = datetime.fromisoformat(iso_date_str).date()
        today = date.today()

        if future_days == 1:
            return "tomorrow"
        elif future_days == 0 and date_obj == today:
            return "today"
        elif future_days == 2:
            return "the day after tomorrow"

        month_names = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ]
        month = month_names[date_obj.month - 1]
        day = date_obj.day

        if 10 <= day % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

        return f"{month} {day}{suffix}"
    except (ValueError, AttributeError):
        return iso_date_str


def normalize_location(location: str) -> str:
    if "," in location:
        parts = [part.strip() for part in location.split(",")]
        if len(parts) >= 2 and parts[-1] in COUNTRY_ABBREVIATIONS:
            parts[-1] = COUNTRY_ABBREVIATIONS[parts[-1]]
            return ", ".join(parts)
    return location


def get_weather_forecast(
    location: str,
    future_days: int = 0,
    unit: str = "celsius",
    timeout: int = 5,
) -> str:
    """Get weather forecast for a location, always including precipitation data.

    Precipitation (rain totals + probability) is always fetched and included
    in the result -- there is no flag to omit it (Session 11 fix, preserved
    exactly here). There is no code path that can produce a rain-free
    successful result.
    """
    try:
        logger.debug("get_weather_forecast(): location=%s, future_days=%s", location, future_days)
        start_time = time.time()

        normalized_location = normalize_location(location)

        geocoding_url = "https://geocoding-api.open-meteo.com/v1/search"
        geocoding_params: dict[str, Any] = {
            "name": normalized_location,
            "count": 1,
            "language": "en",
            "format": "json",
        }

        geo_response = requests.get(geocoding_url, params=geocoding_params, timeout=timeout)
        geo_response.raise_for_status()
        geo_data = geo_response.json()

        if not geo_data.get("results"):
            return f"Error: Location '{location}' not found."

        first_result = geo_data["results"][0]
        latitude = first_result["latitude"]
        longitude = first_result["longitude"]
        location_name = first_result["name"]
        timezone = first_result.get("timezone", "GMT")

        if "country" in first_result:
            location_name = f"{location_name}, {first_result['country']}"

        today = date.today()
        future_days = max(0, int(future_days))
        if future_days == 0:
            forecast_date = None
            is_today = True
        else:
            forecast_date_obj = today + timedelta(days=future_days)
            forecast_date = forecast_date_obj.isoformat()
            is_today = False

        weather_url = "https://api.open-meteo.com/v1/forecast"
        temp_unit = "celsius" if unit.lower() == "celsius" else "fahrenheit"

        weather_params: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
            "temperature_unit": temp_unit,
            "timezone": timezone,
        }

        if is_today:
            weather_params["current_weather"] = "true"
            weather_params["daily"] = "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max"
            weather_params["forecast_days"] = 1
        else:
            weather_params["daily"] = "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max"
            weather_params["start_date"] = forecast_date
            weather_params["end_date"] = forecast_date

        weather_response = requests.get(weather_url, params=weather_params, timeout=timeout)
        weather_response.raise_for_status()
        weather_data = weather_response.json()

        result_parts: list[str] = []

        if is_today:
            if "current_weather" in weather_data:
                current = weather_data["current_weather"]
                temp = current["temperature"]
                time_str = current["time"]
                formatted_time = format_time_for_tts(time_str)
                result_parts.append(
                    f"Current conditions in {location_name}: {temp:.1f}°{unit[0].upper()} "
                    f"as of {formatted_time}."
                )

            if "daily" in weather_data:
                daily = weather_data["daily"]
                if daily.get("time") and len(daily["time"]) > 0:
                    max_temp = daily["temperature_2m_max"][0]
                    min_temp = daily["temperature_2m_min"][0]
                    precip_sum = daily.get("precipitation_sum", [0])[0] or 0
                    precip_prob = daily.get("precipitation_probability_max", [0])[0] or 0

                    result_parts.append(
                        f"Today's forecast: high {max_temp:.1f}°{unit[0].upper()}, "
                        f"low {min_temp:.1f}°{unit[0].upper()}."
                    )
                    result_parts.append(
                        f"Precipitation today: {precip_sum:.1f}mm expected ({precip_prob:.0f}% chance)."
                    )
        else:
            if "daily" in weather_data:
                daily = weather_data["daily"]
                if daily.get("time") and len(daily["time"]) > 0:
                    max_temp = daily["temperature_2m_max"][0]
                    min_temp = daily["temperature_2m_min"][0]
                    formatted_date = format_date_for_tts(forecast_date, future_days)

                    result_parts.append(f"Forecast for {location_name} on {formatted_date}:")
                    result_parts.append(
                        f"High {max_temp:.1f}°{unit[0].upper()}, low {min_temp:.1f}°{unit[0].upper()}."
                    )

                    precip_sum = daily.get("precipitation_sum", [0])[0] or 0
                    precip_prob = daily.get("precipitation_probability_max", [0])[0] or 0

                    if precip_sum > 0 or precip_prob > 0:
                        result_parts.append(
                            f"Precipitation on {formatted_date}: {precip_sum:.1f}mm expected ({precip_prob:.0f}% chance)."
                        )
                    else:
                        result_parts.append(f"No rain expected on {formatted_date}.")

        result = " ".join(result_parts)
        logger.debug("get_weather_forecast() completed in %.2fs", time.time() - start_time)
        return result

    except requests.exceptions.Timeout:
        return f"Error: Request timeout while fetching weather data for {location}"
    except requests.exceptions.RequestException as e:
        return f"Error fetching weather data: {str(e)}"
    except (KeyError, ValueError, IndexError) as e:
        return f"Error parsing weather data: {str(e)}"


def _is_placeholder_location(location: str) -> bool:
    normalized = location.strip().strip(".!").lower()
    return normalized in _PLACEHOLDER_LOCATIONS


def _missing_location_error() -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            "'location' is required and was empty, missing, or a placeholder — there is no "
            "default location. Do not guess or invent a city. Ask the user which location they "
            "mean, then call this tool again once they've told you."
        ),
    }


def _validate_weather_input(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from pydantic import BaseModel, Field

        class WeatherInput(BaseModel):
            location: str = Field(description="Location name")
            future_days: int = Field(
                default=0,
                description="Days in future for forecast (0=today, 1=tomorrow)",
                ge=0,
            )

        data = WeatherInput(**payload).model_dump()
        future_days = int(data.get("future_days", 0))
        if future_days < 0:
            future_days = 0
        data["future_days"] = future_days
        if not data.get("location", "").strip() or _is_placeholder_location(data["location"]):
            return _missing_location_error()
        return {"ok": True, "data": data}
    except Exception:
        location = str(payload.get("location", "")).strip()
        future_days = payload.get("future_days", 0)
        try:
            future_days = int(future_days)
            if future_days < 0:
                future_days = 0
        except (ValueError, TypeError):
            future_days = 0
        if not location or _is_placeholder_location(location):
            return _missing_location_error()
        return {
            "ok": True,
            "data": {
                "location": location,
                "future_days": future_days,
            },
        }


def run_weather(input_data: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_weather_input(input_data)
    if not validated.get("ok"):
        return validated

    data = validated["data"]
    location = data["location"]
    future_days = data.get("future_days", 0)

    try:
        result = get_weather_forecast(
            location=location,
            future_days=future_days,
            unit=TEMPERATURE_UNIT,
        )

        if result.startswith("Error:"):
            return {"ok": False, "error": result}

        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": f"Failed to fetch weather data: {str(e)}"}


# ============================================================================
# Directions tool — ported verbatim from
# hailo_apps/python/gen_ai_apps/agent_tools_example/tools/directions/tool.py
# (added 2026-09-17, see magi-osrm-routing.md). Geocodes via Nominatim,
# routes via this box's own locally-hosted OSRM instance.
# ============================================================================

OSRM_URL = "http://192.168.8.184:5000"  # osrm-backend.service, same box as this API (bound to
                                          # the LAN IP specifically, not loopback -- see the
                                          # docker -p flag in osrm-backend.service)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "magi-cluster-directions-tool/1.0 (Gil's magi-cluster project)"
_last_nominatim_call = {"t": 0.0}
_nominatim_lock = threading.Lock()  # Flask runs threaded=True; rate-limit must be process-wide

_DIRECTIONS_PLACEHOLDERS = {
    "here", "your location", "your current location", "current location",
    "unknown", "n/a", "none", "not specified", "not provided", "unspecified",
    "my location", "wherever i am", "this location", "the user's location",
}


def _is_placeholder_location(location: str) -> bool:
    return location.strip().strip(".!").lower() in _DIRECTIONS_PLACEHOLDERS


def _missing_field_error(field: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            f"'{field}' is required and was empty, missing, or a placeholder — there is no "
            "default location. Do not guess or invent a place. Ask the user for it, then call "
            "this tool again once they've told you."
        ),
    }


def _validate_directions_input(payload: dict[str, Any]) -> dict[str, Any]:
    origin = str(payload.get("origin", "")).strip()
    destination = str(payload.get("destination", "")).strip()

    if not origin or _is_placeholder_location(origin):
        return _missing_field_error("origin")
    if not destination or _is_placeholder_location(destination):
        return _missing_field_error("destination")

    return {"ok": True, "data": {"origin": origin, "destination": destination}}


def _geocode(place: str, timeout: int = 8) -> dict[str, Any]:
    """Nominatim usage policy: max 1 req/sec, real User-Agent header required.
    Process-wide lock+timestamp enforces the rate limit even across
    concurrent /tools/directions requests."""
    with _nominatim_lock:
        elapsed = time.time() - _last_nominatim_call["t"]
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        try:
            r = requests.get(
                NOMINATIM_URL,
                params={"q": place, "format": "json", "limit": 1},
                headers={"User-Agent": NOMINATIM_USER_AGENT},
                timeout=timeout,
            )
            r.raise_for_status()
            results = r.json()
        finally:
            _last_nominatim_call["t"] = time.time()

    if not results:
        return {"ok": False, "error": f"Could not find a location matching '{place}'."}
    top = results[0]
    return {
        "ok": True,
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "display_name": top.get("display_name", place),
    }


def _format_duration(minutes: float) -> str:
    if minutes >= 60:
        hours = int(minutes // 60)
        mins = int(round(minutes % 60))
        return f"{hours} hr {mins} min" if mins else f"{hours} hr"
    return f"{int(round(minutes))} min"


def _build_maps_deep_links(geo_origin: dict[str, Any], geo_dest: dict[str, Any]) -> dict[str, str]:
    """'Open in Maps' links (added 2026-09-17, see magi-osrm-routing.md) —
    reuses the same Nominatim-geocoded lat/lon this tool already computed
    for OSRM routing, rather than re-geocoding on the client. Both are
    public URL schemes, no API key/account needed:
      - Apple Maps: https://maps.apple.com/?saddr=...&daddr=...
      - Google Maps: https://www.google.com/maps/dir/?api=1&origin=...&destination=...
    lat,lon strings need no URL-encoding (digits, '.', '-', ',' are all
    safe in a query string), so this is plain string formatting.
    """
    o = f"{geo_origin['lat']},{geo_origin['lon']}"
    d = f"{geo_dest['lat']},{geo_dest['lon']}"
    return {
        "apple_maps_url": f"https://maps.apple.com/?saddr={o}&daddr={d}",
        "google_maps_url": f"https://www.google.com/maps/dir/?api=1&origin={o}&destination={d}",
    }


def run_directions(input_data: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_directions_input(input_data)
    if not validated.get("ok"):
        return validated

    data = validated["data"]
    origin = data["origin"]
    destination = data["destination"]

    try:
        geo_origin = _geocode(origin)
        if not geo_origin.get("ok"):
            return {"ok": False, "error": f"Origin: {geo_origin.get('error')}"}

        geo_dest = _geocode(destination)
        if not geo_dest.get("ok"):
            return {"ok": False, "error": f"Destination: {geo_dest.get('error')}"}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"Geocoding service error: {e}"}

    return _drive_route_result(geo_origin, geo_dest)


def _drive_route_result(geo_origin: dict[str, Any], geo_dest: dict[str, Any]) -> dict[str, Any]:
    """Shared OSRM-driving-route-plus-maps-links helper, factored out of
    run_directions() (added 2026-09-18) so run_find_nearby() below can reuse
    the exact same routing/turn-extraction/maps-link logic once it has
    already picked the nearest matching place, instead of duplicating it.
    Both callers pass a dict with at least {'lat', 'lon', 'display_name'} for
    each end -- run_directions() gets these from _geocode(), run_find_nearby()
    builds geo_origin from the caller's own lat/lon directly (no geocoding
    needed, it's already coordinates) and geo_dest from the chosen Overpass
    match.
    """
    try:
        coords = f"{geo_origin['lon']},{geo_origin['lat']};{geo_dest['lon']},{geo_dest['lat']}"
        r = requests.get(
            f"{OSRM_URL}/route/v1/driving/{coords}",
            params={"overview": "false", "steps": "true"},
            timeout=10,
        )
        r.raise_for_status()
        route_data = r.json()
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"Routing service error: {e}"}

    if route_data.get("code") != "Ok" or not route_data.get("routes"):
        return {
            "ok": False,
            "error": (
                f"No driving route found between '{geo_origin['display_name']}' and "
                f"'{geo_dest['display_name']}' — this instance currently only has road data "
                "for Illinois."
            ),
        }

    route = route_data["routes"][0]
    distance_km = route["distance"] / 1000.0
    distance_mi = distance_km * 0.621371
    duration_str = _format_duration(route["duration"] / 60.0)

    result_parts = [
        f"Driving from {geo_origin['display_name']} to {geo_dest['display_name']}: "
        f"{distance_mi:.1f} mi ({distance_km:.1f} km), about {duration_str}."
    ]

    steps = [s for leg in route.get("legs", []) for s in leg.get("steps", [])]
    turns = []
    for step in steps:
        maneuver = step.get("maneuver", {})
        instr_type = maneuver.get("type", "")
        if instr_type in ("depart", "arrive"):
            continue
        modifier = maneuver.get("modifier", "")
        road_name = step.get("name", "")
        desc = instr_type + (f" {modifier}" if modifier else "")
        if road_name:
            desc += f" onto {road_name}"
        turns.append(desc)
    if turns:
        shown = turns[:3]
        suffix = "..." if len(turns) > 3 else "."
        result_parts.append("Key turns: " + "; ".join(shown) + suffix)

    maps_links = _build_maps_deep_links(geo_origin, geo_dest)
    return {
        "ok": True,
        "result": " ".join(result_parts),
        "distance_mi": round(distance_mi, 2),
        "duration_str": duration_str,
        **maps_links,
    }


# ============================================================================
# find_nearby tool (added 2026-09-18) — "find me directions to the nearest
# Best Buy" as a single tool call: search OpenStreetMap for places matching a
# free-text query (brand/category name) near a given point via the public
# Overpass API, pick the nearest by real haversine distance, then reuse the
# exact same OSRM routing + maps-deep-link logic the `directions` tool uses
# (_drive_route_result() above) to route to it.
#
# Location handling: this function takes lat/lon as plain required numeric
# input -- it does NOT try to resolve "where is the user" itself, and the
# tool's schema exposed to the model (see the vendored tools/find_nearby
# module on melchior/balthazar) does not ask the model to supply lat/lon at
# all. Per magi-local-llm-feasibility.md's weather-tool location-hallucination
# lesson (Session 10), a small model asked to fill in coordinates will
# sometimes invent plausible-looking ones -- so magi_llm_api.py's runner for
# this tool always overwrites whatever the model put for lat/lon with the
# real coordinates the dashboard frontend captured via
# navigator.geolocation.getCurrentPosition() and sent alongside the chat
# request; see that file's _find_nearby_runner(). If the frontend never
# captured a location (denied/unavailable), that runner returns a clear
# "I don't have your location" error without ever calling this endpoint at
# all -- there is no path from here that silently guesses a location.
#
# Overpass reliability notes (learned by hand this session, real curl
# testing against the public overpass-api.de instance before writing any of
# this): the public instance is genuinely slow/fragile under any real load
# -- single-clause node queries by name/brand regex reliably return in
# ~15-25s, but stacking on a third broad tag-value clause (e.g. amenity/shop
# regex, tried for broader category words like "coffee") measurably
# increased the odds of a full request timeout/504, and repeated rapid
# requests during testing triggered outright 504s from the server being
# "too busy." So this deliberately stays to exactly two OR'd clauses
# (name~query OR brand~query, both case-insensitive substring regex,
# node-only -- no `way` search, which also measurably slowed the very first
# test query down) rather than trying to build a fuller category-keyword
# taxonomy. This handles brand-name queries well ("Best Buy") and handles
# category words reasonably when they literally appear in a place's OSM
# name (e.g. "CVS Pharmacy", "Walgreens Pharmacy" both match query
# "pharmacy") -- it will not find, say, an unnamed-in-that-way "coffee shop"
# whose OSM name is just "Starbucks". A real, known scope limit, not a bug.
# ============================================================================

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_USER_AGENT = "magi-cluster-find-nearby-tool/1.0 (Gil's magi-cluster project)"
FIND_NEARBY_DEFAULT_RADIUS_M = 15000  # 15km -- sensible default, not something the model has to specify
FIND_NEARBY_WIDE_RADIUS_M = 45000     # one-time expansion retry if the default radius finds nothing
FIND_NEARBY_MAX_RESULTS = 30
# Overpass's own internal query timeout, in seconds -- kept comfortably under
# the outer requests.post() timeout below so a real "no matches" or
# malformed-query response has time to come back before our own client gives
# up; observed real single-clause queries taking up to ~25s against the
# public instance.
OVERPASS_QUERY_TIMEOUT_S = 25
OVERPASS_HTTP_TIMEOUT_S = 40


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(a))  # Earth mean radius, km


def _overpass_escape(text: str) -> str:
    """Escape a free-text query for safe embedding inside an Overpass QL
    string-literal regex (double-quoted). Also strips characters that have
    no business being in a place name/category search and could otherwise
    let user/model-controlled text break out of the intended regex literal
    or change the query's structure (semicolons, brackets, parens)."""
    text = text.replace("\\", "").replace('"', "")
    for ch in ";[]{}()":
        text = text.replace(ch, "")
    return text.strip()


OVERPASS_MAX_ATTEMPTS = 2  # one retry on a transient timeout/overload, not an unbounded loop
OVERPASS_RETRY_BACKOFF_S = 2


def _overpass_search_once(ql: str) -> dict[str, Any]:
    """Single Overpass HTTP round-trip. Returns {"ok": True, "elements": [...]},
    or {"ok": False, "error": ..., "transient": bool} -- "transient" marks
    failures worth retrying (timeout / overload) vs. ones that won't improve
    on retry (malformed query, non-timeout remark, etc.)."""
    try:
        r = requests.post(
            OVERPASS_URL,
            data={"data": ql},
            headers={"User-Agent": OVERPASS_USER_AGENT},
            timeout=OVERPASS_HTTP_TIMEOUT_S,
        )
    except requests.exceptions.Timeout:
        return {"ok": False, "transient": True, "error": "Place search timed out (Overpass API is a shared public service and can be slow) — try again in a moment."}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "transient": True, "error": f"Place search service error: {e}"}

    if r.status_code == 504 or "too busy" in r.text.lower():
        return {"ok": False, "transient": True, "error": "Place search service is currently overloaded (public Overpass API) — try again in a moment."}
    try:
        r.raise_for_status()
        data = r.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        return {"ok": False, "transient": True, "error": f"Place search service returned an unexpected response: {e}"}

    if data.get("remark"):
        # Overpass reports its own errors (e.g. query timeout) inside a 200
        # response body via "remark" rather than a non-2xx status -- this is
        # the exact case observed failing intermittently in real use, so it
        # counts as transient/retryable too.
        return {"ok": False, "transient": True, "error": f"Place search service error: {data['remark']}"}

    return {"ok": True, "elements": data.get("elements", [])}


def _overpass_search(query: str, lat: float, lon: float, radius_m: int) -> dict[str, Any]:
    """Nodes within radius_m of (lat, lon) whose name OR brand tag matches
    `query` (case-insensitive substring), via the public Overpass API.
    Retries once on a transient failure (timeout/overload) before giving up
    -- the public instance intermittently times out under its own load even
    for perfectly normal queries (observed in real use), and a single retry
    resolves most of these without meaningfully hurting latency on the
    common (first-try-succeeds) case."""
    escaped = _overpass_escape(query)
    if not escaped:
        return {"ok": False, "error": "empty search query after sanitizing"}

    ql = (
        f'[out:json][timeout:{OVERPASS_QUERY_TIMEOUT_S}];'
        f'(node(around:{radius_m},{lat},{lon})["name"~"{escaped}",i];'
        f'node(around:{radius_m},{lat},{lon})["brand"~"{escaped}",i];);'
        f'out center {FIND_NEARBY_MAX_RESULTS};'
    )

    result = None
    for attempt in range(1, OVERPASS_MAX_ATTEMPTS + 1):
        result = _overpass_search_once(ql)
        if result.get("ok") or not result.get("transient") or attempt == OVERPASS_MAX_ATTEMPTS:
            break
        time.sleep(OVERPASS_RETRY_BACKOFF_S)

    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "Place search failed.")}
    return result


def _validate_find_nearby_input(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "'query' (what to search for, e.g. 'Best Buy', 'coffee', 'pharmacy') is required."}

    try:
        lat = float(payload.get("lat"))
        lon = float(payload.get("lon"))
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": (
                "I don't have your location — try enabling location access, or tell me a "
                "location to search near."
            ),
        }

    return {"ok": True, "data": {"query": query, "lat": lat, "lon": lon}}


def run_find_nearby(input_data: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_find_nearby_input(input_data)
    if not validated.get("ok"):
        return validated
    data = validated["data"]
    query, lat, lon = data["query"], data["lat"], data["lon"]

    search = _overpass_search(query, lat, lon, FIND_NEARBY_DEFAULT_RADIUS_M)
    if not search.get("ok"):
        return search
    elements = search["elements"]
    used_radius_m = FIND_NEARBY_DEFAULT_RADIUS_M

    if not elements:
        # One-time wider retry, per the task's "don't overcomplicate this"
        # guidance -- not an unbounded expansion loop.
        search = _overpass_search(query, lat, lon, FIND_NEARBY_WIDE_RADIUS_M)
        if not search.get("ok"):
            return search
        elements = search["elements"]
        used_radius_m = FIND_NEARBY_WIDE_RADIUS_M

    if not elements:
        return {
            "ok": False,
            "error": (
                f"No place matching '{query}' found within {used_radius_m // 1000}km of your "
                "location. Try a different search term, or a larger area."
            ),
        }

    matches = []
    for el in elements:
        if el.get("type") == "node":
            el_lat, el_lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            el_lat, el_lon = center.get("lat"), center.get("lon")
        if el_lat is None or el_lon is None:
            continue
        name = el.get("tags", {}).get("name") or el.get("tags", {}).get("brand") or "(unnamed)"
        dist_km = _haversine_km(lat, lon, el_lat, el_lon)
        matches.append({
            "name": name, "lat": el_lat, "lon": el_lon,
            "distance_km": round(dist_km, 2), "distance_mi": round(dist_km * 0.621371, 2),
        })

    if not matches:
        return {
            "ok": False,
            "error": f"Found results for '{query}' but none had usable coordinates. Try again.",
        }

    matches.sort(key=lambda m: m["distance_km"])
    nearest = matches[0]

    geo_origin = {"lat": lat, "lon": lon, "display_name": "your current location"}
    geo_dest = {"lat": nearest["lat"], "lon": nearest["lon"], "display_name": nearest["name"]}
    route = _drive_route_result(geo_origin, geo_dest)

    if not route.get("ok"):
        # Found a real nearest place, but couldn't route to it (e.g. outside
        # OSRM's Illinois-only road coverage) -- still tell the user what was
        # found rather than a bare routing error.
        return {
            "ok": True,
            "result": (
                f"Nearest match for '{query}': {nearest['name']} "
                f"({nearest['distance_mi']:.1f} mi / {nearest['distance_km']:.1f} km away, straight-line). "
                f"Could not compute driving directions: {route.get('error')}"
            ),
            "chosen_place": nearest,
            "candidates_considered": len(matches),
        }

    result_text = f"Nearest {query} match: {nearest['name']}. " + route["result"]
    return {
        "ok": True,
        "result": result_text,
        "chosen_place": nearest,
        "candidates_considered": len(matches),
        "apple_maps_url": route.get("apple_maps_url"),
        "google_maps_url": route.get("google_maps_url"),
    }


# ============================================================================
# Live navigation routing (added 2026-09-17) — NOT part of the LLM
# tool-calling system above. This is the backend for the dashboard's
# separate "Live Navigation" UI panel (static/index.html), which polls
# POST /nav/route periodically with the browser's live geolocation position
# (opt-in, started only by the user clicking "Start navigation") plus a
# destination, and re-renders ETA/distance/next-turn text as the position
# updates. Stateless: this box does not store position history anywhere
# (no Postgres, no Redis, no on-disk log) — each request is computed fresh
# from whatever lat/lon the client just sent, which is also how re-routing
# works: the client always asks "route from where I am RIGHT NOW to the
# destination," so a driver who deviates from the previous route simply gets
# a new one on the next poll, no separate polyline-distance-threshold logic
# needed server-side (the client does a lightweight local check of its own
# just to label an update as a meaningful reroute in the UI, but that never
# gates whether this endpoint gets called with fresh data).
# ============================================================================

def compute_route(lat: float, lon: float, dest_lat: float, dest_lon: float, timeout: int = 10) -> dict[str, Any]:
    """Single seam for turn-by-turn route + ETA computation, used by both
    /nav/route below. **TRAFFIC-DATA EXTENSION POINT**: this function
    currently always calls this box's self-hosted OSRM instance (same one
    the `directions` LLM tool uses), which has NO live traffic data —
    routing is static road-network + speed-limit based only, per
    magi-osrm-routing.md. Real traffic-aware routing is a deliberately
    deferred future step: Gil will set up a commercial routing API account
    himself later, and no speculative provider integration/API shape is
    guessed at here since no such account/credentials exist yet. When that
    happens, the intended seam is: swap this function's body (or branch on
    a provider config flag/env var) to call that provider instead, keeping
    the same return shape ({"ok", "distance_mi", "distance_km",
    "duration_min", "duration_str", "eta_utc", "next_instruction",
    "geometry", "provider"}). Every caller — the /nav/route endpoint below,
    and the dashboard frontend's Live Navigation panel — only depends on
    that return shape, not on OSRM specifically, so a provider swap should
    not require touching the endpoint or the frontend at all.
    """
    try:
        coords = f"{lon},{lat};{dest_lon},{dest_lat}"
        r = requests.get(
            f"{OSRM_URL}/route/v1/driving/{coords}",
            params={"overview": "full", "geometries": "geojson", "steps": "true"},
            timeout=timeout,
        )
        r.raise_for_status()
        route_data = r.json()
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"Routing service error: {e}"}

    if route_data.get("code") != "Ok" or not route_data.get("routes"):
        return {
            "ok": False,
            "error": (
                "No driving route found from the current position to the destination — "
                "outside OSRM's Illinois-only coverage, or too far from any road to snap to."
            ),
        }

    route = route_data["routes"][0]
    distance_km = route["distance"] / 1000.0
    distance_mi = distance_km * 0.621371
    duration_min = route["duration"] / 60.0

    steps = [s for leg in route.get("legs", []) for s in leg.get("steps", [])]
    next_instruction = None
    for step in steps:
        maneuver = step.get("maneuver", {})
        instr_type = maneuver.get("type", "")
        if instr_type == "depart":
            continue
        if instr_type == "arrive":
            next_instruction = "Arrive at destination"
            break
        modifier = maneuver.get("modifier", "")
        road_name = step.get("name", "")
        desc = instr_type + (f" {modifier}" if modifier else "")
        if road_name:
            desc += f" onto {road_name}"
        next_instruction = desc
        break

    # Decimate the geometry before shipping it to the client -- OSRM can
    # return hundreds of vertices for a long route, but the client only
    # needs enough points for a rough "am I still near this route" check,
    # not a pixel-perfect polyline (no map widget renders this, per the
    # task's v1 text-based-updates scope).
    raw_geometry = route.get("geometry", {}).get("coordinates", [])  # [[lon,lat], ...]
    if len(raw_geometry) > 100:
        step_n = max(1, len(raw_geometry) // 100)
        raw_geometry = raw_geometry[::step_n]
    geometry_latlon = [[pt[1], pt[0]] for pt in raw_geometry]

    eta_utc = (datetime.utcnow() + timedelta(minutes=duration_min)).isoformat() + "Z"

    return {
        "ok": True,
        "provider": "osrm",  # see the extension-point docstring above
        "distance_mi": round(distance_mi, 2),
        "distance_km": round(distance_km, 2),
        "duration_min": round(duration_min, 1),
        "duration_str": _format_duration(duration_min),
        "eta_utc": eta_utc,
        "next_instruction": next_instruction,
        "geometry": geometry_latlon,
    }


def _validate_nav_route_input(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        lat = float(payload.get("lat"))
        lon = float(payload.get("lon"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "'lat' and 'lon' are required and must be numeric."}

    dest_lat, dest_lon = payload.get("dest_lat"), payload.get("dest_lon")
    if dest_lat is not None and dest_lon is not None:
        try:
            return {"ok": True, "data": {
                "lat": lat, "lon": lon,
                "dest_lat": float(dest_lat), "dest_lon": float(dest_lon),
                "destination_text": None,
            }}
        except (TypeError, ValueError):
            return {"ok": False, "error": "'dest_lat'/'dest_lon' must be numeric."}

    destination = str(payload.get("destination", "")).strip()
    if not destination or _is_placeholder_location(destination):
        return {
            "ok": False,
            "error": "'destination' (text) or 'dest_lat'/'dest_lon' (numeric) is required.",
        }
    return {"ok": True, "data": {
        "lat": lat, "lon": lon, "dest_lat": None, "dest_lon": None,
        "destination_text": destination,
    }}


def run_nav_route(input_data: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_nav_route_input(input_data)
    if not validated.get("ok"):
        return validated
    data = validated["data"]

    dest_lat, dest_lon, dest_label = data["dest_lat"], data["dest_lon"], None
    if dest_lat is None:
        # First call for a given navigation session (client has no cached
        # dest_lat/dest_lon yet) -- geocode once via the same rate-limited
        # Nominatim helper the `directions` tool uses, then the client
        # caches dest_lat/dest_lon itself and sends those directly on every
        # subsequent poll so we never re-geocode the same destination on a
        # timer (Nominatim's usage policy is 1 req/sec, shared process-wide).
        try:
            geo = _geocode(data["destination_text"])
        except requests.exceptions.RequestException as e:
            return {"ok": False, "error": f"Geocoding service error: {e}"}
        if not geo.get("ok"):
            return {"ok": False, "error": geo.get("error")}
        dest_lat, dest_lon, dest_label = geo["lat"], geo["lon"], geo.get("display_name")

    result = compute_route(data["lat"], data["lon"], dest_lat, dest_lon)
    if result.get("ok"):
        result["dest_lat"] = dest_lat
        result["dest_lon"] = dest_lon
        if dest_label:
            result["dest_label"] = dest_label
    return result


# ============================================================================
# HTTP API
# ============================================================================

@app.get("/health")
def health():
    return jsonify(ok=True, ts=time.time(), tools=["math", "weather", "directions", "find_nearby"], nav=True)


@app.post("/tools/math")
def tools_math():
    body = request.get_json(silent=True) or {}
    logger.info("math tool call: args=%s", body)
    result = run_math(body)
    logger.info("math tool result: %s", result)
    return jsonify(result)


@app.post("/tools/weather")
def tools_weather():
    body = request.get_json(silent=True) or {}
    logger.info("weather tool call: args=%s", body)
    result = run_weather(body)
    logger.info("weather tool result: %s", result)
    return jsonify(result)


@app.post("/tools/directions")
def tools_directions():
    body = request.get_json(silent=True) or {}
    logger.info("directions tool call: args=%s", body)
    result = run_directions(body)
    logger.info("directions tool result: %s", result)
    return jsonify(result)


@app.post("/tools/find_nearby")
def tools_find_nearby():
    body = request.get_json(silent=True) or {}
    logger.info("find_nearby tool call: query=%r lat=%r lon=%r", body.get("query"), body.get("lat"), body.get("lon"))
    result = run_find_nearby(body)
    logger.info("find_nearby tool result: ok=%s", result.get("ok"))
    return jsonify(result)


@app.post("/nav/route")
def nav_route():
    """Live-navigation routing for the dashboard's Live Navigation panel --
    NOT part of the LLM tool-calling system (see the section header comment
    above run_nav_route()). Logged at debug level, not info: this endpoint
    is polled every ~15-30s while a user has navigation active, and unlike
    the one-shot LLM tools, logging every lat/lon at info level would build
    up a de-facto location history in the systemd journal, which the task's
    privacy requirement (nothing about live position logged/persisted
    beyond the current request) argues against even as an incidental side
    effect."""
    body = request.get_json(silent=True) or {}
    logger.debug("nav route call: args=%s", body)
    result = run_nav_route(body)
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8804, threaded=True)
