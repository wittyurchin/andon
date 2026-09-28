"""WeatherAPI.com "Current Weather" provider (v1/current.json).

A fourth, independent weather vendor. Verified against weatherapi.com/docs
(2026-09-28):

* endpoint ``GET https://api.weatherapi.com/v1/current.json``, params ``key``,
  ``q=lat,lon``;
* both metric and imperial fields are always present — no ``units`` parameter
  exists. ``wind_kph``/``gust_kph`` and ``vis_km`` are already the units this
  app uses internally, so no conversion is needed;
* ``location.lat``/``location.lon`` echo the **matched** location, not
  necessarily the exact query point — recorded as such, no resolution claim;
* the docs describe capabilities (real-time, forecast, historical) but make
  **no statement** about whether a reading is a station report or a blended
  estimate. Since there is no station id/name in the payload — one merged
  object per queried location, not an individual station's report — this is
  extracted as model evidence, the same rule Open-Meteo and OpenWeatherMap
  get: a merged value for right now is still somebody's estimate;
* **``precip_mm``'s time window is not documented** (unlike Open-Meteo's or
  OpenWeatherMap's explicit "mm/h"). Treating it as a rate would be a
  fabricated unit, so it is kept verbatim under ``reported`` and never banded
  or fed into the rain-severity rules;
* free tier: 100,000 calls/month, no card required, **commercial use
  permitted** (weatherapi.com/pricing.aspx);
* 401, error code 2006, "API key provided is invalid"; **403**, error code
  2007, "API key has exceeded calls per month quota" — documented as a quota
  problem, not an authorization failure, so it is reported as unavailable
  with ``quota_exceeded``, not unauthorized;
* the Alerts API is documented as covering "USA, UK, Europe and Rest of the
  World" with no India-specific confirmation, and the (real, production)
  Google Sheet this was ported from sources its official alerts from NDMA
  Sachet instead of WeatherAPI — so alerts are not requested here either.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ...domain.enums import HealthStatus
from ...domain.signals import FetchContext, GeoPoint, SourceRef, WeatherSnapshot
from ..base import WeatherProvider
from ..errors import ProviderError, ProviderUnauthorized

ENDPOINT = "https://api.weatherapi.com/v1/current.json"


class WeatherApiComProvider(WeatherProvider):
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
            id="weatherapi",
            name="WeatherAPI.com",
            kind="weather",
            mode="live",
            attribution="Weather data by WeatherAPI.com",
            docs_url="https://www.weatherapi.com/docs/",
        )

    def precheck(self) -> tuple[HealthStatus, dict[str, Any]] | None:
        if not self._api_key:
            return HealthStatus.MISCONFIGURED, {
                "reason": "set ANDON_WEATHERAPI_KEY (free, no card needed)",
                "requires": "credentials",
            }
        return None

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> WeatherSnapshot:
        response = await self._client.get(
            ENDPOINT,
            params={"key": self._api_key, "q": f"{point.lat:.5f},{point.lon:.5f}"},
        )
        if response.status_code == 401:
            raise ProviderUnauthorized("WeatherAPI.com rejected the key (401)", http_status=401)
        if response.status_code == 403:
            # Documented as the monthly quota, not an auth failure (error code 2007).
            raise ProviderError(
                "WeatherAPI.com monthly call quota exceeded (403)", http_status=403, quota_exceeded=True
            )
        response.raise_for_status()
        return self._parse(response.json() or {})

    def _parse(self, payload: dict) -> WeatherSnapshot:
        current = payload.get("current") or {}
        location = payload.get("location") or {}
        condition = current.get("condition") or {}

        observed_at = _from_unix(current.get("last_updated_epoch")) or datetime.now(timezone.utc)

        grid = None
        if location.get("lat") is not None and location.get("lon") is not None:
            try:
                grid = GeoPoint(lat=float(location["lat"]), lon=float(location["lon"]))
            except (TypeError, ValueError):
                grid = None

        reported: dict[str, Any] = {}
        precip_mm = _float(current.get("precip_mm"))
        if precip_mm is not None:
            # Time window is not documented — never labelled as a rate.
            reported["precip_mm"] = precip_mm
            reported["precip_units_documented"] = False

        return WeatherSnapshot(
            observed_at=observed_at,
            grid_location=grid,
            wind_speed_kmh=_float(current.get("wind_kph")),
            wind_gust_kmh=_float(current.get("gust_kph")),
            visibility_m=_km_to_m(current.get("vis_km")),
            temperature_c=_float(current.get("temp_c")),
            condition=condition.get("text"),
            reported=reported,
            recent=[],
            outlook=[],
            alerts=[],
            # Documented for USA/UK/Europe/"Rest of the World"; not confirmed
            # for India, and the production system this was ported from uses
            # NDMA Sachet for alerts instead — not requested here.
            alerts_supported=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _km_to_m(value: Any) -> float | None:
    value = _float(value)
    return round(value * 1000, 1) if value is not None else None


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
