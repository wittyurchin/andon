"""Open-Meteo weather provider.

Chosen because it needs no credentials, which keeps the default local setup
honest: at least one signal is real data rather than a simulation.

Caveat we surface rather than hide: Open-Meteo is a numerical model, not a
station reading, and it has no severe-weather alert feed. Both facts are
reported through the normal channels (source confidence, coverage gaps).

Verified against documentation/open-meteo/ (2026-09-27):
- the free endpoint is licensed for non-commercial use only (terms.html);
- commercial use goes to customer-api.open-meteo.com with ``&apikey=`` and is
  otherwise identical (pricing.html);
- the response's ``latitude``/``longitude`` is the model grid cell, which can
  sit kilometres from the requested point. We record it rather than pretend
  the value was computed for the restaurant's exact coordinates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from ...domain.enums import EvidenceKind
from ...domain.signals import (
    FetchContext,
    GeoPoint,
    PrecipPoint,
    SourceRef,
    WeatherSnapshot,
)
from ..base import WeatherProvider

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
CUSTOMER_ENDPOINT = "https://customer-api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes, trimmed to what matters operationally.
WMO_CODES: dict[int, str] = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Freezing fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Heavy freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Rain showers",
    82: "Violent rain showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


class OpenMeteoWeatherProvider(WeatherProvider):
    def __init__(self, timeout_s: float = 8.0, api_key: str | None = None) -> None:
        self._api_key = api_key or None
        self.uses_key = self._api_key is not None
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": "andon-situation-awareness/0.1"},
        )

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="open-meteo",
            name="Open-Meteo",
            kind="weather",
            mode="live",
            attribution="Open-Meteo.com (CC-BY 4.0), numerical weather model",
            docs_url="https://open-meteo.com/en/docs",
            licence_note=None
            if self._api_key
            else "Free tier: non-commercial use only. Set ANDON_OPEN_METEO_API_KEY for the commercial endpoint.",
        )

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> WeatherSnapshot:
        params = {
            "latitude": f"{point.lat:.5f}",
            "longitude": f"{point.lon:.5f}",
            "current": "precipitation,rain,weather_code,wind_speed_10m,wind_gusts_10m,temperature_2m",
            "minutely_15": "precipitation,precipitation_probability,visibility,wind_gusts_10m",
            "past_minutely_15": 4,  # last hour, observed side of the model
            "forecast_minutely_15": 8,  # next two hours
            "wind_speed_unit": "kmh",
            "timezone": "UTC",
        }
        endpoint = ENDPOINT
        if self._api_key:
            endpoint = CUSTOMER_ENDPOINT
            params["apikey"] = self._api_key
        response = await self._client.get(endpoint, params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(str(payload.get("reason", "open-meteo error")))
        return self._parse(payload)

    def _parse(self, payload: dict) -> WeatherSnapshot:
        current = payload.get("current") or {}
        observed_at = _parse_ts(current.get("time")) or datetime.now(timezone.utc)
        # `current.precipitation` is a sum over `interval` seconds; normalize to mm/h.
        interval_s = float(current.get("interval") or 900) or 900.0
        precip_mm_h = _maybe_float(current.get("precipitation"))
        if precip_mm_h is not None:
            precip_mm_h = round(precip_mm_h * 3600.0 / interval_s, 2)

        recent, outlook = self._split_minutely(payload, observed_at)

        # Visibility is only available on the 15-minute series; take the block
        # covering now, and fall back to the nearest one we have.
        visibility = _latest(recent, "visibility_m") or _first(outlook, "visibility_m")
        probability = _first(outlook, "probability_pct") or _latest(recent, "probability_pct")

        code = current.get("weather_code")
        condition = WMO_CODES.get(int(code)) if code is not None else None

        grid = None
        try:
            if payload.get("latitude") is not None and payload.get("longitude") is not None:
                grid = GeoPoint(lat=float(payload["latitude"]), lon=float(payload["longitude"]))
        except (TypeError, ValueError):
            grid = None

        return WeatherSnapshot(
            observed_at=observed_at,
            grid_location=grid,
            precipitation_mm_h=precip_mm_h,
            probability_pct=int(probability) if probability is not None else None,
            wind_speed_kmh=_maybe_float(current.get("wind_speed_10m")),
            wind_gust_kmh=_maybe_float(current.get("wind_gusts_10m")),
            visibility_m=visibility,
            temperature_c=_maybe_float(current.get("temperature_2m")),
            condition=condition,
            recent=recent,
            outlook=outlook,
            alerts=[],
            # Open-Meteo's free forecast API carries no warnings feed. Saying so
            # is different from saying "no warnings are active".
            alerts_supported=False,
        )

    def _split_minutely(
        self, payload: dict, now: datetime
    ) -> tuple[list[PrecipPoint], list[PrecipPoint]]:
        series = payload.get("minutely_15") or {}
        times = series.get("time") or []
        precip = series.get("precipitation") or []
        prob = series.get("precipitation_probability") or []
        vis = series.get("visibility") or []
        gusts = series.get("wind_gusts_10m") or []

        recent: list[PrecipPoint] = []
        outlook: list[PrecipPoint] = []
        cutoff = now + timedelta(minutes=1)

        for i, raw_time in enumerate(times):
            at = _parse_ts(raw_time)
            if at is None:
                continue
            is_past = at <= cutoff
            mm_per_block = _maybe_float(_at(precip, i))
            point = PrecipPoint(
                at=at,
                # 15-minute accumulation -> mm/h
                precipitation_mm_h=round(mm_per_block * 4, 2) if mm_per_block is not None else None,
                probability_pct=int(p) if (p := _at(prob, i)) is not None else None,
                visibility_m=_maybe_float(_at(vis, i)),
                wind_gust_kmh=_maybe_float(_at(gusts, i)),
                # Past blocks are the model's analysis, not a measurement, so
                # they are model output like the rest — never "observed".
                kind=EvidenceKind.FORECAST,
            )
            (recent if is_past else outlook).append(point)

        return recent, outlook

    async def aclose(self) -> None:
        await self._client.aclose()


def _at(seq: list, index: int):
    return seq[index] if index < len(seq) else None


def _maybe_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _latest(points: list[PrecipPoint], field: str) -> float | None:
    for point in reversed(points):
        value = getattr(point, field)
        if value is not None:
            return value
    return None


def _first(points: list[PrecipPoint], field: str) -> float | None:
    for point in points:
        value = getattr(point, field)
        if value is not None:
            return value
    return None
