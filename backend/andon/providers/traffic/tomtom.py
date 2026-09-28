"""TomTom Traffic Flow provider.

Samples each approach corridor from the restaurant access graph, so traffic is
measured on roads that lead to the kitchen — not at arbitrary radial points.

Verified against documentation/tomtom/flow-segment-data.html (2026-09-27):
``/traffic/services/4/flowSegmentData/{style}/{zoom}/{format}?key&point&unit``
returning ``flowSegmentData`` with ``frc``, ``currentSpeed``, ``freeFlowSpeed``,
``currentTravelTime``, ``freeFlowTravelTime``, ``confidence`` (0–1),
``roadClosure`` and ``coordinates.coordinate[]`` (the matched segment's shape).

TomTom returns the segment *nearest* the point we send. We measure how far that
segment is from our corridor and keep the figure: a large gap means TomTom
matched a different road, and the observation says so instead of silently
attributing it to the approach.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from ...domain.enums import HealthStatus
from ...domain.signals import (
    ApproachTarget,
    FetchContext,
    GeoPoint,
    RoadProbe,
    SourceRef,
    TrafficSnapshot,
)
from ...geo import bearing_label, haversine_km, offset, point_to_polyline_m
from ..base import TrafficProvider

# Zoom decides which roads TomTom will match: "Roads of lower importance are only
# visible on zoom levels with a higher value" (0..22). At zoom 10 HSR Layout
# probes all snapped to an FRC1 arterial ~1 km away; at 18 they match the
# corridor road itself within a few metres.
ENDPOINT = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/18/json"

FRC_LABELS = {
    "FRC0": "Motorway",
    "FRC1": "Major road",
    "FRC2": "Other major road",
    "FRC3": "Secondary road",
    "FRC4": "Local connecting road",
    "FRC5": "Local road",
    "FRC6": "Local road",
}


def radial_targets(point: GeoPoint, radius_m: int) -> tuple[ApproachTarget, ...]:
    """Used only when no access graph is supplied — and labelled as such."""
    targets = [ApproachTarget("here", "At the restaurant (point probe)", point, is_approximation=True)]
    for bearing, deg in (("N", 0.0), ("E", 90.0), ("S", 180.0), ("W", 270.0)):
        targets.append(
            ApproachTarget(
                f"radial-{bearing.lower()}",
                f"{radius_m} m {bearing} (radial point, not a mapped road)",
                offset(point, deg, radius_m),
                bearing=bearing,
                distance_m=radius_m,
                is_approximation=True,
            )
        )
    return tuple(targets)


class TomTomTrafficProvider(TrafficProvider):
    uses_key = True

    def __init__(self, api_key: str | None, radius_m: int = 900, timeout_s: float = 8.0) -> None:
        self._api_key = api_key
        self._radius_m = radius_m
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": "andon-situation-awareness/0.1"},
        )

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="tomtom-flow",
            name="TomTom Traffic Flow",
            kind="traffic",
            mode="live",
            attribution="TomTom Traffic API",
            docs_url="https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/traffic-flow/flow-segment-data",
        )

    def precheck(self) -> tuple[HealthStatus, dict[str, Any]] | None:
        if not self._api_key:
            return HealthStatus.MISCONFIGURED, {"reason": "set ANDON_TOMTOM_API_KEY", "requires": "credentials"}
        return None

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> TrafficSnapshot:
        targets = context.approaches or radial_targets(point, self._radius_m)
        results = await asyncio.gather(
            *(self._probe(point, target) for target in targets), return_exceptions=True
        )

        probes = [r for r in results if isinstance(r, RoadProbe)]
        failed = [t.approach_id for t, r in zip(targets, results, strict=True) if not isinstance(r, RoadProbe)]
        if not probes:
            first_error = next((r for r in results if isinstance(r, BaseException)), None)
            if isinstance(first_error, httpx.HTTPStatusError):
                raise first_error  # keeps 401/403 classifiable as unauthorized
            raise RuntimeError(f"no traffic probe succeeded: {first_error}")

        return TrafficSnapshot(
            observed_at=datetime.now(timezone.utc),
            probes=probes,
            radius_m=self._radius_m,
            failed_probes=failed,
        )

    async def _probe(self, restaurant: GeoPoint, target: ApproachTarget) -> RoadProbe:
        response = await self._client.get(
            ENDPOINT,
            params={
                "key": self._api_key,
                "point": f"{target.probe.lat:.5f},{target.probe.lon:.5f}",
                "unit": "KMPH",
            },
        )
        response.raise_for_status()
        return parse_segment((response.json() or {}).get("flowSegmentData") or {}, restaurant, target)

    async def aclose(self) -> None:
        await self._client.aclose()


def parse_segment(segment: dict, restaurant: GeoPoint, target: ApproachTarget) -> RoadProbe:
    if not segment:
        raise RuntimeError("empty flowSegmentData")

    shape = [
        (float(c["longitude"]), float(c["latitude"]))
        for c in ((segment.get("coordinates") or {}).get("coordinate") or [])
        if c.get("latitude") is not None and c.get("longitude") is not None
    ]
    # How far TomTom's matched segment is from the corridor we asked about.
    match = None
    if shape and target.geometry:
        match = min(
            (d for line in target.geometry if (d := _line_gap(line, shape)) is not None),
            default=None,
        )
    elif shape:
        match = point_to_polyline_m(target.probe, shape)

    frc = str(segment.get("frc") or "")
    distance = haversine_km(restaurant, target.probe) * 1000
    return RoadProbe(
        label=target.label,
        bearing=target.bearing or bearing_label(restaurant, target.probe),
        distance_m=int(round(distance)),
        road_name=target.road_name,  # the flow endpoint returns no street names
        road_class=target.road_class or FRC_LABELS.get(frc, frc or None),
        current_speed_kmh=_float(segment.get("currentSpeed")),
        free_flow_speed_kmh=_float(segment.get("freeFlowSpeed")),
        current_travel_time_s=_float(segment.get("currentTravelTime")),
        free_flow_travel_time_s=_float(segment.get("freeFlowTravelTime")),
        road_closed=bool(segment.get("roadClosure")),
        source_confidence=_float(segment.get("confidence")),
        approach_id=target.approach_id,
        location=target.probe,
        source_segment_reference=f"tomtom:frc={frc}" if frc else None,
        match_distance_m=round(match, 1) if match is not None else None,
    )


def _line_gap(corridor: tuple, shape: list[tuple[float, float]]) -> float | None:
    """Smallest distance from any vertex of TomTom's shape to our corridor."""
    gaps = [
        point_to_polyline_m(GeoPoint(lat=lat, lon=lon), list(corridor))
        for lon, lat in shape
    ]
    gaps = [g for g in gaps if g is not None]
    return min(gaps) if gaps else None


def _float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
