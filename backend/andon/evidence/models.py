"""Canonical evidence model.

Every provider's output is reduced to these types before it is stored or
shown. They are the contract the situation/reasoning layer consumes — it never
sees a provider client or a vendor payload.

Three quantities are deliberately kept apart on every piece of evidence:

* ``confidence`` — how reliable the *measurement* is (0–1);
* ``spatial.relevance`` — how applicable it is *to this restaurant* (0–1);
* ``freshness_seconds`` / ``stale`` — how *current* it is.

A consumer combines them; the evidence layer never pre-blends them into one
number, because that would hide which of the three is the weak link.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..domain.enums import (
    Applicability,
    ChangeType,
    Freshness,
    HealthStatus,
    IncidentCategory,
    SourceType,
)
from ..domain.signals import GeoPoint

ObservationKind = Literal["observation", "forecast"]


class SpatialContext(BaseModel):
    """Where a piece of evidence sits relative to the restaurant."""

    distance_m: float | None = None
    bearing: str | None = None
    applicability: Applicability = Applicability.UNKNOWN
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    # Access-graph relationship, for road-bound evidence.
    nearest_segment_id: str | None = None
    nearest_segment_distance_m: float | None = None
    approach_ids: list[str] = Field(default_factory=list)
    on_approach: bool = False
    # Access roads the item is near but not on (a parallel street, a
    # junction), nearest first, with the distance to the nearest. Listed
    # alongside the road; never counted as on it.
    near_approach_ids: list[str] = Field(default_factory=list)
    near_distance_m: float | None = None
    basis: str = ""  # one line explaining how relevance was derived


class Observation(BaseModel):
    """One measured or forecast value, with full provenance.

    ``kind`` is only ever "observation" or "forecast". Inferences are made by
    the situation layer and are never stored here.
    """

    id: str  # deterministic: re-fetching the same report yields the same id
    restaurant_id: str
    source_id: str
    source_name: str
    source_type: SourceType
    category: str  # e.g. weather.precipitation, traffic.flow, radar.reflectivity
    subject_id: str | None  # station id, approach id, grid cell, radar ring…
    kind: ObservationKind
    observed_at: datetime  # when the source says it was measured / issued
    # "source" if the provider stated observed_at; "received" if it did not
    # and observed_at is only our receipt time.
    observed_at_basis: Literal["source", "received"] = "source"
    valid_at: datetime | None = None  # the time a forecast is *for*
    received_at: datetime
    location: GeoPoint | None = None
    spatial: SpatialContext = Field(default_factory=SpatialContext)
    value: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    freshness_seconds: int | None = None  # computed when read, not stored
    stale: bool = False
    stale_after_seconds: int | None = None
    source_record_id: str | None = None
    raw_reference: str | None = None  # a bounded pointer, never a raw payload


class IncidentEvidence(BaseModel):
    """An incident as a time-varying record, never an overwritten snapshot."""

    id: str  # our stable identity: source + source record id (or content key)
    restaurant_id: str
    source_id: str
    source_name: str
    source_type: SourceType
    source_record_id: str | None
    incident_type: IncidentCategory
    status: Literal["active", "cleared"]
    description: str
    location: GeoPoint | None
    geometry: dict[str, Any] | None = None
    first_seen: datetime
    last_seen: datetime
    observed_at: datetime | None  # the source's own timestamp, if any
    cleared_at: datetime | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    spatial: SpatialContext = Field(default_factory=SpatialContext)
    attributes: dict[str, Any] = Field(default_factory=dict)
    freshness_seconds: int | None = None
    stale: bool = False


class SourceHealthRecord(BaseModel):
    source_id: str
    source_name: str
    source_kind: str
    source_type: SourceType
    restaurant_id: str
    status: HealthStatus
    checked_at: datetime
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    latency_ms: int | None = None
    response_age_seconds: int | None = None
    # Where response_age_seconds sits against this source's own freshness
    # policy (evidence/freshness.py), in the same thirds-of-window grading
    # used for SignalAssessment.freshness — one staleness system, not two.
    freshness_grade: Freshness = Freshness.UNKNOWN
    error: str | None = None
    licence_note: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ChangeEvent(BaseModel):
    id: str
    restaurant_id: str
    detected_at: datetime
    change_type: ChangeType
    category: str
    subject_id: str | None = None
    source_id: str | None = None
    summary: str
    previous: dict[str, Any] | None = None
    current: dict[str, Any] | None = None
    magnitude: float | None = None


# --------------------------------------------------------------------------
# Access graph
# --------------------------------------------------------------------------


class AccessNode(BaseModel):
    id: str
    restaurant_id: str
    kind: Literal["restaurant", "junction", "probe", "entry"]
    location: GeoPoint
    source_reference: str | None = None


class AccessSegment(BaseModel):
    id: str
    restaurant_id: str
    approach_id: str | None
    road_name: str | None
    road_class: str | None
    geometry: list[tuple[float, float]]  # (lon, lat)
    length_m: float
    bearing: str | None
    bearing_deg: float | None
    distance_m: float  # nearest point of the segment to the restaurant
    oneway: bool | None = None
    source_segment_reference: str | None = None  # e.g. "osm:way/123456"


class Approach(BaseModel):
    """A corridor riders use to reach or leave the restaurant."""

    id: str
    restaurant_id: str
    label: str
    road_name: str | None
    road_class: str | None
    bearing: str | None
    bearing_deg: float | None
    distance_m: float  # nearest point of the corridor to the restaurant
    length_m: float
    probe: GeoPoint  # where flow is sampled
    entry: GeoPoint | None  # outer end, for corridor travel times
    segment_ids: list[str] = Field(default_factory=list)
    derivation: str  # how this approach was produced
    is_approximation: bool = False  # True: NOT a mapped road


class AccessGraph(BaseModel):
    restaurant_id: str
    source: Literal["osm", "configured", "radial", "mock"]
    source_type: SourceType
    built_at: datetime
    radius_m: int
    derivation: str
    attribution: str | None = None
    approaches: list[Approach] = Field(default_factory=list)
    segments: list[AccessSegment] = Field(default_factory=list)
    nodes: list[AccessNode] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Bundle
# --------------------------------------------------------------------------


class RestaurantRecord(BaseModel):
    id: str
    name: str
    location: GeoPoint
    refresh_interval_seconds: int
    created_at: datetime
    updated_at: datetime
    last_refreshed_at: datetime | None = None


class Conflict(BaseModel):
    """Two or more current sources disagree about the same thing."""

    category: str
    subject: str
    summary: str
    spread: int  # difference in severity bands between the extremes
    members: list[dict[str, Any]]  # observation id, source, kind, band, relevance


class TrendSummary(BaseModel):
    category: str
    source_id: str
    subject_id: str | None
    direction: Literal["worsening", "improving", "steady"]
    window_minutes: int
    points: int
    first: dict[str, Any]
    last: dict[str, Any]
    kind: ObservationKind = "observation"


class RefreshSummary(BaseModel):
    refresh_id: str
    started_at: datetime
    completed_at: datetime
    providers_called: int
    providers_failed: int
    observations_new: int
    observations_seen: int
    incidents_active: int
    changes_detected: int


class EvidenceBundle(BaseModel):
    """The boundary between the data plane and anything that reasons over it."""

    restaurant: RestaurantRecord
    generated_at: datetime
    refresh: RefreshSummary | None = None
    observations: list[Observation] = Field(default_factory=list)
    forecasts: list[Observation] = Field(default_factory=list)
    incidents: list[IncidentEvidence] = Field(default_factory=list)
    source_health: list[SourceHealthRecord] = Field(default_factory=list)
    access_graph: AccessGraph | None = None
    changes: list[ChangeEvent] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    trends: list[TrendSummary] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    attributions: list[str] = Field(default_factory=list)
