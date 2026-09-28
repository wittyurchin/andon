"""Mock weather providers driven by the shared scenario clock.

Two roles, mirroring the two real kinds of weather evidence:

* ``MockWeatherProvider`` behaves like a numerical model (Open-Meteo): a value
  for a grid cell near the restaurant plus a short forecast. Everything it
  produces is model output — including its "past" blocks.
* ``MockStationProvider`` behaves like a station (METAR): a qualitative band at
  a fixed point some distance away, with half-hourly report history.

In the ``demo`` scenario the model over-reads the rain relative to the station,
which gives the dashboard a genuine source conflict to show.

Everything here reports ``mode="mock"`` and is labelled simulated in the UI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...domain.enums import EvidenceKind, Severity
from ...domain.signals import (
    FetchContext,
    GeoPoint,
    PrecipPoint,
    SourceRef,
    WeatherAlert,
    WeatherSnapshot,
)
from ...geo import haversine_km, location_key, offset
from ..base import WeatherProvider
from ..scenario import ScenarioClock, site_jitter

SIMULATED = "Simulated data — not a real observation"


class MockWeatherProvider(WeatherProvider):
    """Model-like mock: grid value plus forecast."""

    def __init__(self, clock: ScenarioClock) -> None:
        self._clock = clock

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="mock-weather",
            name=f"Mock weather model ({self._clock.scenario})",
            kind="weather",
            mode="mock",
            attribution=SIMULATED,
        )

    def _intensity(self, point: GeoPoint, offset_min: float) -> float:
        if self._clock.scenario == "demo":
            # The model over-reads this rain band — a deliberate conflict.
            return min(1.0, self._clock.demo_rain(offset_min) + ScenarioClock.DEMO_MODEL_BIAS)
        jitter = site_jitter(location_key(point), "weather")
        return max(0.0, min(1.0, self._clock.intensity(offset_min) + jitter))

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> WeatherSnapshot:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        intensity = self._intensity(point, 0)
        precip = _precip_mm_h(intensity)

        # Model output, past or future: never an observation.
        recent = [_point(now + timedelta(minutes=m), self._intensity(point, m)) for m in (-45, -30, -15, 0)]
        outlook = [_point(now + timedelta(minutes=m), self._intensity(point, m)) for m in (15, 30, 45, 60, 75, 90)]

        alerts: list[WeatherAlert] = []
        if intensity >= 0.82:
            alerts.append(
                WeatherAlert(
                    event="Heavy rainfall warning",
                    headline="Heavy rainfall expected to continue in the area",
                    severity_hint=Severity.HIGH,
                    starts_at=now - timedelta(minutes=25),
                    ends_at=now + timedelta(minutes=90),
                )
            )

        return WeatherSnapshot(
            observed_at=now,
            # A model grid cell is not centred on the kitchen; the mock says so.
            grid_location=offset(point, 225, 1400),
            precipitation_mm_h=precip,
            probability_pct=int(min(99, 25 + intensity * 74)),
            wind_speed_kmh=round(8 + intensity * 38, 1),
            wind_gust_kmh=round(14 + intensity * 62, 1),
            visibility_m=round(12000 * (1 - intensity) ** 1.2 + 400),
            temperature_c=round(30 - intensity * 7, 1),
            condition=_condition(precip),
            recent=recent,
            outlook=outlook,
            alerts=alerts,
            alerts_supported=True,
        )


class MockStationProvider(WeatherProvider):
    """Station-like mock: a band observed at a point ~3 km away, half-hourly."""

    STATION_BEARING_DEG = 40.0
    STATION_DISTANCE_M = 3200.0
    HISTORY_MINUTES = (-90, -60, -30, 0)

    def __init__(self, clock: ScenarioClock) -> None:
        self._clock = clock

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="mock-station",
            name=f"Mock weather station ({self._clock.scenario})",
            kind="weather",
            mode="mock",
            attribution=SIMULATED,
        )

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> WeatherSnapshot:
        station = offset(point, self.STATION_BEARING_DEG, self.STATION_DISTANCE_M)
        # Reports land on the half hour, like the real stations we observed.
        now = datetime.now(timezone.utc)
        report_time = now.replace(minute=(now.minute // 30) * 30, second=0, microsecond=0)
        lag_min = (now - report_time).total_seconds() / 60.0

        reports = []
        for m in self.HISTORY_MINUTES:
            band, condition, code = _band(self._clock.intensity(m - lag_min))
            reports.append(
                PrecipPoint(
                    at=report_time + timedelta(minutes=m),
                    rain_intensity=band,
                    condition=condition,
                    raw_text=f"MOCK {code or 'NSW'} (simulated station report)",
                    visibility_m=9000.0 if band is Severity.NONE else 4000.0,
                    kind=EvidenceKind.OBSERVED,
                )
            )

        latest = reports[-1]
        return WeatherSnapshot(
            observed_at=latest.at,
            precipitation_mm_h=None,  # a station band, like METAR — no rate
            rain_intensity=latest.rain_intensity,
            station_id="MOCK1",
            station_name="MOCK1 (Simulated station)",
            station_location=station,
            station_distance_km=round(haversine_km(point, station), 1),
            raw_text=latest.raw_text,
            wind_speed_kmh=14.8,
            wind_gust_kmh=None,  # no gust reported
            visibility_m=latest.visibility_m,
            temperature_c=24.0,
            condition=latest.condition,
            recent=reports,
            outlook=[],
            alerts_supported=False,  # mirrors METAR: no severe-weather feed
        )


def _band(intensity: float) -> tuple[Severity, str, str | None]:
    if intensity < 0.12:
        return Severity.NONE, "No significant weather", None
    if intensity < 0.40:
        return Severity.LOW, "Light rain", "-RA"
    if intensity < 0.70:
        return Severity.MEDIUM, "Rain", "RA"
    return Severity.HIGH, "Heavy rain", "+RA"


def _precip_mm_h(intensity: float) -> float:
    if intensity < 0.12:
        return 0.0
    return round((intensity**1.6) * 22.0, 2)


def _point(at: datetime, intensity: float) -> PrecipPoint:
    return PrecipPoint(
        at=at,
        precipitation_mm_h=_precip_mm_h(intensity),
        probability_pct=int(min(99, 25 + intensity * 74)),
        visibility_m=round(12000 * (1 - intensity) ** 1.2 + 400),
        wind_gust_kmh=round(14 + intensity * 62, 1),
        kind=EvidenceKind.FORECAST,
    )


def _condition(precip_mm_h: float) -> str:
    if precip_mm_h <= 0:
        return "Cloudy"
    if precip_mm_h < 2.5:
        return "Light rain"
    if precip_mm_h < 7.6:
        return "Rain"
    return "Heavy rain"
