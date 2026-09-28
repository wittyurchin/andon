"""Test harness for the evidence layer: scripted providers and a movable clock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from andon.config import Settings
from andon.domain.enums import IncidentCategory, Severity
from andon.domain.signals import (
    FetchContext,
    GeoPoint,
    Incident,
    IncidentSnapshot,
    PrecipPoint,
    RoadProbe,
    SourceRef,
    TrafficSnapshot,
    WeatherSnapshot,
)
from andon.evidence.service import EvidenceService
from andon.evidence.store import EvidenceStore
from andon.providers.base import Provider
from andon.providers.registry import ProviderRegistry

HSR = GeoPoint(lat=12.912233, lon=77.651282)
T0 = datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)


class Scripted(Provider):
    """A provider whose output (or failure) the test controls per call."""

    def __init__(self, kind: str, source_id: str, produce: Callable[[FetchContext], object], mode: str = "live") -> None:
        self.kind = kind
        self._source = SourceRef(id=source_id, name=source_id.replace("-", " ").title(), kind=kind, mode=mode)
        self.produce = produce
        self.calls = 0

    @property
    def source(self) -> SourceRef:
        return self._source

    async def _fetch(self, point: GeoPoint, context: FetchContext):
        self.calls += 1
        result = self.produce(context)
        if isinstance(result, Exception):
            raise result
        return result


def settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        _env_file=None,
        database_path=str(tmp_path / "evidence.sqlite3"),
        access_graph_source="mock",
        llm_enabled=False,
        weather_providers="mock",
        traffic_providers="mock",
        incident_providers="mock",
        radar_providers="none",
        mode="auto",
    )
    base.update(overrides)
    return Settings(**base)


def make_service(
    tmp_path: Path,
    *,
    weather: list[Provider] = (),
    traffic: list[Provider] = (),
    incidents: list[Provider] = (),
    radar: list[Provider] = (),
    clock: Clock | None = None,
    store: EvidenceStore | None = None,
    **overrides,
) -> tuple[EvidenceService, EvidenceStore, Clock]:
    cfg = settings(tmp_path, **overrides)
    store = store or EvidenceStore(cfg.database_path)
    registry = ProviderRegistry(cfg)
    registry.weather, registry.traffic = list(weather), list(traffic)
    registry.incidents, registry.radar = list(incidents), list(radar)
    registry.inactive = []
    clock = clock or Clock()
    return EvidenceService(cfg, store, registry, clock=clock), store, clock


# -- canned snapshots ----------------------------------------------------------


def station(clock: Clock, band: Severity, distance_km: float = 4.0, bearing_deg: float = 30.0,
            age_min: float = 5.0, gust: float | None = None) -> Callable:
    from andon.geo import offset

    def produce(_ctx):
        at = clock.now - timedelta(minutes=age_min)
        loc = offset(HSR, bearing_deg, distance_km * 1000)
        return WeatherSnapshot(
            observed_at=at, rain_intensity=band, station_id="ST1", station_name="ST1 (Test)",
            station_location=loc, station_distance_km=distance_km, wind_speed_kmh=10.0,
            wind_gust_kmh=gust, visibility_m=6000.0, condition="Rain",
            recent=[PrecipPoint(at=at, rain_intensity=band, kind="observed", raw_text="TEST RA")],
            alerts_supported=False,
        )
    return produce


def model(clock: Clock, mm_h: float) -> Callable:
    from andon.geo import offset

    def produce(_ctx):
        return WeatherSnapshot(
            observed_at=clock.now, precipitation_mm_h=mm_h, grid_location=offset(HSR, 200, 1500),
            outlook=[PrecipPoint(at=clock.now + timedelta(minutes=15), precipitation_mm_h=mm_h)],
            alerts_supported=False,
        )
    return produce


def flow(clock: Clock, congestion_by_index: Callable[[int], float], free_flow: float | None = 40.0) -> Callable:
    def produce(ctx: FetchContext):
        probes = []
        for i, a in enumerate(ctx.approaches):
            c = congestion_by_index(i)
            probes.append(RoadProbe(
                label=a.label, bearing=a.bearing or "N", distance_m=int(a.distance_m or 0),
                current_speed_kmh=40.0 * (1 - c), free_flow_speed_kmh=free_flow,
                approach_id=a.approach_id, location=a.probe, source_confidence=0.9,
            ))
        return TrafficSnapshot(observed_at=clock.now, probes=probes)
    return produce


def incident_feed(clock: Clock, items: Callable[[FetchContext], list[Incident]]) -> Callable:
    def produce(ctx):
        return IncidentSnapshot(
            observed_at=clock.now, incidents=items(ctx), radius_km=5.0,
            covered_categories=[IncidentCategory.ACCIDENT, IncidentCategory.WATERLOGGING],
        )
    return produce


def on_approach(ctx: FetchContext, index: int, record_id: str, category=IncidentCategory.WATERLOGGING) -> Incident:
    target = ctx.approaches[index]
    return Incident(id=record_id, category=category, description=f"{category.value} test",
                    location=target.probe, distance_km=(target.distance_m or 0) / 1000)
