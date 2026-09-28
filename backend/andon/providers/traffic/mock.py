"""Mock traffic providers.

``MockTrafficProvider`` samples the same approach corridors a real provider
would (from the access graph) and moves with the shared scenario: weather,
meal-time peaks, and — in the ``demo`` scenario — one approach steadily
worsening while the others hold. Road names come from the access graph when
it has them; the mock never invents a street name.

``MockOutageTrafficProvider`` is a second feed that is always down, so the
demo exercises provider independence and unavailable-source handling.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...domain.signals import (
    ApproachTarget,
    FetchContext,
    GeoPoint,
    RoadProbe,
    SourceRef,
    TrafficSnapshot,
)
from ...geo import bearing_label, haversine_km, location_key
from ..base import TrafficProvider
from ..errors import ProviderError
from ..scenario import ScenarioClock, pseudo_local_hour, rush_hour_factor, site_jitter
from .tomtom import radial_targets

SIMULATED = "Simulated data — not a real observation"

FREE_FLOW_BY_CLASS = {
    "trunk": 60.0,
    "primary": 50.0,
    "secondary": 42.0,
    "tertiary": 36.0,
    "unclassified": 30.0,
    "residential": 25.0,
}
DEFAULT_FREE_FLOW = 35.0
BACKFILL_MINUTES = (-60, -45, -30, -15)


class MockTrafficProvider(TrafficProvider):
    def __init__(self, clock: ScenarioClock, radius_m: int = 900) -> None:
        self._clock = clock
        self._radius_m = radius_m

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="mock-traffic",
            name=f"Mock traffic ({self._clock.scenario})",
            kind="traffic",
            mode="mock",
            attribution=SIMULATED,
        )

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> TrafficSnapshot:
        return self._snapshot(point, context, 0.0)

    async def backfill(self, point: GeoPoint, context: FetchContext) -> list[TrafficSnapshot]:
        """Simulated past readings, so a fresh demo already shows a trend.

        Only mocks implement this. Real history comes from real refreshes.
        """
        return [self._snapshot(point, context, m) for m in BACKFILL_MINUTES]

    def _snapshot(self, point: GeoPoint, context: FetchContext, offset_min: float) -> TrafficSnapshot:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        targets = context.approaches or radial_targets(point, self._radius_m)
        probes = [
            self._probe(point, target, index, offset_min)
            for index, target in enumerate(targets)
        ]
        return TrafficSnapshot(
            observed_at=now + timedelta(minutes=offset_min),
            probes=probes,
            radius_m=self._radius_m,
        )

    def _congestion(self, point: GeoPoint, index: int, target: ApproachTarget, offset_min: float) -> float:
        if self._clock.scenario == "demo":
            value = self._clock.demo_approach_congestion(index, offset_min)
        else:
            intensity = self._clock.intensity(offset_min)
            peak = rush_hour_factor(pseudo_local_hour(point.lon))
            value = 0.10 + 0.30 * peak + 0.40 * intensity
            value += site_jitter(location_key(point), f"traffic:{target.approach_id}", spread=0.14)
        # Capped short of a standstill: even gridlock creeps.
        return max(0.0, min(0.88, value))

    def _probe(self, point: GeoPoint, target: ApproachTarget, index: int, offset_min: float) -> RoadProbe:
        congestion = self._congestion(point, index, target, offset_min)
        free_flow = FREE_FLOW_BY_CLASS.get((target.road_class or "").lower(), DEFAULT_FREE_FLOW)
        current = round(free_flow * (1 - congestion), 1)
        return RoadProbe(
            label=target.label,
            bearing=target.bearing or bearing_label(point, target.probe),
            distance_m=int(round(haversine_km(point, target.probe) * 1000)),
            road_name=target.road_name,  # from the access graph, never invented
            road_class=target.road_class,
            current_speed_kmh=current,
            free_flow_speed_kmh=free_flow,
            # Travel time over a nominal 1 km of corridor.
            current_travel_time_s=round(3600.0 / max(current, 1.0), 1),
            free_flow_travel_time_s=round(3600.0 / free_flow, 1),
            road_closed=False,
            source_confidence=0.9,
            approach_id=target.approach_id,
            location=target.probe,
            source_segment_reference=target.segment_refs[0] if target.segment_refs else None,
        )


class MockOutageTrafficProvider(TrafficProvider):
    """A second traffic feed that is always down — for demos and tests."""

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="mock-traffic-feed-b",
            name="Mock traffic feed B (simulated outage)",
            kind="traffic",
            mode="mock",
            attribution=SIMULATED,
        )

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> TrafficSnapshot:
        raise ProviderError("simulated outage: feed B is not responding", simulated=True)
