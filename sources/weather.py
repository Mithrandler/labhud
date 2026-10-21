# The weather, from Open-Meteo (free, no key). Set in config.toml, not in the environment:
#
#   [weather]
#   latitude = 52.52
#   longitude = 13.41
#   timezone = "Europe/Berlin"   # optional, defaults to the forecast location's own
#   city = "Berlin"              # optional, shown next to the temperature

import urllib.parse

from ._common import request, source


@source("weather", every=900, section="weather",
         title="Weather", about="Open-Meteo, free and keyless; set by city on the setup page.")
def weather(cfg):
    qs = urllib.parse.urlencode({
        "latitude": cfg["latitude"], "longitude": cfg["longitude"],
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
        "timezone": cfg.get("timezone", "auto"),
        "forecast_days": 4,   # today + the next three, for the forecast panel and the calm screen
    })
    d = request(f"https://api.open-meteo.com/v1/forecast?{qs}", timeout=15, public=True)
    current = d.get("current") or {}
    day = d.get("daily") or {}
    col = lambda key: day.get(key) or []
    days = [{"date": date, "max": hi, "min": lo, "code": code, "rain": rain}
            for date, hi, lo, code, rain in zip(col("time"), col("temperature_2m_max"), col("temperature_2m_min"),
                                                col("weather_code"), col("precipitation_probability_max") or [None] * 4)]
    return {
        "temp": current.get("temperature_2m"),
        "code": current.get("weather_code"),
        "max": (col("temperature_2m_max") or [None])[0],
        "min": (col("temperature_2m_min") or [None])[0],
        "days": days,   # [{date "YYYY-MM-DD", max, min, code, rain %}], today first
    }
