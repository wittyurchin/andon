"""REST API.

The UI talks only to these endpoints and only in normalized vocabulary — it has
no idea which weather or traffic vendor is behind them.

Two families:

* ``/api/situation`` — the report-level view the dashboard renders.
* ``/api/restaurants/{id}/...`` — the evidence layer: bundle, sources, changes,
  history, incidents, access graph. ``POST .../refresh`` polls providers and
  never calls the LLM.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..domain.signals import GeoPoint
from ..domain.situation import SituationSnapshot
from ..evidence.models import (
    AccessGraph,
    ChangeEvent,
    EvidenceBundle,
    IncidentEvidence,
    RestaurantRecord,
    SourceHealthRecord,
)
from ..geo import location_key
from ..service import SituationResponse, SituationService
from .schemas import HistoryBundle, HistoryResponse, RestaurantCreate, SourceDetail, StatusResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["situation"])

LatQuery = Annotated[float, Query(ge=-90, le=90, description="Restaurant latitude")]
LonQuery = Annotated[float, Query(ge=-180, le=180, description="Restaurant longitude")]


def get_service(request: Request) -> SituationService:
    return request.app.state.service


Service = Annotated[SituationService, Depends(get_service)]


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/status", response_model=StatusResponse, summary="Configured providers and LLM")
async def status(service: Service) -> StatusResponse:
    return StatusResponse.model_validate(service.status())


@router.get(
    "/situation",
    response_model=SituationResponse,
    summary="Current situation report (and its evidence) for a restaurant location",
)
async def situation(
    service: Service,
    lat: LatQuery,
    lon: LonQuery,
    name: Annotated[str, Query(max_length=120)] = "Restaurant",
    force: Annotated[bool, Query(description="Bypass provider caches and re-assess")] = False,
    poll: Annotated[
        bool,
        Query(description="Poll providers at all. False replays the last polled response "
                           "untouched — no provider contacted, cached or otherwise."),
    ] = True,
    refresh_interval_s: Annotated[int, Query(ge=30, le=86400)] = 300,
) -> SituationResponse:
    point = GeoPoint(lat=lat, lon=lon)
    result = await service.get_situation(
        name, point, force=force, poll=poll, refresh_interval_seconds=refresh_interval_s
    )
    log.info(
        "situation served",
        extra={
            "restaurant_id": result.restaurant_id,
            "location": location_key(point),
            "situation_level": result.situation.overall.level.value,
            "score": result.situation.overall.score,
            "report_reused": result.report.reused,
            "generator": result.report.generator,
            "forced": force,
            "polled": poll,
        },
    )
    return result


@router.get(
    "/history",
    response_model=HistoryResponse,
    summary="Recent situation snapshots for a location",
)
async def history(
    service: Service,
    lat: LatQuery,
    lon: LonQuery,
    minutes: Annotated[int, Query(ge=5, le=1440)] = 180,
) -> HistoryResponse:
    key = location_key(GeoPoint(lat=lat, lon=lon))
    snapshots: list[SituationSnapshot] = service.history.get(key, within_minutes=minutes)
    return HistoryResponse(
        location=key,
        window_minutes=minutes,
        snapshots=snapshots,
        note=service.history.summarize(snapshots),
        direction=service.history.direction(snapshots),
    )


# --------------------------------------------------------------------------
# Restaurants and their evidence
# --------------------------------------------------------------------------


def _restaurant(service: SituationService, restaurant_id: str) -> RestaurantRecord:
    record = service.store.get_restaurant(restaurant_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown restaurant {restaurant_id}")
    return record


@router.post("/restaurants", response_model=RestaurantRecord, status_code=201, tags=["evidence"],
             summary="Register a restaurant (idempotent per location)")
async def create_restaurant(service: Service, body: RestaurantCreate) -> RestaurantRecord:
    return service.evidence.register(
        body.restaurant_name,
        GeoPoint(lat=body.latitude, lon=body.longitude),
        body.refresh_interval_seconds,
    )


@router.get("/restaurants", response_model=list[RestaurantRecord], tags=["evidence"])
async def list_restaurants(service: Service) -> list[RestaurantRecord]:
    return service.store.list_restaurants()


@router.get("/restaurants/{restaurant_id}", response_model=RestaurantRecord, tags=["evidence"])
async def get_restaurant(service: Service, restaurant_id: str) -> RestaurantRecord:
    return _restaurant(service, restaurant_id)


@router.post("/restaurants/{restaurant_id}/refresh", response_model=EvidenceBundle, tags=["evidence"],
             summary="Poll providers, persist evidence, detect changes. Never calls the LLM.")
async def refresh_restaurant(
    service: Service,
    restaurant_id: str,
    force: Annotated[bool, Query(description="Bypass provider caches")] = False,
) -> EvidenceBundle:
    _restaurant(service, restaurant_id)
    outcome = await service.evidence.refresh(restaurant_id, force=force)
    return outcome.bundle


@router.get("/restaurants/{restaurant_id}/evidence", response_model=EvidenceBundle, tags=["evidence"],
            summary="The evidence bundle from the store (no provider calls unless refresh=true)")
async def restaurant_evidence(
    service: Service,
    restaurant_id: str,
    refresh: Annotated[bool, Query(description="Poll providers first")] = False,
) -> EvidenceBundle:
    _restaurant(service, restaurant_id)
    if refresh:
        return (await service.evidence.refresh(restaurant_id)).bundle
    return service.evidence.bundle(restaurant_id)


@router.get("/restaurants/{restaurant_id}/sources", response_model=list[SourceHealthRecord], tags=["evidence"])
async def restaurant_sources(service: Service, restaurant_id: str) -> list[SourceHealthRecord]:
    _restaurant(service, restaurant_id)
    return sorted(service.store.get_health(restaurant_id).values(), key=lambda h: (h.source_kind, h.source_id))


@router.get("/restaurants/{restaurant_id}/sources/{source_id}", response_model=SourceDetail, tags=["evidence"],
            summary="One source in full: health, provenance, readings, forecasts, incidents, history")
async def restaurant_source(
    service: Service,
    restaurant_id: str,
    source_id: str,
    minutes: Annotated[int, Query(ge=5, le=10080)] = 180,
) -> SourceDetail:
    _restaurant(service, restaurant_id)
    health = service.store.get_health(restaurant_id).get(source_id)
    ref = service.providers.describe(source_id)
    if health is None and ref is None:
        raise HTTPException(status_code=404, detail=f"unknown source {source_id}")
    bundle = service.evidence.bundle(restaurant_id)
    history = [
        *service.evidence.history(restaurant_id, minutes=minutes, source_id=source_id, kind="observation"),
        *service.evidence.history(restaurant_id, minutes=minutes, source_id=source_id, kind="forecast"),
    ]
    history.sort(key=lambda o: o.observed_at, reverse=True)
    return SourceDetail(
        restaurant_id=restaurant_id,
        source=ref,
        health=health,
        observations=[o for o in bundle.observations if o.source_id == source_id],
        forecasts=[o for o in bundle.forecasts if o.source_id == source_id],
        incidents=[i for i in service.store.get_incidents(restaurant_id) if i.source_id == source_id],
        history=history[:500],
        history_minutes=minutes,
    )


@router.get("/restaurants/{restaurant_id}/changes", response_model=list[ChangeEvent], tags=["evidence"])
async def restaurant_changes(
    service: Service,
    restaurant_id: str,
    minutes: Annotated[int, Query(ge=1, le=10080)] = 120,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ChangeEvent]:
    _restaurant(service, restaurant_id)
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return service.store.list_changes(restaurant_id, since=since, limit=limit)


@router.get("/restaurants/{restaurant_id}/history", response_model=HistoryBundle, tags=["evidence"])
async def restaurant_history(
    service: Service,
    restaurant_id: str,
    minutes: Annotated[int, Query(ge=5, le=10080)] = 180,
    category: str | None = None,
    source_id: str | None = None,
    subject_id: str | None = None,
    kind: Annotated[str | None, Query(pattern="^(observation|forecast)$")] = "observation",
) -> HistoryBundle:
    record = _restaurant(service, restaurant_id)
    return HistoryBundle(
        restaurant_id=restaurant_id,
        window_minutes=minutes,
        observations=service.evidence.history(
            restaurant_id, minutes=minutes, category=category, source_id=source_id,
            subject_id=subject_id, kind=kind,
        ),
        situation=service.history.get(location_key(record.location), within_minutes=minutes),
    )


@router.get("/restaurants/{restaurant_id}/incidents", response_model=list[IncidentEvidence], tags=["evidence"])
async def restaurant_incidents(
    service: Service,
    restaurant_id: str,
    status: Annotated[str | None, Query(pattern="^(active|cleared)$")] = None,
) -> list[IncidentEvidence]:
    _restaurant(service, restaurant_id)
    return service.store.get_incidents(restaurant_id, status)


@router.get("/restaurants/{restaurant_id}/access-graph", response_model=AccessGraph, tags=["evidence"])
async def restaurant_access_graph(
    service: Service,
    restaurant_id: str,
    rebuild: Annotated[bool, Query(description="Rebuild from road data now")] = False,
) -> AccessGraph:
    record = _restaurant(service, restaurant_id)
    return await service.evidence.ensure_graph(record, rebuild=rebuild)


@router.get("/restaurants/{restaurant_id}/situation", response_model=SituationResponse, tags=["situation"])
async def restaurant_situation(
    service: Service,
    restaurant_id: str,
    force: bool = False,
    poll: bool = True,
) -> SituationResponse:
    _restaurant(service, restaurant_id)
    return await service.situation_for(restaurant_id, force=force, poll=poll)
