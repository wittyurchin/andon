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

# A road-bound item within this distance of a corridor counts as on it.
ON_APPROACH_M = 40.0
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
) -> SpatialContext:
    """Relevance of road-bound evidence (traffic, incidents).

    ``approach_id`` is set when the provider sampled a specific corridor for us;
    otherwise corridor membership is found geometrically.
    """
    if location is None:
        return SpatialContext(basis="no location reported — spatial relevance unknown")
    distance = haversine_km(restaurant, location) * 1000
    bearing = bearing_label(restaurant, location) if distance > 1 else None

    nearest_id, nearest_gap = None, None
    on_ids: list[str] = []
    if graph is not None:
        for segment in graph.segments:
            gap = point_to_polyline_m(location, segment.geometry)
            if gap is None:
                continue
            if nearest_gap is None or gap < nearest_gap:
                nearest_id, nearest_gap = segment.id, gap
            if gap <= ON_APPROACH_M and segment.approach_id and segment.approach_id not in on_ids:
                on_ids.append(segment.approach_id)

    if approach_id and approach_id not in on_ids:
        on_ids.insert(0, approach_id)

    on_approach = bool(on_ids)
    knots = ROAD_ON_APPROACH if on_approach else ROAD_OFF_APPROACH
    relevance = _interp(distance, knots, 0.05 if on_approach else 0.02)
    basis = (
        f"on access corridor {', '.join(on_ids)}" if on_approach
        else f"{distance:.0f} m from the restaurant, not on a mapped access corridor"
    )
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
        basis=basis,
    )
