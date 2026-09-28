"""Restaurant Access Graph: which roads actually lead to this kitchen.

Replaces "five radial points" with corridors built from the real road network:

    restaurant → nearby roads (OpenStreetMap) → corridors → segments → probes

Derivation (deterministic, documented, deliberately simple):

1. Fetch drivable roads within ``radius_m`` from Overpass (OSM, ODbL).
2. The nearest road within 150 m is the restaurant's **frontage**.
3. Major roads (trunk → tertiary, plus unclassified) are grouped by **name**
   (or ref) and split by the **compass sector** they extend into from the
   restaurant — "27th Main Road · north" and "· south" are separate corridors,
   because riders can be stuck on one side and clear on the other.
4. Corridors are ranked by road class, then proximity; the top N are kept.
5. Each corridor gets a **probe** (a vertex ~250 m out, where flow is sampled)
   and an **entry** (its far end, for corridor travel times).

This is a grouping of mapped roads, not a routing analysis, and it is labelled
as such. When Overpass is unavailable (the public instance does time out —
HTTP 504 was observed on 2026-09-27) the fallback is operator-configured
points or radial points, and every approach built that way carries
``is_approximation=True`` and says so in its label.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import httpx

from ..domain.enums import SourceType
from ..domain.signals import ApproachTarget, GeoPoint
from ..geo import (
    bearing_degrees,
    compass,
    haversine_km,
    offset,
    point_along,
    point_to_polyline_m,
    polyline_length_m,
)
from .models import AccessGraph, AccessNode, AccessSegment, Approach

log = logging.getLogger(__name__)

OSM_ATTRIBUTION = "© OpenStreetMap contributors, ODbL (https://www.openstreetmap.org/copyright)"

DRIVABLE = (
    "trunk|primary|secondary|tertiary|trunk_link|primary_link|secondary_link|"
    "tertiary_link|unclassified|residential|living_street"
)
MAJOR_CLASSES = {
    "trunk": 6, "primary": 5, "secondary": 4, "tertiary": 3,
    "trunk_link": 4, "primary_link": 4, "secondary_link": 3, "tertiary_link": 2,
    "unclassified": 2,
}
FRONTAGE_MAX_M = 150.0
PROBE_TARGET_M = 250.0
# Shorter groups are junction stubs, not corridors a rider travels along.
MIN_CORRIDOR_M = 150.0
SECTORS = (("north", 0.0), ("east", 90.0), ("south", 180.0), ("west", 270.0))


def overpass_query(point: GeoPoint, radius_m: int) -> str:
    return (
        "[out:json][timeout:60];"
        f'way(around:{radius_m},{point.lat:.6f},{point.lon:.6f})["highway"~"^({DRIVABLE})$"];'
        "out body geom;"
    )


def _sector(deg: float) -> str:
    for name, centre in SECTORS:
        diff = abs((deg - centre + 180) % 360 - 180)
        if diff <= 45:
            return name
    return "north"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "road"


def _id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def build_from_osm(
    restaurant_id: str,
    point: GeoPoint,
    payload: dict[str, Any],
    *,
    radius_m: int,
    max_approaches: int,
    now: datetime | None = None,
) -> AccessGraph:
    """Pure: Overpass JSON → access graph. Tested against a captured response."""
    now = now or datetime.now(timezone.utc)
    ways = [e for e in payload.get("elements", []) if e.get("type") == "way" and e.get("geometry")]

    segments: dict[str, AccessSegment] = {}
    way_nodes: dict[str, list[int]] = {}
    for way in ways:
        tags = way.get("tags") or {}
        geometry = [(float(g["lon"]), float(g["lat"])) for g in way["geometry"]]
        if len(geometry) < 2:
            continue
        # A way's direction from the restaurant is where it *leads*: its far
        # end. (The midpoint is noisy for short ways sitting on a junction.)
        far = max(geometry, key=lambda c: haversine_km(point, GeoPoint(lat=c[1], lon=c[0])))
        deg = bearing_degrees(point, GeoPoint(lat=far[1], lon=far[0]))
        seg_id = f"osm-way-{way['id']}"
        oneway = tags.get("oneway")
        segments[seg_id] = AccessSegment(
            id=seg_id,
            restaurant_id=restaurant_id,
            approach_id=None,
            road_name=tags.get("name") or tags.get("ref"),
            road_class=tags.get("highway"),
            geometry=geometry,
            length_m=round(polyline_length_m(geometry), 1),
            bearing=compass(deg),
            bearing_deg=round(deg, 1),
            distance_m=round(point_to_polyline_m(point, geometry) or 0.0, 1),
            oneway=None if oneway is None else oneway in ("yes", "1", "true", "-1"),
            source_segment_reference=f"osm:way/{way['id']}",
        )
        way_nodes[seg_id] = list(way.get("nodes") or [])

    if not segments:
        raise ValueError("no drivable roads found around the restaurant")

    approaches: list[Approach] = []

    # 1. Frontage: the road the kitchen actually sits on.
    frontage = min(segments.values(), key=lambda s: s.distance_m)
    used: set[str] = set()
    if frontage.distance_m <= FRONTAGE_MAX_M:
        name = frontage.road_name or f"Unnamed {frontage.road_class} road"
        approaches.append(
            _approach(
                restaurant_id, point, f"frontage:{frontage.id}", f"Frontage · {name}",
                frontage.road_name, frontage.road_class, [frontage],
                "osm_frontage: nearest mapped road to the restaurant",
            )
        )
        used.add(frontage.id)

    # 2. Corridors: major roads grouped by name and sector.
    groups: dict[tuple[str, str], list[AccessSegment]] = defaultdict(list)
    for segment in segments.values():
        if segment.road_class not in MAJOR_CLASSES or segment.id in used:
            continue
        key = segment.road_name or f"unnamed {segment.road_class} {segment.id}"
        groups[(key, _sector(segment.bearing_deg or 0.0))].append(segment)

    ranked = sorted(
        groups.items(),
        key=lambda item: (
            -max(MAJOR_CLASSES[s.road_class] for s in item[1]),
            min(s.distance_m for s in item[1]),
        ),
    )
    for (road_key, sector), members in ranked:
        if len(approaches) >= max_approaches:
            break
        if sum(s.length_m for s in members) < MIN_CORRIDOR_M:
            continue
        best = max(members, key=lambda s: MAJOR_CLASSES[s.road_class])
        name = members[0].road_name
        label = f"{name or f'Unnamed {best.road_class} road'} · {sector}"
        approaches.append(
            _approach(
                restaurant_id, point, f"{_slug(road_key)}:{sector}", label, name,
                best.road_class, sorted(members, key=lambda s: s.distance_m),
                "osm_named_road_sector: mapped road grouped by name and compass sector",
            )
        )

    # Only segments that belong to a corridor are kept.
    kept: list[AccessSegment] = []
    for approach in approaches:
        for seg_id in approach.segment_ids:
            kept.append(segments[seg_id].model_copy(update={"approach_id": approach.id}))

    nodes = [AccessNode(id="restaurant", restaurant_id=restaurant_id, kind="restaurant", location=point)]
    for approach in approaches:
        nodes.append(AccessNode(id=f"{approach.id}:probe", restaurant_id=restaurant_id, kind="probe", location=approach.probe))
        if approach.entry:
            nodes.append(AccessNode(id=f"{approach.id}:entry", restaurant_id=restaurant_id, kind="entry", location=approach.entry))
    for segment in kept:
        ids = way_nodes.get(segment.id) or []
        for node_id, (lon, lat) in ((ids[0] if ids else None, segment.geometry[0]),
                                   (ids[-1] if ids else None, segment.geometry[-1])):
            nid = f"osm-node-{node_id}" if node_id else f"{segment.id}:end:{lon:.5f},{lat:.5f}"
            if all(n.id != nid for n in nodes):
                nodes.append(AccessNode(
                    id=nid, restaurant_id=restaurant_id, kind="junction",
                    location=GeoPoint(lat=lat, lon=lon),
                    source_reference=f"osm:node/{node_id}" if node_id else None,
                ))

    return AccessGraph(
        restaurant_id=restaurant_id,
        source="osm",
        source_type=SourceType.DERIVED,
        built_at=now,
        radius_m=radius_m,
        derivation=(
            f"Derived from OpenStreetMap roads within {radius_m} m: frontage plus major roads "
            "grouped by name and compass sector. A grouping of mapped roads, not a routing analysis."
        ),
        attribution=OSM_ATTRIBUTION,
        approaches=approaches,
        segments=kept,
        nodes=nodes,
    )


def _approach(
    restaurant_id: str,
    point: GeoPoint,
    key: str,
    label: str,
    road_name: str | None,
    road_class: str | None,
    members: list[AccessSegment],
    derivation: str,
) -> Approach:
    vertices = [v for s in members for v in s.geometry]
    distances = [(haversine_km(point, GeoPoint(lat=lat, lon=lon)) * 1000, (lon, lat)) for lon, lat in vertices]
    probe = point_along(vertices, point, PROBE_TARGET_M)
    far = max(distances, key=lambda d: d[0])[1]
    entry = GeoPoint(lat=far[1], lon=far[0])
    deg = bearing_degrees(point, probe) if haversine_km(point, probe) > 0.001 else None
    return Approach(
        id=f"{restaurant_id}:{key}",
        restaurant_id=restaurant_id,
        label=label,
        road_name=road_name,
        road_class=road_class,
        bearing=compass(deg) if deg is not None else None,
        bearing_deg=round(deg, 1) if deg is not None else None,
        distance_m=round(min(s.distance_m for s in members), 1),
        length_m=round(sum(s.length_m for s in members), 1),
        probe=probe,
        entry=entry,
        segment_ids=[s.id for s in members],
        derivation=derivation,
        is_approximation=False,
    )


def build_radial(
    restaurant_id: str, point: GeoPoint, radius_m: int, reason: str, now: datetime | None = None
) -> AccessGraph:
    """Fallback when no road data is available. Explicitly NOT roads."""
    now = now or datetime.now(timezone.utc)
    distance = min(radius_m, 600)
    approaches = []
    for sector, deg in SECTORS:
        probe = offset(point, deg, distance)
        approaches.append(Approach(
            id=f"{restaurant_id}:radial-{sector}",
            restaurant_id=restaurant_id,
            label=f"{distance} m {sector} (radial point, not a mapped road)",
            road_name=None,
            road_class=None,
            bearing=compass(deg),
            bearing_deg=deg,
            distance_m=float(distance),
            length_m=0.0,
            probe=probe,
            entry=probe,
            derivation="radial_approximation: a point at fixed distance and bearing — not a road",
            is_approximation=True,
        ))
    return AccessGraph(
        restaurant_id=restaurant_id,
        source="radial",
        source_type=SourceType.DERIVED,
        built_at=now,
        radius_m=radius_m,
        derivation="Radial approximation — no road network available. These are not road approaches.",
        approaches=approaches,
        nodes=[AccessNode(id="restaurant", restaurant_id=restaurant_id, kind="restaurant", location=point)],
        notes=[reason],
    )


def build_configured(
    restaurant_id: str, point: GeoPoint, points: list[dict], radius_m: int, now: datetime | None = None
) -> AccessGraph:
    now = now or datetime.now(timezone.utc)
    approaches = []
    for index, item in enumerate(points):
        probe = GeoPoint(lat=float(item["lat"]), lon=float(item["lon"]))
        deg = bearing_degrees(point, probe)
        approaches.append(Approach(
            id=f"{restaurant_id}:configured-{index}",
            restaurant_id=restaurant_id,
            label=f"{item.get('label') or f'Configured point {index + 1}'} (configured approximation)",
            road_name=item.get("road_name"),
            road_class=item.get("road_class"),
            bearing=compass(deg),
            bearing_deg=round(deg, 1),
            distance_m=round(haversine_km(point, probe) * 1000, 1),
            length_m=0.0,
            probe=probe,
            entry=probe,
            derivation="configured_approximation: operator-entered point, not derived from road data",
            is_approximation=True,
        ))
    return AccessGraph(
        restaurant_id=restaurant_id,
        source="configured",
        source_type=SourceType.CONFIGURED,
        built_at=now,
        radius_m=radius_m,
        derivation="Operator-configured approach points (ANDON_ACCESS_POINTS). Approximations, not mapped roads.",
        approaches=approaches,
        nodes=[AccessNode(id="restaurant", restaurant_id=restaurant_id, kind="restaurant", location=point)],
    )


def build_mock(restaurant_id: str, point: GeoPoint, radius_m: int, now: datetime | None = None) -> AccessGraph:
    """Deterministic synthetic network for offline development. Labelled mock."""
    now = now or datetime.now(timezone.utc)
    specs = (
        ("north", 5.0, "secondary"),
        ("east", 95.0, "tertiary"),
        ("south", 182.0, "secondary"),
        ("west", 268.0, "residential"),
    )
    approaches, segments = [], []
    for sector, deg, road_class in specs:
        name = f"Mock {sector.title()} Road"
        start, end = offset(point, deg, 60), offset(point, deg, min(radius_m, 700))
        geometry = [(start.lon, start.lat), (end.lon, end.lat)]
        seg_id = f"mock-seg-{sector}"
        approach_id = f"{restaurant_id}:mock-{sector}"
        segments.append(AccessSegment(
            id=seg_id, restaurant_id=restaurant_id, approach_id=approach_id, road_name=name,
            road_class=road_class, geometry=geometry, length_m=round(polyline_length_m(geometry), 1),
            bearing=compass(deg), bearing_deg=deg, distance_m=60.0, oneway=False,
            source_segment_reference=f"mock:{sector}",
        ))
        approaches.append(Approach(
            id=approach_id, restaurant_id=restaurant_id, label=f"{name} · {sector} (simulated road)",
            road_name=name, road_class=road_class, bearing=compass(deg), bearing_deg=deg,
            distance_m=60.0, length_m=segments[-1].length_m, probe=offset(point, deg, 250),
            entry=end, segment_ids=[seg_id], derivation="mock: synthetic road for development",
        ))
    return AccessGraph(
        restaurant_id=restaurant_id,
        source="mock",
        source_type=SourceType.MOCK,
        built_at=now,
        radius_m=radius_m,
        derivation="Simulated road network for mock mode — not real roads.",
        approaches=approaches,
        segments=segments,
        nodes=[AccessNode(id="restaurant", restaurant_id=restaurant_id, kind="restaurant", location=point)],
    )


def to_targets(graph: AccessGraph) -> tuple[ApproachTarget, ...]:
    by_approach: dict[str, list[AccessSegment]] = defaultdict(list)
    for segment in graph.segments:
        if segment.approach_id:
            by_approach[segment.approach_id].append(segment)
    return tuple(
        ApproachTarget(
            approach_id=a.id,
            label=a.label,
            probe=a.probe,
            entry=a.entry,
            road_name=a.road_name,
            road_class=a.road_class,
            bearing=a.bearing,
            distance_m=a.distance_m,
            segment_refs=tuple(
                s.source_segment_reference for s in by_approach[a.id] if s.source_segment_reference
            ),
            geometry=tuple(tuple(s.geometry) for s in by_approach[a.id]),
            is_approximation=a.is_approximation,
        )
        for a in graph.approaches
    )


class AccessGraphBuilder:
    """Builds, caches and refreshes access graphs according to configuration."""

    # How long a fallback (radial) graph is kept before OSM is tried again.
    FALLBACK_RETRY_S = 1800

    def __init__(
        self,
        source: str,
        radius_m: int,
        max_approaches: int,
        max_age_days: int,
        overpass_endpoints: list[str],
        configured_points: list[dict],
        timeout_s: float = 30.0,
    ) -> None:
        self._source = source
        self._radius_m = radius_m
        self._max_approaches = max_approaches
        self._max_age_s = max_age_days * 86400
        self._endpoints = overpass_endpoints
        self._configured = configured_points
        self._timeout_s = timeout_s

    def needs_rebuild(self, graph: AccessGraph | None, now: datetime) -> bool:
        if graph is None:
            return True
        # Simulated roads are never kept once real roads are wanted.
        if graph.source == "mock" and self._source != "mock":
            return True
        age = (now - graph.built_at).total_seconds()
        if graph.source == "radial" and self._source in ("auto", "osm"):
            return age >= self.FALLBACK_RETRY_S
        return age >= self._max_age_s

    async def build(self, restaurant_id: str, point: GeoPoint) -> AccessGraph:
        if self._source == "mock":
            return build_mock(restaurant_id, point, self._radius_m)
        if self._source == "configured":
            if not self._configured:
                return build_radial(restaurant_id, point, self._radius_m,
                                    "ANDON_ACCESS_GRAPH_SOURCE=configured but ANDON_ACCESS_POINTS is empty")
            return build_configured(restaurant_id, point, self._configured, self._radius_m)

        errors: list[str] = []
        for endpoint in self._endpoints:
            try:
                payload = await self._overpass(endpoint, point)
                return build_from_osm(
                    restaurant_id, point, payload,
                    radius_m=self._radius_m, max_approaches=self._max_approaches,
                )
            except Exception as exc:  # noqa: BLE001 - try the next endpoint
                errors.append(f"{endpoint}: {type(exc).__name__}: {exc}"[:200])
                log.warning("access graph: overpass failed", extra={"endpoint": endpoint, "error": errors[-1]})

        reason = "OpenStreetMap road data unavailable (" + "; ".join(errors) + ")"
        if self._configured:
            graph = build_configured(restaurant_id, point, self._configured, self._radius_m)
            graph.notes.append(reason)
            return graph
        return build_radial(restaurant_id, point, self._radius_m, reason)

    async def _overpass(self, endpoint: str, point: GeoPoint) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self._timeout_s,
            headers={"User-Agent": "andon-situation-awareness/0.1"},
        ) as client:
            response = await client.post(endpoint, data={"data": overpass_query(point, self._radius_m)})
            response.raise_for_status()
            return response.json()
