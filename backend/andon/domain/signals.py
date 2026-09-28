"""Provider-facing normalized payloads.

A provider's only job is to turn its vendor response into one of these shapes.
Nothing downstream of the provider layer knows that TomTom or Open-Meteo exist.

Every field is optional where a source may legitimately not have it. A provider
must leave a field as ``None`` rather than guess: "unknown" is a first-class
answer in a situation-awareness system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    Confidence,
    EvidenceKind,
    HealthStatus,
    IncidentCategory,
    Severity,
    SourceStatus,
)


class GeoPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class SourceRef(BaseModel):
    """Provenance for everything the system says."""

    id: str
    name: str
    kind: str  # weather | traffic | incidents | radar
    mode: str  # live | mock
    attribution: str | None = None
    docs_url: str | None = None
    # Licence or terms constraint worth surfacing, e.g. "non-commercial only".
    licence_note: str | None = None


# --------------------------------------------------------------------------
# Fetch context
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApproachTarget:
    """One access corridor a traffic provider should sample.

    Comes from the restaurant access graph, so traffic is measured on roads
    that actually lead to the kitchen rather than at arbitrary radial points.
    """

    approach_id: str
    label: str
    probe: GeoPoint  # a point on the corridor, where flow is sampled
    entry: GeoPoint | None = None  # the corridor's outer end, for route ETAs
    road_name: str | None = None
    road_class: str | None = None
    bearing: str | None = None
    distance_m: float | None = None
    segment_refs: tuple[str, ...] = ()
    # Polyline(s) of the corridor as (lon, lat) pairs, used to check that a
    # provider's matched segment is really the corridor we asked about.
    geometry: tuple[tuple[tuple[float, float], ...], ...] = ()
    is_approximation: bool = False


@dataclass(frozen=True, slots=True)
class FetchContext:
    """Everything a provider may need beyond the restaurant's coordinates."""

    restaurant_id: str | None = None
    approaches: tuple[ApproachTarget, ...] = field(default_factory=tuple)

    @property
    def cache_key(self) -> str:
        return ",".join(a.approach_id for a in self.approaches)


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------


class PrecipPoint(BaseModel):
    at: datetime
    precipitation_mm_h: float | None = None
    # Set instead of a rate by sources that only report a band (e.g. METAR).
    rain_intensity: Severity | None = None
    probability_pct: int | None = None
    visibility_m: float | None = None
    wind_gust_kmh: float | None = None
    kind: EvidenceKind = EvidenceKind.FORECAST
    # The source's own text for this report, when it has one (e.g. a METAR).
    raw_text: str | None = None
    condition: str | None = None


class WeatherAlert(BaseModel):
    event: str
    headline: str | None = None
    severity_hint: Severity | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class WeatherSnapshot(BaseModel):
    observed_at: datetime
    # "source" when the provider stated the observation time; "received" when
    # it did not and ``observed_at`` is merely when we got the data. The
    # evidence layer carries this through so nobody mistakes one for the other.
    observed_at_basis: str = "source"
    precipitation_mm_h: float | None = None
    # Some sources report intensity qualitatively rather than as a rate —
    # METAR's -RA / RA / +RA is a trained observer's judgement, not a gauge.
    # Inventing an mm/h figure from it would be fabrication, so the band is
    # carried directly and the rules engine prefers it over a derived one.
    rain_intensity: Severity | None = None
    # Station-based sources measure at a fixed point, which may be a long way
    # from the restaurant. Distance is evidence and must reach the UI.
    station_id: str | None = None
    station_name: str | None = None
    station_location: GeoPoint | None = None
    station_distance_km: float | None = None
    # Model-based sources compute a value for a grid cell, whose centre is not
    # necessarily the requested point (Open-Meteo reports it back).
    grid_location: GeoPoint | None = None
    probability_pct: int | None = None
    wind_speed_kmh: float | None = None
    wind_gust_kmh: float | None = None
    visibility_m: float | None = None
    temperature_c: float | None = None
    condition: str | None = None
    raw_text: str | None = None
    # Values a source reports whose units or semantics its documentation does
    # not define. Stored verbatim; never converted or classified.
    reported: dict[str, Any] = Field(default_factory=dict)
    # Oldest-to-newest observed blocks (past ~1h) and forecast blocks (next ~2h).
    recent: list[PrecipPoint] = Field(default_factory=list)
    outlook: list[PrecipPoint] = Field(default_factory=list)
    alerts: list[WeatherAlert] = Field(default_factory=list)
    # Set when the source has no severe-weather feed at all, so the UI can say
    # "not covered" instead of implying "nothing reported".
    alerts_supported: bool = True


# --------------------------------------------------------------------------
# Radar
# --------------------------------------------------------------------------


class RadarSample(BaseModel):
    """Reflectivity at one point of a radar composite."""

    location: GeoPoint
    distance_m: float
    bearing: str | None = None
    # A decoded colour can stand for a range of dBZ values (the published
    # table reuses colours at both ends), so both bounds are kept. None means
    # the pixel could not be decoded — which is not the same as "no echo".
    reflectivity_dbz: float | None = None
    reflectivity_dbz_max: float | None = None
    # False where the radar network has no coverage: absence of echo there is
    # absence of data, not absence of rain.
    covered: bool | None = None


class RadarSnapshot(BaseModel):
    observed_at: datetime  # the radar frame's time, not our fetch time
    resolution_m: float  # ground size of one sample; coarser means less local
    samples: list[RadarSample] = Field(default_factory=list)
    frame_reference: str | None = None
    undecodable_samples: int = 0

    def site_samples(self) -> list[RadarSample]:
        """The decoded cells touching the restaurant.

        A tile centred on the restaurant puts it on the corner shared by four
        cells, so "the cell over the restaurant" is really up to four. Picking
        one of them would be arbitrary; callers get all of them.
        """
        decoded = [s for s in self.samples if s.reflectivity_dbz is not None]
        if not decoded:
            return []
        nearest = min(s.distance_m for s in decoded)
        reach = max(nearest, 0.75 * self.resolution_m)
        return [s for s in decoded if s.distance_m <= reach]

    def site_strongest(self) -> RadarSample | None:
        cells = self.site_samples()
        return max(cells, key=lambda s: s.reflectivity_dbz) if cells else None


# --------------------------------------------------------------------------
# Traffic
# --------------------------------------------------------------------------


class RoadProbe(BaseModel):
    """One road segment or corridor sampled near the restaurant.

    Several probes on different approaches are what makes the system reason
    locally instead of assigning a single congestion figure to a whole city.
    """

    label: str
    bearing: str  # N, NE, E, ...
    distance_m: int
    road_name: str | None = None
    road_class: str | None = None
    current_speed_kmh: float | None = None
    free_flow_speed_kmh: float | None = None
    current_travel_time_s: float | None = None
    free_flow_travel_time_s: float | None = None
    road_closed: bool = False
    source_confidence: float | None = None
    # Access-graph linkage.
    approach_id: str | None = None
    location: GeoPoint | None = None
    source_segment_reference: str | None = None
    # Distance between the provider's matched segment and our corridor. Large
    # values mean the provider may have snapped to a different road.
    match_distance_m: float | None = None
    # A comparison travel time that is NOT free-flow (e.g. Mappls "optimal"
    # ETA). Kept separate so it can never be mistaken for one.
    reference_travel_time_s: float | None = None
    reference_kind: str | None = None
    length_m: float | None = None
    measurement: str = "segment_flow"  # segment_flow | corridor_eta

    @property
    def congestion(self) -> float | None:
        """0.0 = free flow, 1.0 = standstill. None without a free-flow reference."""
        if self.road_closed:
            return 1.0
        if not self.free_flow_speed_kmh or self.current_speed_kmh is None:
            return None
        if self.free_flow_speed_kmh <= 0:
            return None
        return max(0.0, min(1.0, 1.0 - (self.current_speed_kmh / self.free_flow_speed_kmh)))

    @property
    def delay_pct(self) -> float | None:
        if not self.free_flow_travel_time_s or self.current_travel_time_s is None:
            return None
        if self.free_flow_travel_time_s <= 0:
            return None
        return (self.current_travel_time_s / self.free_flow_travel_time_s - 1.0) * 100.0


class TrafficSnapshot(BaseModel):
    observed_at: datetime
    probes: list[RoadProbe] = Field(default_factory=list)
    radius_m: int = 0
    # Probes that were attempted but returned nothing usable.
    failed_probes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Incidents
# --------------------------------------------------------------------------


class Incident(BaseModel):
    id: str  # the source's own record id where it has one
    category: IncidentCategory
    description: str
    distance_km: float | None = None
    bearing: str | None = None
    # The road the incident is on, only when the source actually names it
    # (e.g. TomTom roadNumbers). Never filled from where it starts or ends.
    road: str | None = None
    # Where the incident starts and ends, as the source names them. TomTom's
    # "from"/"to" are location names (usually cross streets), not the road
    # the incident is on: a closure "from 12th Main Road to 14th Main Road"
    # lies on a third road between them.
    from_location: str | None = None
    to_location: str | None = None
    severity_hint: Severity | None = None
    reported_at: datetime | None = None
    last_reported_at: datetime | None = None
    ends_at: datetime | None = None
    confidence: Confidence = Confidence.MEDIUM
    location: GeoPoint | None = None
    # GeoJSON geometry as the source reported it, when it did.
    geometry: dict[str, Any] | None = None
    delay_s: float | None = None
    length_m: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class IncidentSnapshot(BaseModel):
    observed_at: datetime
    incidents: list[Incident] = Field(default_factory=list)
    radius_km: float = 0.0
    # Categories this feed can actually report. Anything outside this set is
    # "not covered by any source", not "confirmed absent".
    covered_categories: list[IncidentCategory] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Provider envelope
# --------------------------------------------------------------------------

PayloadT = TypeVar(
    "PayloadT", WeatherSnapshot, TrafficSnapshot, IncidentSnapshot, RadarSnapshot
)


class ProviderResponse(BaseModel, Generic[PayloadT]):
    """What every provider returns: data or a stated failure, never a guess."""

    source: SourceRef
    status: SourceStatus
    fetched_at: datetime
    data: PayloadT | None = None
    error: str | None = None
    cached: bool = False
    latency_ms: int | None = None
    # Why a failed call failed, in source-health vocabulary — lets the health
    # registry tell "unauthorised" from "down" without parsing error strings.
    health_hint: HealthStatus | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is not SourceStatus.UNAVAILABLE and self.data is not None

    @property
    def observed_at(self) -> datetime | None:
        return self.data.observed_at if self.data is not None else None
