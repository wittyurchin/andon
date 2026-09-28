"""Mappls corridor travel-time provider.

Mappls publishes no traffic flow or traffic event REST API (checked both auth
branches of github.com/mappls-api/mappls-rest-apis, 2026-09-27). What it does
publish is the Predictive Routing API, which returns a route's live-traffic
ETA. Routed along each access corridor into the restaurant, that is genuine,
documented traffic evidence — at corridor level rather than segment level.

Verified against documentation/mappls/rest-apis-main/predictive-routing-api.md:

* ``GET https://route.mappls.com/routev2/direction/route``;
* ``locations=lon,lat;lon,lat``, ``profile=driving``;
* ``speedTypes=traffic`` with ``date_time=0,""`` → ETA under live traffic;
* ``speedTypes=optimal`` → "ETA acc. to current time" — **not free-flow**;
* ``access_token`` query parameter carries the console key (current auth);
* response ``trip.summary.length`` (km) and ``trip.summary.time`` (s);
* 401 = key not allowed; **403 = daily/hourly limit reached** (not an auth
  failure, so it is reported as unavailable, not unauthorized).

Because ``optimal`` is not free-flow, no congestion index is computed from it.
The comparison is carried as ``reference_travel_time_s`` with
``reference_kind="mappls_optimal_eta"`` so it cannot be read as one.
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
from ...geo import bearing_label, haversine_km
from ..base import TrafficProvider
from ..errors import ProviderError, ProviderSchemaError, ProviderUnauthorized

ROUTE_URL = "https://route.mappls.com/routev2/direction/route"
REFERENCE_KIND = "mappls_optimal_eta"


class MapplsCorridorTrafficProvider(TrafficProvider):
    uses_key = True

    def __init__(
        self,
        access_token: str | None,
        compare_optimal: bool = True,
        timeout_s: float = 8.0,
    ) -> None:
        self._token = access_token
        self._compare_optimal = compare_optimal
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": "andon-situation-awareness/0.1"},
        )

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="mappls-route",
            name="Mappls corridor travel times",
            kind="traffic",
            mode="live",
            attribution="Mappls (MapmyIndia) Predictive Routing API",
            docs_url="https://github.com/mappls-api/mappls-rest-apis/tree/main/mappls-predictive-routing-api",
        )

    def precheck(self) -> tuple[HealthStatus, dict[str, Any]] | None:
        if not self._token:
            return HealthStatus.MISCONFIGURED, {
                "reason": "set ANDON_MAPPLS_ACCESS_TOKEN (static key from the Mappls console)",
                "requires": "credentials",
            }
        return None

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> TrafficSnapshot:
        corridors = [a for a in context.approaches if a.entry is not None]
        if not corridors:
            raise ProviderSchemaError("no access corridors with an entry point to route along")

        results = await asyncio.gather(
            *(self._corridor(point, c) for c in corridors), return_exceptions=True
        )
        probes = [r for r in results if isinstance(r, RoadProbe)]
        failed = [c.approach_id for c, r in zip(corridors, results, strict=True) if not isinstance(r, RoadProbe)]
        if not probes:
            first = next((r for r in results if isinstance(r, BaseException)), None)
            if isinstance(first, ProviderError):
                raise first
            raise RuntimeError(f"no corridor route succeeded: {first}")
        return TrafficSnapshot(
            observed_at=datetime.now(timezone.utc),
            probes=probes,
            failed_probes=failed,
        )

    async def _corridor(self, restaurant: GeoPoint, corridor: ApproachTarget) -> RoadProbe:
        locations = f"{corridor.entry.lon:.6f},{corridor.entry.lat:.6f};{restaurant.lon:.6f},{restaurant.lat:.6f}"
        live = await self._route(locations, "traffic")
        reference = await self._route(locations, "optimal") if self._compare_optimal else None
        return corridor_probe(restaurant, corridor, live, reference)

    async def _route(self, locations: str, speed_type: str) -> dict:
        params = {
            "locations": locations,
            "profile": "driving",
            "speedTypes": speed_type,
            "access_token": self._token,
        }
        if speed_type == "traffic":
            params["date_time"] = '0,""'
        response = await self._client.get(ROUTE_URL, params=params)
        if response.status_code == 401:
            # Mappls says why: e.g. ASSET_ACCESS_DENIED when the key is valid
            # but this API is not enabled for it in the console.
            try:
                body = response.json() or {}
            except ValueError:
                body = {}
            why = body.get("error_code") or body.get("error") or ""
            raise ProviderUnauthorized(
                f"Mappls rejected the key (401{': ' + why if why else ''})",
                http_status=401,
                provider_error=why or None,
            )
        if response.status_code == 403:
            # Documented as the daily/hourly limit — an outage of quota, not of trust.
            raise ProviderError("Mappls usage limit reached (403)", http_status=403, quota_exceeded=True)
        response.raise_for_status()
        return response.json() or {}


def corridor_probe(
    restaurant: GeoPoint, corridor: ApproachTarget, live: dict, reference: dict | None
) -> RoadProbe:
    summary = ((live.get("trip") or {}).get("summary")) or {}
    time_s = _float(summary.get("time"))
    length_km = _float(summary.get("length"))
    if time_s is None or length_km is None:
        raise ProviderSchemaError("route response lacks trip.summary.time/length")

    ref_time = None
    if reference is not None:
        ref_time = _float((((reference.get("trip") or {}).get("summary")) or {}).get("time"))

    return RoadProbe(
        label=corridor.label,
        bearing=corridor.bearing or bearing_label(restaurant, corridor.entry),
        distance_m=int(round(haversine_km(restaurant, corridor.entry) * 1000)),
        road_name=corridor.road_name,
        road_class=corridor.road_class,
        # Average speed along the corridor under live traffic.
        current_speed_kmh=round(length_km / time_s * 3600, 1) if time_s > 0 else None,
        free_flow_speed_kmh=None,  # Mappls publishes none; never inferred
        current_travel_time_s=time_s,
        free_flow_travel_time_s=None,
        reference_travel_time_s=ref_time,
        reference_kind=REFERENCE_KIND if ref_time is not None else None,
        length_m=round(length_km * 1000, 1),
        approach_id=corridor.approach_id,
        location=corridor.entry,
        measurement="corridor_eta",
    )


def _float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
