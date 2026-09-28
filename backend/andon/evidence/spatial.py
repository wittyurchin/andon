"""Spatial relevance: how much a piece of evidence says about *this* restaurant.

The rule this module exists to enforce: a reading somewhere else is not a
reading here. "Heavy rain at the airport 33 km away" stays exactly that — its
distance is kept, its applicability is REGIONAL and its relevance is low. No
code path turns it into "heavy rain at the restaurant".

Two phenomena behave differently:

* **Area** phenomena (rain, visibility, radar echo) decay smoothly with
  distance, because weather is spatially correlated but patchy.
* **Road-bound** phenomena (traffic, incidents) matter if they sit *on an
  access corridor*, far more than their straight-line distance suggests.

All thresholds are engineering conventions, stated here and nowhere else.
"""

from __future__ import annotations

import math

from ..domain.enums import Applicability
from ..domain.signals import GeoPoint
from ..geo import bearing_label, haversine_km, point_to_polyline_m
from .models import AccessGraph, SpatialContext

APPLICABILITY_BANDS: tuple[tuple[float, Applicability], ...] = (
    (150.0, Applicability.AT_SITE),
    (1_000.0, Applicability.LOCAL),
    (5_000.0, Applicability.NEARBY),
    (30_000.0, Applicability.REGIONAL),
)

# (distance m, relevance) knots, linearly interpolated.
AREA_RELEVANCE: tuple[tuple[float, float], ...] = (
    (0.0, 1.0),
    (1_000.0, 1.0),
    (5_000.0, 0.8),
    (10_000.0, 0.6),
    (30_000.0, 0.3),
    (60_000.0, 0.1),
)
AREA_RELEVANCE_FLOOR = 0.05

# Whether a road-bound item is ON an access road, or merely near one.
#
# Measured on the 95 live TomTom incidents around HSR Layout on 2026-09-28:
# incidents on Outer Ring Road sat 1 to 12 m from the OpenStreetMap line
# (two vendors draw the same road slightly differently), while a closure on
# 13th Cross Road, a street running parallel to it, sat 34 to 37 m away
# along its whole length. The previous single 40 m radius counted that
# closure as being on Outer Ring Road.
ON_ROAD_M = 20.0
# Close enough to list under an access road, but not on it: a parallel
# street, a junction, a service road. Never raises the road's severity.
NEAR_ROAD_M = 60.0
# For a line (a closure, a jam), how it runs matters, not one point: it is on
# the road only where it runs within ON_ROAD_M and roughly parallel to it. A
# street crossing at a junction fails the parallel test; a parallel street
# fails the distance test. The overlap minimum only absorbs map noise, and is
# short because a jam can run on the road mostly beyond the end of the mapped
# corridor (seen live: 45 m of overlap at the corridor's outer tip).
PARALLEL_DEG = 35.0
MIN_OVERLAP_M = 30.0
SAMPLE_STEP_M = 10.0
ROAD_OFF_APPROACH: tuple[tuple[float, float], ...] = (
    (0.0, 0.7),
    (300.0, 0.7),
    (1_000.0, 0.5),
    (2_500.0, 0.3),
    (5_000.0, 0.15),
    (10_000.0, 0.05),
)
ROAD_ON_APPROACH: tuple[tuple[float, float], ...] = (
    (0.0, 1.0),
    (1_000.0, 1.0),
    (2_500.0, 0.85),
    (5_000.0, 0.7),
)
# Evidence tied to a radial approximation instead of a mapped road is worth less.
APPROXIMATION_FACTOR = 0.6


def applicability(distance_m: float | None) -> Applicability:
    if distance_m is None:
        return Applicability.UNKNOWN
    for limit, value in APPLICABILITY_BANDS:
        if distance_m <= limit:
            return value
    return Applicability.DISTANT


def _interp(distance: float, knots: tuple[tuple[float, float], ...], floor: float) -> float:
    if distance <= knots[0][0]:
        return knots[0][1]
    for (d0, r0), (d1, r1) in zip(knots, knots[1:], strict=False):
        if distance <= d1:
            return round(r0 + (r1 - r0) * (distance - d0) / (d1 - d0), 3)
    return floor


def area(restaurant: GeoPoint, location: GeoPoint | None, resolution_m: float | None = None) -> SpatialContext:
    """Relevance of a point reading of an area phenomenon (rain, visibility)."""
    if location is None:
        return SpatialContext(basis="no location reported — spatial relevance unknown")
    distance = haversine_km(restaurant, location) * 1000
    bearing = bearing_label(restaurant, location) if distance > 1 else None

    if resolution_m and distance <= resolution_m / 2:
        # The cell that contains the restaurant.
        return SpatialContext(
            distance_m=round(distance, 1),
            bearing=bearing,
            applicability=Applicability.AT_SITE if resolution_m <= 1500 else Applicability.LOCAL,
            relevance=1.0 if resolution_m <= 1500 else 0.8,
            basis=f"cell containing the restaurant (resolution ~{resolution_m:.0f} m)",
        )

    relevance = _interp(distance, AREA_RELEVANCE, AREA_RELEVANCE_FLOOR)
    return SpatialContext(
        distance_m=round(distance, 1),
        bearing=bearing,
        applicability=applicability(distance),
        relevance=relevance,
        basis=f"point reading {distance / 1000:.1f} km away; area phenomenon, relevance decays with distance",
    )


def road(
    restaurant: GeoPoint,
    location: GeoPoint | None,
    graph: AccessGraph | None,
    *,
    approach_id: str | None = None,
    is_approximation: bool = False,
    line: list[tuple[float, float]] | None = None,
) -> SpatialContext:
    """Relevance of road-bound evidence (traffic, incidents).

    ``approach_id`` is set when the provider sampled a specific corridor for us;
    otherwise corridor membership is found geometrically. ``line`` is the
    item's own geometry as (lon, lat) points when the source reported one
    (e.g. the stretch a closure covers); a line is judged by how it runs, a
    point by how close it is.
    """
    if location is None:
        return SpatialContext(basis="no location reported — spatial relevance unknown")
    distance = haversine_km(restaurant, location) * 1000
    bearing = bearing_label(restaurant, location) if distance > 1 else None

    nearest_id, nearest_gap = None, None
    on_ids: list[str] = []
    near: dict[str, float] = {}
    if graph is not None:
        for segment in graph.segments:
            gap = point_to_polyline_m(location, segment.geometry)
            if gap is None:
                continue
            if nearest_gap is None or gap < nearest_gap:
                nearest_id, nearest_gap = segment.id, gap
            if line is None or len(line) < 2:
                if not segment.approach_id:
                    continue
                if gap <= ON_ROAD_M and segment.approach_id not in on_ids:
                    on_ids.append(segment.approach_id)
                elif gap <= NEAR_ROAD_M:
                    near[segment.approach_id] = min(gap, near.get(segment.approach_id, math.inf))
        if line is not None and len(line) >= 2:
            on_ids, near = _line_membership(restaurant, line, graph)

    if approach_id and approach_id not in on_ids:
        on_ids.insert(0, approach_id)
    near = {k: v for k, v in near.items() if k not in on_ids}
    near_ids = sorted(near, key=near.__getitem__)

    on_approach = bool(on_ids)
    knots = ROAD_ON_APPROACH if on_approach else ROAD_OFF_APPROACH
    relevance = _interp(distance, knots, 0.05 if on_approach else 0.02)
    if on_approach:
        basis = f"on access corridor {', '.join(on_ids)}"
    elif near_ids and near[near_ids[0]] <= ON_ROAD_M:
        basis = f"meets access corridor {near_ids[0]} (crosses or joins it) but does not run along it"
    elif near_ids:
        basis = f"{near[near_ids[0]]:.0f} m from access corridor {near_ids[0]}, near it but not on it"
    else:
        basis = f"{distance:.0f} m from the restaurant, not on a mapped access corridor"
    if is_approximation:
        relevance = round(relevance * APPROXIMATION_FACTOR, 3)
        basis += " (corridor is a radial approximation, not a mapped road)"

    return SpatialContext(
        distance_m=round(distance, 1),
        bearing=bearing,
        applicability=applicability(distance),
        relevance=relevance,
        nearest_segment_id=nearest_id,
        nearest_segment_distance_m=round(nearest_gap, 1) if nearest_gap is not None else None,
        approach_ids=on_ids,
        on_approach=on_approach,
        near_approach_ids=near_ids,
        near_distance_m=round(near[near_ids[0]], 1) if near_ids else None,
        basis=basis,
    )


def _xy(origin: GeoPoint, lon: float, lat: float) -> tuple[float, float]:
    """Equirectangular metres from ``origin``; accurate at the km scale used here."""
    x = math.radians(lon - origin.lon) * 6_371_000 * math.cos(math.radians(origin.lat))
    y = math.radians(lat - origin.lat) * 6_371_000
    return x, y


def _heading(ax: float, ay: float, bx: float, by: float) -> float:
    """Undirected heading in degrees, 0 to 180: a road has no direction here."""
    return math.degrees(math.atan2(bx - ax, by - ay)) % 180


def _samples(points: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """Points every SAMPLE_STEP_M along a polyline, each with its local heading."""
    out: list[tuple[float, float, float]] = []
    for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
        length = math.hypot(bx - ax, by - ay)
        if length == 0:
            continue
        heading = _heading(ax, ay, bx, by)
        steps = max(1, int(length // SAMPLE_STEP_M))
        for k in range(steps):
            t = (k + 0.5) / steps
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t, heading))
    return out


def _line_membership(
    origin: GeoPoint, line: list[tuple[float, float]], graph: AccessGraph
) -> tuple[list[str], dict[str, float]]:
    """Which access roads a line runs along, and which it only comes near."""
    points = [_xy(origin, lon, lat) for lon, lat in line]
    samples = _samples(points)
    if not samples:
        return [], {}
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:], strict=False))
    step = length / len(samples)
    needed = min(MIN_OVERLAP_M, 0.5 * length)

    pieces: dict[str, list[tuple[float, float, float, float]]] = {}
    for segment in graph.segments:
        if not segment.approach_id or len(segment.geometry) < 2:
            continue
        seg = [_xy(origin, lon, lat) for lon, lat in segment.geometry]
        pieces.setdefault(segment.approach_id, []).extend(
            (ax, ay, bx, by) for (ax, ay), (bx, by) in zip(seg, seg[1:], strict=False)
        )

    on_ids: list[str] = []
    near: dict[str, float] = {}
    for approach_id, road_pieces in pieces.items():
        xs = [v for p in road_pieces for v in (p[0], p[2])]
        ys = [v for p in road_pieces for v in (p[1], p[3])]
        box = (min(xs) - NEAR_ROAD_M, min(ys) - NEAR_ROAD_M, max(xs) + NEAR_ROAD_M, max(ys) + NEAR_ROAD_M)
        along = 0.0
        closest = math.inf
        for x, y, heading in samples:
            if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
                continue
            gap, piece_heading = _nearest_piece(x, y, road_pieces)
            closest = min(closest, gap)
            turn = abs(heading - piece_heading)
            if gap <= ON_ROAD_M and min(turn, 180 - turn) <= PARALLEL_DEG:
                along += step
        if along > 0 and along >= needed:
            on_ids.append(approach_id)
        elif closest <= NEAR_ROAD_M:
            near[approach_id] = closest
    return on_ids, near


def _nearest_piece(x: float, y: float, pieces: list[tuple[float, float, float, float]]) -> tuple[float, float]:
    best, best_heading = math.inf, 0.0
    for ax, ay, bx, by in pieces:
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length_sq))
        gap = math.hypot(x - ax - t * dx, y - ay - t * dy)
        if gap < best:
            best, best_heading = gap, _heading(ax, ay, bx, by)
    return best, best_heading
