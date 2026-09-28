"""OpenWeatherMap "Current Weather Data" provider (data/2.5/weather).

A second, independent weather vendor: not a station and not the same model as
Open-Meteo, so its disagreement or agreement with the other two is itself
evidence. Its own docs describe the data as a blend — "collected and processed
from different sources such as global and local weather models, satellites,
radars and a vast network of weather stations" — not a single station's raw
report, so it is extracted as model evidence (``kind="forecast"``), the same
rule Open-Meteo gets, and for the same reason: a blended value for right now is
still someone's estimate, not a measurement.

Verified against openweathermap.org/current, /price and /faq (2026-09-27):

* endpoint ``GET https://api.openweathermap.org/data/2.5/weather``, params
  ``lat``, ``lon``, ``appid`` (query-parameter key), ``units=metric``;
* wind speed and gust are documented as **metre/second even under
  ``units=metric``** — only ``units=imperial`` changes them (to mph). Celsius
  comes from ``units=metric``; the speeds still need our own ×3.6 conversion;
* ``rain.1h`` is documented as mm/h and is **absent, not zero, when there is no
  rain** ("these weather phenomena are just not happened for the time of
  measurement") — so a missing field is recorded as no precipitation, not
  fabricated as a reading;
* this endpoint documents no alerts field at all. Alerts exist only on the
  separate One Call 3.0/4.0 product, gated behind the "One Call by Call"
  subscription, which requires billing details on file even for its free
  daily quota — so alerts are not attempted here (see ``alerts_supported``);
* the free "Current Weather" tier permits commercial use (attribution required
  once above the free plan, per the FAQ) — a more permissive licence than
  Open-Meteo's non-commercial-only free tier;
* 401 = invalid key; 429 = the account's call quota was exceeded for the day
  or month (FAQ), not a per-second rate limit;
* whether ``coord`` in the response echoes the exact query point or a nearby
  station/city location is **not documented** either way, so no resolution or
  "grid cell" claim is made for it — only the plain distance to it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ...domain.enums import HealthStatus
from ...domain.signals import FetchContext, GeoPoint, SourceRef, WeatherSnapshot
from ..base import WeatherProvider
from ..errors import ProviderUnauthorized

ENDPOINT = "https://api.openweathermap.org/data/2.5/weather"
MPS_TO_KMH = 3.6


class OpenWeatherMapProvider(WeatherProvider):
    uses_key = True

    def __init__(self, api_key: str | None, timeout_s: float = 8.0) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": "andon-situation-awareness/0.1"},
        )

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="openweathermap",
            name="OpenWeatherMap",
            kind="weather",
            mode="live",
            attribution="Weather data provided by OpenWeather",
            docs_url="https://openweathermap.org/current",
        )

    def precheck(self) -> tuple[HealthStatus, dict[str, Any]] | None:
        if not self._api_key:
            return HealthStatus.MISCONFIGURED, {
                "reason": "set ANDON_OPENWEATHERMAP_API_KEY (free, no card needed for this endpoint)",
                "requires": "credentials",
            }
        return None

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> WeatherSnapshot:
        response = await self._client.get(
            ENDPOINT,
            params={
                "lat": f"{point.lat:.5f}",
                "lon": f"{point.lon:.5f}",
                "appid": self._api_key,
                "units": "metric",
            },
        )
        if response.status_code == 401:
            raise ProviderUnauthorized("OpenWeatherMap rejected the key (401)", http_status=401)
        response.raise_for_status()
        return self._parse(response.json() or {})

    def _parse(self, payload: dict) -> WeatherSnapshot:
        main = payload.get("main") or {}
        wind = payload.get("wind") or {}
        rain = payload.get("rain") or {}
        weather = (payload.get("weather") or [{}])[0]

        observed_at = _from_unix(payload.get("dt")) or datetime.now(timezone.utc)

        grid = None
        coord = payload.get("coord") or {}
        if coord.get("lat") is not None and coord.get("lon") is not None:
            try:
                grid = GeoPoint(lat=float(coord["lat"]), lon=float(coord["lon"]))
            except (TypeError, ValueError):
                grid = None

        return WeatherSnapshot(
            observed_at=observed_at,
            grid_location=grid,
            # Documented as absent (not zero) when there is no rain.
            precipitation_mm_h=_float(rain.get("1h")),
            wind_speed_kmh=_mps_to_kmh(wind.get("speed")),
            wind_gust_kmh=_mps_to_kmh(wind.get("gust")),
            visibility_m=_float(payload.get("visibility")),
            temperature_c=_float(main.get("temp")),
            condition=weather.get("description"),
            recent=[],
            outlook=[],
            alerts=[],
            # Alerts live behind the separate, card-required One Call product —
            # not requested here, so this feed's silence must not be read as
            # "no warnings active".
            alerts_supported=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _mps_to_kmh(value: Any) -> float | None:
    value = _float(value)
    return round(value * MPS_TO_KMH, 1) if value is not None else None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _from_unix(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc) if value is not None else None
    except (TypeError, ValueError, OSError):
        return None
