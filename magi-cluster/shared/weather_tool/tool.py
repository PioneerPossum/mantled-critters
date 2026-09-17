"""
Real weather tool using Open-Meteo API.

Gets current temperature and weather data for any location worldwide.
No API key required!
"""

from __future__ import annotations

from typing import Any

from .api import get_weather_forecast

# Temperature unit configuration
TEMPERATURE_UNIT: str = "celsius"

name: str = "weather"

# User-facing description (shown in CLI tool list)
display_description: str = (
    "Get current weather and rain forecasts (supports future days) using the Open-Meteo API."
)

# LLM instruction description (includes warnings for model)
description: str = (
    "CRITICAL: Use this tool ONLY when the user explicitly asks about weather, temperature, or rain, "
    "AND you already know a real location for the query from the conversation. "
    "'location' is REQUIRED and has NO DEFAULT — there is no fallback city, and you must NEVER invent, "
    "guess, or assume one. This means: do not pick a real city (e.g. do not say 'New York'), and do not "
    "put a placeholder string in the field either (e.g. do NOT use 'here', 'your location', "
    "'current location', 'unknown', or anything similar as the location value — those are not real places "
    "and will just fail). "
    "If the user has not stated a location, and none was mentioned earlier in this conversation, "
    "do NOT call this tool at all — do not call it with a placeholder either. Instead, your entire reply "
    "must be plain text asking the user which city or location they mean (e.g. 'Sure — which city would "
    "you like the weather for?'), with no tool call, and wait for their reply before calling this tool.\n\n"
    "Supported requests: current temperature, forecasts for future days, rain/precipitation queries. "
    "For dates: use the 'future_days' parameter (e.g., 'tomorrow' -> future_days=1, 'in 3 days' -> future_days=3, 'today' -> future_days=0). "
    "Every successful call always returns full weather info (current/forecast temps plus precipitation "
    "chance and amount) — there is no way to request a partial result, so you don't need to ask for rain "
    "data separately."
)

schema: dict[str, Any] = {
    "type": "object",
    "properties": {
        "location": {
            "type": "string",
            "description": (
                "Location in 'City' or 'City, Country' format. REQUIRED — must be a real place the user "
                "actually specified (or clearly gave earlier in the conversation). There is no default "
                "location; never guess or invent one. If unknown, do not call this tool — ask the user instead."
            ),
        },
        "future_days": {
            "type": "integer",
            "description": (
                "Number of days in the future for forecast (0=today, 1=tomorrow, 2=in 2 days, etc.). "
                "Defaults to 0 if not specified."
            ),
        },
    },
    "required": ["location"],
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


# Not real places — if the model calls this tool with one of these as
# 'location' (a hallucinated placeholder rather than a genuine empty/missing
# field), reject it the same as a missing location instead of letting it go
# to the geocoder disguised as a real request.
_PLACEHOLDER_LOCATIONS = {
    "here", "your location", "your current location", "current location",
    "unknown", "n/a", "none", "not specified", "not provided", "unspecified",
    "my location", "wherever i am", "this location", "the user's location",
}


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


def _validate_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate input parameters.

    Safety net: 'location' has no default and is never inferred here — if it's
    missing, empty, blank, or an obvious non-place placeholder (e.g. "here",
    "your location"), this returns a clear error rather than silently
    defaulting to any place, so the caller (the model) gets pushed to ask the
    user instead of getting a real result for the wrong city. This only
    catches *missing/placeholder* location; it can't detect a plausible-
    looking but wrong/invented real city name, which is why the
    system-prompt-level instruction above is the primary fix for that case.
    """
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
        # Fallback validation without pydantic
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


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    """
    Get weather data for a location: current conditions (or the requested
    future day) plus forecast high/low and precipitation, always together.

    Precipitation is always included — there is no per-call flag to omit it.
    (A prior version exposed a model-controlled `include_rain` toggle; that
    let the model silently return a rain-free result for the exact same
    query it had previously answered with rain data, which read as the tool
    contradicting itself. Rain/precipitation data is now unconditional.)

    Args:
        input_data: Dictionary with keys:
            - location: Location name (required, no default — never guessed)
            - future_days: Days in future for forecast (default: 0)

    Returns:
        Dictionary with 'ok' and weather data or 'error'. If 'location' is
        missing/empty, 'ok' is False with a clear error asking for a location,
        rather than silently defaulting to any city.
    """
    validated = _validate_input(input_data)
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

