"""Today's forecast from Open-Meteo (free, no API key) for WEATHER_LOCATIONS."""

import json
import logging
import urllib.request

from . import config

logger = logging.getLogger(__name__)

# WMO weather interpretation codes → short description
_WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "heavy rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}


def forecast() -> list[dict]:
    """Return today's forecast per location, or [] if the request fails.

    Each item: name, condition, now, high, low, rain (all temperatures °F,
    rain = max precipitation probability in %).
    """
    places = config.WEATHER_LOCATIONS
    if not places:
        return []
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={','.join(str(p[1]) for p in places)}"
        f"&longitude={','.join(str(p[2]) for p in places)}"
        "&current=temperature_2m"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&temperature_unit=fahrenheit&timezone=auto&forecast_days=1"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.load(resp)
    except Exception as exc:
        logger.warning("Weather fetch failed: %s", exc)
        return []
    if isinstance(data, dict):
        data = [data]

    results = []
    for (name, _lat, _lon), item in zip(places, data):
        daily = item["daily"]
        results.append({
            "name": name,
            "condition": _WMO.get(daily["weather_code"][0], "mixed"),
            "now": round(item["current"]["temperature_2m"]),
            "high": round(daily["temperature_2m_max"][0]),
            "low": round(daily["temperature_2m_min"][0]),
            "rain": daily["precipitation_probability_max"][0] or 0,
        })
    return results


def summary_lines(days: list[dict]) -> list[str]:
    """One text line per location, e.g. 'Alexandria, VA: clear, 61°→84°F, 1% rain'."""
    return [
        f"{d['name']}: {d['condition']}, {d['low']}°→{d['high']}°F, {d['rain']}% rain"
        for d in days
    ]


def spoken(days: list[dict]) -> str:
    """Forecast phrased for speech."""
    parts = []
    for d in days:
        city, _, region = d["name"].partition(",")
        if region.strip().upper() == "DC":
            city = "D.C."
        text = f"{city}: {d['condition']}, high of {d['high']}"
        if d["rain"] >= 40:
            text += f", {d['rain']} percent chance of rain"
        parts.append(text + ".")
    return " ".join(parts)
