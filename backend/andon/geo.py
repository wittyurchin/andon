"""Small geospatial helpers. No dependencies beyond the standard library."""

from __future__ import annotations

import math

from .domain.signals import GeoPoint

EARTH_RADIUS_M = 6_371_000.0

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def offset(point: GeoPoint, bearing_deg: float, distance_m: float) -> GeoPoint:
    """Point at ``distance_m`` from ``point`` along ``bearing_deg`` (0 = north)."""
    lat_rad = math.radians(point.lat)
    d_lat = (distance_m * math.cos(math.radians(bearing_deg))) / EARTH_RADIUS_M
    d_lon = (distance_m * math.sin(math.radians(bearing_deg))) / (
        EARTH_RADIUS_M * max(math.cos(lat_rad), 1e-6)
    )
    return GeoPoint(
        lat=round(point.lat + math.degrees(d_lat), 6),
        lon=round(point.lon + math.degrees(d_lon), 6),
    )


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M / 1000.0 * math.asin(min(1.0, math.sqrt(h)))


def bearing_label(origin: GeoPoint, target: GeoPoint) -> str:
    lat1, lat2 = math.radians(origin.lat), math.radians(target.lat)
    dlon = math.radians(target.lon - origin.lon)
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    deg = (math.degrees(math.atan2(y, x)) + 360) % 360
    return COMPASS[round(deg / 45) % 8]


def bbox(center: GeoPoint, radius_km: float) -> tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat) — the order most traffic APIs want."""
    d_lat = radius_km / 111.0
    d_lon = radius_km / (111.0 * max(math.cos(math.radians(center.lat)), 1e-6))
    return (
        round(center.lon - d_lon, 6),
        round(center.lat - d_lat, 6),
        round(center.lon + d_lon, 6),
        round(center.lat + d_lat, 6),
    )


def bearing_degrees(origin: GeoPoint, target: GeoPoint) -> float:
    lat1, lat2 = math.radians(origin.lat), math.radians(target.lat)
    dlon = math.radians(target.lon - origin.lon)
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def compass(degrees: float) -> str:
    return COMPASS[round(degrees / 45) % 8]


def _local_xy(origin: GeoPoint, lon: float, lat: float) -> tuple[float, float]:
    """Equirectangular metres from ``origin`` — accurate at the km scale we use."""
    x = math.radians(lon - origin.lon) * EARTH_RADIUS_M * math.cos(math.radians(origin.lat))
    y = math.radians(lat - origin.lat) * EARTH_RADIUS_M
    return x, y


def point_to_polyline_m(point: GeoPoint, line: list[tuple[float, float]] | tuple) -> float | None:
    """Shortest distance in metres from ``point`` to a polyline of (lon, lat)."""
    if not line:
        return None
    if len(line) == 1:
        return haversine_km(point, GeoPoint(lat=line[0][1], lon=line[0][0])) * 1000
    best = math.inf
    for (lon1, lat1), (lon2, lat2) in zip(line, line[1:], strict=False):
        ax, ay = _local_xy(point, lon1, lat1)
        bx, by = _local_xy(point, lon2, lat2)
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        t = 0.0 if length_sq == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def polyline_length_m(line: list[tuple[float, float]] | tuple) -> float:
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(line, line[1:], strict=False):
        total += haversine_km(GeoPoint(lat=lat1, lon=lon1), GeoPoint(lat=lat2, lon=lon2)) * 1000
    return total


def point_along(line: list[tuple[float, float]] | tuple, origin: GeoPoint, target_m: float) -> GeoPoint:
    """Vertex of ``line`` whose distance from ``origin`` is closest to ``target_m``."""
    best = min(
        line,
        key=lambda c: abs(haversine_km(origin, GeoPoint(lat=c[1], lon=c[0])) * 1000 - target_m),
    )
    return GeoPoint(lat=best[1], lon=best[0])


def location_key(point: GeoPoint, precision: int = 4) -> str:
    """Grid key used for caching and history (~11 m at 4 decimal places)."""
    return f"{point.lat:.{precision}f},{point.lon:.{precision}f}"
