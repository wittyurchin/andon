"""Provider wiring.

The only place in the codebase that knows which vendor is in use. Every signal
can have several providers; each is fetched independently, so one failing
never stops the others.

Sources whose adapter exists but cannot run — no credentials, no authorised
access path, licence not acknowledged — are *inactive*: they are listed with
the exact reason (so source health shows the whole portfolio) but never called.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..domain.enums import HealthStatus
from ..domain.signals import (
    FetchContext,
    GeoPoint,
    IncidentSnapshot,
    ProviderResponse,
    RadarSnapshot,
    TrafficSnapshot,
    WeatherSnapshot,
)
from . import disabled
from .base import Provider
from .cache import CachedProvider
from .incidents.mock import MockIncidentProvider
from .incidents.tomtom import TomTomIncidentProvider
from .radar.mock import MockRadarProvider
from .radar.rainviewer import RainViewerRadarProvider
from .scenario import ScenarioClock
from .traffic.mappls import MapplsCorridorTrafficProvider
from .traffic.mock import MockOutageTrafficProvider, MockTrafficProvider
from .traffic.tomtom import TomTomTrafficProvider
from .weather.awc_metar import AwcMetarWeatherProvider
from .weather.imd import ImdWeatherProvider
from .weather.mock import MockStationProvider, MockWeatherProvider
from .weather.ndma_sachet import NdmaSachetAlertProvider
from .weather.open_meteo import OpenMeteoWeatherProvider
from .weather.openweathermap import OpenWeatherMapProvider
from .weather.weatherapi import WeatherApiComProvider

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SignalBundle:
    """One parallel fetch of every active source.

    Every signal is a list: several providers answer the same question
    differently, and disagreement between them is itself evidence.
    """

    weather: list[ProviderResponse[WeatherSnapshot]]
    traffic: list[ProviderResponse[TrafficSnapshot]]
    incidents: list[ProviderResponse[IncidentSnapshot]]
    radar: list[ProviderResponse[RadarSnapshot]] = field(default_factory=list)
    # Sources that were not called, with why (see Provider.precheck).
    inactive: list[ProviderResponse] = field(default_factory=list)

    def all(self) -> list[ProviderResponse]:
        return [*self.weather, *self.radar, *self.traffic, *self.incidents]

    def everything(self) -> list[ProviderResponse]:
        return [*self.all(), *self.inactive]


class ProviderRegistry:
    def __init__(self, settings: Settings, clock: ScenarioClock | None = None) -> None:
        self._settings = settings
        self.clock = clock or ScenarioClock(settings.effective_scenario, settings.mock_ramp_minutes)

        built = {
            "weather": [self._weather(n) for n in settings.resolved_weather_providers],
            "radar": [self._radar(n) for n in settings.resolved_radar_providers],
            "traffic": [self._traffic(n) for n in settings.resolved_traffic_providers],
            "incidents": [self._incidents(n) for n in settings.resolved_incident_providers],
        }
        ttl = {
            "weather": settings.weather_cache_ttl_s,
            "radar": settings.radar_cache_ttl_s,
            "traffic": settings.traffic_cache_ttl_s,
            "incidents": settings.incident_cache_ttl_s,
        }

        self.weather: list[Provider] = []
        self.radar: list[Provider] = []
        self.traffic: list[Provider] = []
        self.incidents: list[Provider] = []
        # Inactive providers, with the precheck result that made them so.
        self.inactive: list[tuple[Provider, HealthStatus, dict[str, Any]]] = []

        for kind, providers in built.items():
            for provider in providers:
                blocked = provider.precheck()
                if blocked is not None:
                    self.inactive.append((provider, *blocked))
                    continue
                getattr(self, kind).append(CachedProvider(provider, ttl[kind]))

        log.info(
            "providers configured",
            extra={
                "weather": [p.source.id for p in self.weather],
                "radar": [p.source.id for p in self.radar],
                "traffic": [p.source.id for p in self.traffic],
                "incidents": [p.source.id for p in self.incidents],
                "inactive": {p.source.id: status.value for p, status, _ in self.inactive},
                "scenario": self.clock.scenario,
            },
        )

    # -- construction ----------------------------------------------------

    def _weather(self, name: str) -> Provider:
        s = self._settings
        if name == "awc_metar":
            return AwcMetarWeatherProvider(s.provider_timeout_s, s.metar_search_radius_km)
        if name == "imd":
            return ImdWeatherProvider(
                api_key=s.imd_api_key,
                auth_header=s.imd_auth_header,
                auth_query_param=s.imd_auth_query_param,
                state_id=s.imd_aws_state_id,
                base_url=s.imd_base_url,
                timeout_s=s.provider_timeout_s,
            )
        if name == "ksndmc":
            return disabled.ksndmc_weather()
        if name == "openweathermap":
            return OpenWeatherMapProvider(s.openweathermap_api_key, s.provider_timeout_s)
        if name == "weatherapi":
            return WeatherApiComProvider(s.weatherapi_key, s.provider_timeout_s)
        if name == "ndma_sachet":
            return NdmaSachetAlertProvider(s.provider_timeout_s)
        if name == "mock":
            return MockWeatherProvider(self.clock)
        if name == "mock_station":
            return MockStationProvider(self.clock)
        return OpenMeteoWeatherProvider(s.provider_timeout_s, api_key=s.open_meteo_api_key)

    def _radar(self, name: str) -> Provider:
        if name == "mock":
            return MockRadarProvider(self.clock)
        return RainViewerRadarProvider(
            self._settings.provider_timeout_s,
            terms_acknowledged=self._settings.rainviewer_terms_acknowledged,
        )

    def _traffic(self, name: str) -> Provider:
        s = self._settings
        if name == "tomtom":
            return TomTomTrafficProvider(s.tomtom_api_key, s.local_radius_m, s.provider_timeout_s)
        if name == "mappls":
            return MapplsCorridorTrafficProvider(
                s.mappls_access_token, s.mappls_compare_optimal, s.provider_timeout_s
            )
        if name == "mock_outage":
            return MockOutageTrafficProvider()
        return MockTrafficProvider(self.clock, s.local_radius_m)

    def _incidents(self, name: str) -> Provider:
        s = self._settings
        if name == "tomtom":
            return TomTomIncidentProvider(s.tomtom_api_key, s.incident_radius_km, s.provider_timeout_s)
        if name == "mappls":
            return disabled.mappls_incidents()
        if name == "btp":
            return disabled.bengaluru_traffic_police()
        if name == "bmc":
            return disabled.bmc_disaster_management()
        return MockIncidentProvider(self.clock, s.incident_radius_km)

    # -- use -------------------------------------------------------------

    @property
    def active(self) -> list[Provider]:
        return [*self.weather, *self.radar, *self.traffic, *self.incidents]

    async def fetch_all(
        self,
        point: GeoPoint,
        context: FetchContext | None = None,
        *,
        bypass_cache: bool = False,
    ) -> SignalBundle:
        """Fetch every active source in parallel. Individual failures are contained."""
        context = context or FetchContext()
        if bypass_cache:
            for provider in self.active:
                if isinstance(provider, CachedProvider):
                    provider.invalidate(point)

        groups = (self.weather, self.radar, self.traffic, self.incidents)
        results = await asyncio.gather(
            *(p.fetch(point, context) for group in groups for p in group)
        )
        sliced: list[list[ProviderResponse]] = []
        cursor = 0
        for group in groups:
            sliced.append(list(results[cursor : cursor + len(group)]))
            cursor += len(group)

        inactive = [
            provider._failure(status, details.get("reason", status.value), 0, details)
            for provider, status, details in self.inactive
        ]
        return SignalBundle(
            weather=sliced[0],
            radar=sliced[1],
            traffic=sliced[2],
            incidents=sliced[3],
            inactive=inactive,
        )

    def find(self, source_id: str) -> Provider | None:
        for provider in self.active:
            if provider.source.id == source_id:
                return provider
        return None

    def describe(self, source_id: str):
        """SourceRef for any configured source, active or inactive."""
        for provider in [*self.active, *(p for p, _, _ in self.inactive)]:
            if provider.source.id == source_id:
                return provider.source
        return None

    async def aclose(self) -> None:
        await asyncio.gather(
            *(p.aclose() for p in self.active),
            *(p.aclose() for p, _, _ in self.inactive),
            return_exceptions=True,
        )
