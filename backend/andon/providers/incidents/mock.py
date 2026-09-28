"""Mock incident provider.

Incidents are placed *on the access corridors* when the fetch context supplies
them, so spatial relevance ("does this block an approach?") is exercised the
same way real incidents would exercise it.

Scenarios:
* ``demo`` — waterlogging on the second approach from the start; an accident
  on the third approach appearing a few minutes in; distant roadworks.
* others — incidents appear as the shared scenario intensity rises.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...domain.enums import Confidence, IncidentCategory, Severity
from ...domain.signals import (
    ApproachTarget,
    FetchContext,
    GeoPoint,
    Incident,
    IncidentSnapshot,
    SourceRef,
)
from ...geo import bearing_label, haversine_km, location_key, offset
from ..base import IncidentProvider
from ..scenario import ScenarioClock, site_jitter

COVERED = [
    IncidentCategory.WATERLOGGING,
    IncidentCategory.FLOODING,
    IncidentCategory.ROAD_CLOSURE,
    IncidentCategory.ACCIDENT,
    IncidentCategory.CONSTRUCTION,
    IncidentCategory.EVENT,
]


class MockIncidentProvider(IncidentProvider):
    def __init__(self, clock: ScenarioClock, radius_km: float = 5.0) -> None:
        self._clock = clock
        self._radius_km = radius_km

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="mock-incidents",
            name=f"Mock local incidents ({self._clock.scenario})",
            kind="incidents",
            mode="mock",
            attribution="Simulated data — not a real observation",
        )

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> IncidentSnapshot:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        key = location_key(point)
        approaches = context.approaches
        incidents: list[Incident] = []

        def on(index: int, fallback_bearing: float, fallback_m: float) -> tuple[GeoPoint, ApproachTarget | None]:
            if len(approaches) > index:
                return approaches[index].probe, approaches[index]
            return offset(point, fallback_bearing, fallback_m), None

        def add(record_id, category, description, where, target, severity, reported_min_ago, confidence):
            incidents.append(
                Incident(
                    id=record_id,
                    category=category,
                    description=description,
                    distance_km=round(haversine_km(point, where), 2),
                    bearing=bearing_label(point, where),
                    road=(target.road_name or target.label) if target else None,
                    severity_hint=severity,
                    reported_at=now - timedelta(minutes=reported_min_ago),
                    last_reported_at=now - timedelta(minutes=min(reported_min_ago, 4)),
                    confidence=confidence,
                    location=where,
                    geometry={"type": "Point", "coordinates": [where.lon, where.lat]},
                    attributes={"simulated": True},
                )
            )

        # Distant roadworks: present in every scenario, rarely relevant.
        works = offset(point, 40, 1900 + site_jitter(key, "works", 400))
        add("mock-works-1", IncidentCategory.CONSTRUCTION, "Lane closed for roadworks",
            works, None, Severity.LOW, 360, Confidence.HIGH)

        if self._clock.scenario == "demo":
            where, target = on(1, 90, 700)
            add("mock-waterlog-1", IncidentCategory.WATERLOGGING,
                "Standing water across the carriageway, vehicles slowing",
                where, target, Severity.HIGH, 25, Confidence.MEDIUM)
            if self._clock.demo_accident_active():
                where, target = on(2, 180, 600)
                add("mock-accident-1", IncidentCategory.ACCIDENT,
                    "Two-vehicle collision, one lane blocked",
                    where, target, Severity.MEDIUM, 2, Confidence.HIGH)
        else:
            intensity = self._clock.intensity()
            if intensity >= 0.55:
                where, target = on(1, 180, 1200)
                add("mock-waterlog-1", IncidentCategory.WATERLOGGING,
                    "Standing water reported on the carriageway, vehicles slowing",
                    where, target, Severity.HIGH if intensity >= 0.75 else Severity.MEDIUM,
                    int(8 + (1 - intensity) * 30), Confidence.MEDIUM)
            if intensity >= 0.72:
                where, target = on(3, 270, 2600)
                add("mock-accident-1", IncidentCategory.ACCIDENT,
                    "Two-vehicle collision, one lane blocked",
                    where, target, Severity.MEDIUM, 14, Confidence.HIGH)
            if intensity >= 0.86:
                where, target = on(2, 135, 900)
                add("mock-closure-1", IncidentCategory.ROAD_CLOSURE,
                    "Road closed due to flooding",
                    where, target, Severity.HIGH, 6, Confidence.HIGH)

        incidents.sort(key=lambda inc: inc.distance_km if inc.distance_km is not None else 99)
        return IncidentSnapshot(
            observed_at=now,
            incidents=incidents,
            radius_km=self._radius_km,
            covered_categories=COVERED,
        )
