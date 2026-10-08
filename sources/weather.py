# The weather, from Open-Meteo (free, no key). Set in config.toml, not in the environment:
#
#   [weather]
#   latitude = 52.52
#   longitude = 13.41
#   timezone = "Europe/Berlin"   # optional, defaults to the forecast location's own
#   city = "Berlin"              # optional, shown next to the temperature

import urllib.parse

from ._common import request, source


@source("weather", every=900, section="weather")
def weather(cfg):
    qs = urllib.parse.urlencode({
        "latitude": cfg["latitude"], "longitude": cfg["longitude"],
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min", "timezone": cfg.get("timezone", "auto"),
        "forecast_days": 1,
    })
    d = request(f"https://api.open-meteo.com/v1/forecast?{qs}", timeout=15)
    current = d.get("current") or {}
    day = d.get("daily") or {}
    return {
        "temp": current.get("temperature_2m"),
        "code": current.get("weather_code"),
        "max": (day.get("temperature_2m_max") or [None])[0],
        "min": (day.get("temperature_2m_min") or [None])[0],
    }
