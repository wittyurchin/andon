"""Provider snapshots → canonical evidence.

Classification rules applied here, and only here:

* A **model** source (no station) produces ``kind="forecast"`` for everything,
  including its "current" and "past" values — a model's value for now is still
  a model's opinion. Open-Meteo is always forecast evidence.
* A **station** or **radar** source produces ``kind="observation"``.
* Undocumented values (e.g. IMD's unitless fields) are stored verbatim with
  ``units_documented: false`` and never banded.
* Nothing is filled in. A missing gust is ``None``; a missing free-flow speed
  means no congestion index; a missing record id is ``None``.

Confidence figures are engineering defaults per evidence class, used only when
the provider supplies none of its own (TomTom supplies one for flow).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from ..domain.enums import Confidence, Severity, SourceType
from ..domain.signals import (
    IncidentSnapshot,
    ProviderResponse,
    RadarSnapshot,
    TrafficSnapshot,
    WeatherSnapshot,
)
from ..engine import rules
from . import spatial
from .models import AccessGraph, IncidentEvidence, Observation, RestaurantRecord

# Engineering defaults — how much a measurement of this class is trusted when
# the provider says nothing about its own quality.
MODEL_CONFIDENCE = 0.6
STATION_CONFIDENCE = 0.9
RADAR_CONFIDENCE = 0.75
TRAFFIC_CONFIDENCE = 0.8
CORRIDOR_CONFIDENCE = 0.8
# A provider segment this far from our corridor may be a different road.
MATCH_WARNING_M = 60.0

INCIDENT_CONFIDENCE = {
    Confidence.HIGH: 0.85,
    Confidence.MEDIUM: 0.65,
    Confidence.LOW: 0.4,
}


def obs_id(*parts: object) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def source_type(response: ProviderResponse) -> SourceType:
    return SourceType.MOCK if response.source.mode == "mock" else SourceType.LIVE


@dataclass
class Extraction:
    observations: list[Observation] = field(default_factory=list)
    # Incidents per source that answered; absent sources are unknown, not empty.
    incidents: dict[str, list[IncidentEvidence]] = field(default_factory=dict)


class Extractor:
    def __init__(self, stale_after) -> None:
        # stale_after(source_id, category, kind) -> seconds
        self._stale_after = stale_after

    def extract(
        self,
        restaurant: RestaurantRecord,
        graph: AccessGraph | None,
        weather: list[ProviderResponse],
        radar: list[ProviderResponse],
        traffic: list[ProviderResponse],
        incidents: list[ProviderResponse],
        received_at: datetime,
    ) -> Extraction:
        out = Extraction()
        for response in weather:
            if response.ok:
                out.observations += self.weather(restaurant, response, received_at)
        for response in radar:
            if response.ok:
                out.observations += self.radar(restaurant, response, received_at)
        for response in traffic:
            if response.ok:
                out.observations += self.traffic(restaurant, graph, response, response.data, received_at)
        for response in incidents:
            if response.ok:
                out.incidents[response.source.id] = self.incidents(restaurant, graph, response, received_at)
        return out

    # -- helpers ----------------------------------------------------------

    def _obs(self, restaurant, response, *, category, subject, kind, observed_at, received_at,
             value, confidence, location=None, spatial_context=None, valid_at=None,
             basis="source", record_id=None, raw=None) -> Observation:
        return Observation(
            id=obs_id(restaurant.id, response.source.id, category, subject, kind,
                      observed_at.isoformat(), valid_at.isoformat() if valid_at else ""),
            restaurant_id=restaurant.id,
            source_id=response.source.id,
            source_name=response.source.name,
            source_type=source_type(response),
            category=category,
            subject_id=subject,
            kind=kind,
            observed_at=observed_at,
            observed_at_basis=basis,
            valid_at=valid_at,
            received_at=received_at,
            location=location,
            spatial=spatial_context or spatial.area(restaurant.location, location),
            value=value,
            confidence=round(max(0.0, min(1.0, confidence)), 3),
            stale_after_seconds=self._stale_after(response.source.id, category, kind),
            source_record_id=record_id,
            raw_reference=raw[:200] if raw else None,
        )

    # -- weather ----------------------------------------------------------

    def weather(self, restaurant: RestaurantRecord, response: ProviderResponse, received_at: datetime) -> list[Observation]:
        snap: WeatherSnapshot = response.data
        if snap.station_name or snap.station_id:
            return self._station(restaurant, response, snap, received_at)
        return self._model(restaurant, response, snap, received_at)

    def _model(self, restaurant, response, snap: WeatherSnapshot, received_at) -> list[Observation]:
        where = snap.grid_location
        ctx = spatial.area(restaurant.location, where)
        if where is not None:
            ctx.basis = f"model grid cell centred {ctx.distance_m / 1000:.1f} km away; " + ctx.basis
        issued = snap.observed_at
        common = dict(kind="forecast", observed_at=issued, received_at=received_at,
                      location=where, spatial_context=ctx, confidence=MODEL_CONFIDENCE)
        subject = "grid"
        out: list[Observation] = []

        # A model grid cell has no name of its own — labelled generically so
        # the UI can show *what kind* of point this is without inventing one.
        location_label = "Model grid cell" if where is not None else None

        if snap.precipitation_mm_h is not None:
            out.append(self._obs(restaurant, response, category="weather.precipitation", subject=subject,
                                 valid_at=issued, value={
                                     "precipitation_mm_h": snap.precipitation_mm_h,
                                     "band": _band(rules.rain_severity(snap.precipitation_mm_h)),
                                     "probability_pct": snap.probability_pct,
                                     "condition": snap.condition,
                                     "basis": "model",
                                     "location_label": location_label,
                                 }, **common))
        if snap.wind_gust_kmh is not None or snap.wind_speed_kmh is not None:
            out.append(self._obs(restaurant, response, category="weather.wind", subject=subject,
                                 valid_at=issued, value={
                                     "gust_kmh": snap.wind_gust_kmh,
                                     "speed_kmh": snap.wind_speed_kmh,
                                     "band": _band(rules.wind_severity(snap.wind_gust_kmh)),
                                     "location_label": location_label,
                                 }, **common))
        if snap.visibility_m is not None:
            out.append(self._obs(restaurant, response, category="weather.visibility", subject=subject,
                                 valid_at=issued, value={
                                     "visibility_m": snap.visibility_m,
                                     "band": _band(rules.visibility_severity(snap.visibility_m)),
                                     "location_label": location_label,
                                 }, **common))
        if snap.temperature_c is not None:
            out.append(self._obs(restaurant, response, category="weather.temperature", subject=subject,
                                 valid_at=issued, value={
                                     "temperature_c": snap.temperature_c,
                                     "location_label": location_label,
                                 }, **common))
        if snap.reported:
            out.append(self._obs(restaurant, response, category="weather.model_report", subject=subject,
                                 valid_at=issued, value={
                                     "reported": snap.reported,
                                     "units_documented": False,
                                     "location_label": location_label,
                                 }, **common))
        for point in [*snap.recent, *snap.outlook]:
            if point.at == issued or point.precipitation_mm_h is None:
                continue
            out.append(self._obs(restaurant, response, category="weather.precipitation", subject=subject,
                                 valid_at=point.at, value={
                                     "precipitation_mm_h": point.precipitation_mm_h,
                                     "band": _band(rules.rain_severity(point.precipitation_mm_h)),
                                     "probability_pct": point.probability_pct,
                                     "basis": "model",
                                 }, **common))
        for alert in snap.alerts:
            out.append(self._obs(restaurant, response, category="weather.alert", subject=alert.event,
                                 valid_at=alert.starts_at or issued, value={
                                     "event": alert.event,
                                     "headline": alert.headline,
                                     "band": _band(alert.severity_hint),
                                     "ends_at": alert.ends_at.isoformat() if alert.ends_at else None,
                                 }, **common))
        return out

    def _station(self, restaurant, response, snap: WeatherSnapshot, received_at) -> list[Observation]:
        where = snap.station_location
        ctx = spatial.area(restaurant.location, where)
        if where is not None:
            ctx.basis = f"station {snap.station_id or snap.station_name} {ctx.distance_m / 1000:.1f} km away; " + ctx.basis
        subject = snap.station_id or snap.station_name
        basis = "received" if snap.observed_at_basis == "received" else "source"
        common = dict(kind="observation", received_at=received_at, location=where,
                      spatial_context=ctx, confidence=STATION_CONFIDENCE, basis=basis)
        out: list[Observation] = []
        # The friendly name (e.g. "VOBG (HAL Airport)") lives only on the
        # snapshot today — carried into value so the UI can show it without
        # re-deriving it from the bare station id.
        location_label = snap.station_name or snap.station_id

        reports = snap.recent or []
        for report in reports:
            if report.rain_intensity is None:
                continue  # no band reported, nothing to store — never derived
            out.append(self._obs(restaurant, response, category="weather.precipitation", subject=subject,
                                 observed_at=report.at, value={
                                     "band": report.rain_intensity.value,
                                     "rate_mm_h": None,
                                     "reported_as": "band",
                                     "condition": report.condition,
                                     "location_label": location_label,
                                 }, raw=report.raw_text, **common))
        if not reports and snap.rain_intensity is not None:
            out.append(self._obs(restaurant, response, category="weather.precipitation", subject=subject,
                                 observed_at=snap.observed_at, value={
                                     "band": snap.rain_intensity.value,
                                     "rate_mm_h": None,
                                     "reported_as": "band",
                                     "condition": snap.condition,
                                     "location_label": location_label,
                                 }, raw=snap.raw_text, **common))

        at = snap.observed_at
        if snap.wind_gust_kmh is not None or snap.wind_speed_kmh is not None:
            out.append(self._obs(restaurant, response, category="weather.wind", subject=subject,
                                 observed_at=at, value={
                                     "gust_kmh": snap.wind_gust_kmh,  # None when no gust reported
                                     "speed_kmh": snap.wind_speed_kmh,
                                     "band": _band(rules.wind_severity(snap.wind_gust_kmh)),
                                     "location_label": location_label,
                                 }, raw=snap.raw_text, **common))
        if snap.visibility_m is not None:
            out.append(self._obs(restaurant, response, category="weather.visibility", subject=subject,
                                 observed_at=at, value={
                                     "visibility_m": snap.visibility_m,
                                     "band": _band(rules.visibility_severity(snap.visibility_m)),
                                     "location_label": location_label,
                                 }, raw=snap.raw_text, **common))
        if snap.temperature_c is not None:
            out.append(self._obs(restaurant, response, category="weather.temperature", subject=subject,
                                 observed_at=at, value={
                                     "temperature_c": snap.temperature_c,
                                     "location_label": location_label,
                                 }, **common))
        if snap.reported:
            out.append(self._obs(restaurant, response, category="weather.station_report", subject=subject,
                                 observed_at=at, value={
                                     "reported": snap.reported,
                                     "units_documented": False,
                                     "location_label": location_label,
                                 }, **common))
        return out

    # -- radar ------------------------------------------------------------

    def radar(self, restaurant, response, received_at) -> list[Observation]:
        snap: RadarSnapshot = response.data
        decoded = [s for s in snap.samples if s.reflectivity_dbz is not None]
        if not decoded:
            return []
        out: list[Observation] = []

        def emit(subject: str, sample, note: str) -> None:
            ctx = spatial.area(restaurant.location, sample.location, snap.resolution_m)
            out.append(self._obs(
                restaurant, response, category="radar.reflectivity", subject=subject,
                kind="observation", observed_at=snap.observed_at, received_at=received_at,
                location=sample.location, spatial_context=ctx, confidence=RADAR_CONFIDENCE,
                record_id=snap.frame_reference, value={
                    "dbz_min": sample.reflectivity_dbz,
                    "dbz_max": sample.reflectivity_dbz_max,
                    "band": _band(rules.radar_severity(sample.reflectivity_dbz)),
                    "band_derivation": rules.RADAR_BAND_DERIVATION,
                    "resolution_m": snap.resolution_m,
                    "bearing": sample.bearing,
                    "note": note,
                },
            ))

        at_site = snap.site_strongest()
        cells = len(snap.site_samples())
        emit("at_site", at_site, f"strongest echo of the {cells} cell(s) touching the restaurant")
        for radius in (2000, 5000):
            within = [s for s in decoded if s.distance_m <= radius]
            if within:
                strongest = max(within, key=lambda s: s.reflectivity_dbz)
                emit(f"max_within_{radius // 1000}km", strongest,
                     f"strongest echo within {radius // 1000} km")
        return out

    # -- traffic ----------------------------------------------------------

    def traffic(self, restaurant, graph, response, snap: TrafficSnapshot, received_at) -> list[Observation]:
        approximations = {a.id for a in (graph.approaches if graph else []) if a.is_approximation}
        out: list[Observation] = []
        for probe in snap.probes:
            subject = probe.approach_id or f"probe:{probe.label}"
            ctx = spatial.road(restaurant.location, probe.location, graph,
                               approach_id=probe.approach_id,
                               is_approximation=probe.approach_id in approximations)
            if probe.measurement == "corridor_eta":
                delay = None
                if probe.reference_travel_time_s and probe.current_travel_time_s is not None:
                    delay = round((probe.current_travel_time_s / probe.reference_travel_time_s - 1) * 100, 1)
                out.append(self._obs(
                    restaurant, response, category="traffic.corridor_eta", subject=subject,
                    kind="observation", observed_at=snap.observed_at, received_at=received_at,
                    location=probe.location, spatial_context=ctx, confidence=CORRIDOR_CONFIDENCE,
                    value={
                        "approach_label": probe.label,
                        "travel_time_s": probe.current_travel_time_s,
                        "length_m": probe.length_m,
                        "avg_speed_kmh": probe.current_speed_kmh,
                        "reference_travel_time_s": probe.reference_travel_time_s,
                        "reference_kind": probe.reference_kind,
                        "delay_vs_reference_pct": delay,
                        "note": "reference is the provider's own ETA, not free-flow",
                    },
                ))
                continue

            congestion = probe.congestion
            confidence = probe.source_confidence if probe.source_confidence is not None else TRAFFIC_CONFIDENCE
            warning = None
            if probe.match_distance_m is not None and probe.match_distance_m > MATCH_WARNING_M:
                confidence *= 0.5
                warning = (f"provider matched a segment {probe.match_distance_m:.0f} m from the corridor "
                           "— it may be a different road")
            out.append(self._obs(
                restaurant, response, category="traffic.flow", subject=subject,
                kind="observation", observed_at=snap.observed_at, received_at=received_at,
                location=probe.location, spatial_context=ctx, confidence=confidence,
                record_id=probe.source_segment_reference,
                value={
                    "approach_label": probe.label,
                    "road_name": probe.road_name,
                    "road_class": probe.road_class,
                    "current_speed_kmh": probe.current_speed_kmh,
                    "free_flow_speed_kmh": probe.free_flow_speed_kmh,
                    "congestion_index": round(congestion, 3) if congestion is not None else None,
                    "band": _band(rules.congestion_severity(congestion)),
                    "current_travel_time_s": probe.current_travel_time_s,
                    "free_flow_travel_time_s": probe.free_flow_travel_time_s,
                    "delay_pct": round(probe.delay_pct, 1) if probe.delay_pct is not None else None,
                    "road_closed": probe.road_closed,
                    "match_distance_m": probe.match_distance_m,
                    "match_warning": warning,
                },
            ))
        return out

    # -- incidents --------------------------------------------------------

    def incidents(self, restaurant, graph, response, received_at) -> list[IncidentEvidence]:
        snap: IncidentSnapshot = response.data
        out: list[IncidentEvidence] = []
        for incident in snap.incidents:
            record_id = None if incident.id.startswith("unidentified:") else incident.id
            identity = record_id or "|".join([
                incident.category.value,
                f"{incident.location.lat:.4f},{incident.location.lon:.4f}" if incident.location else "",
                incident.description,
            ])
            ctx = spatial.road(restaurant.location, incident.location, graph, line=_line(incident.geometry))
            out.append(IncidentEvidence(
                id=obs_id(restaurant.id, response.source.id, identity)[:16],
                restaurant_id=restaurant.id,
                source_id=response.source.id,
                source_name=response.source.name,
                source_type=source_type(response),
                source_record_id=record_id,
                incident_type=incident.category,
                status="active",
                description=incident.description,
                location=incident.location,
                geometry=incident.geometry,
                first_seen=received_at,
                last_seen=received_at,
                observed_at=incident.last_reported_at or incident.reported_at,
                confidence=INCIDENT_CONFIDENCE.get(incident.confidence, 0.65),
                spatial=ctx,
                attributes={
                    k: v for k, v in {
                        "road": incident.road,
                        "from": incident.from_location,
                        "to": incident.to_location,
                        "severity_hint": incident.severity_hint.value if incident.severity_hint else None,
                        "delay_s": incident.delay_s,
                        "length_m": incident.length_m,
                        "reported_at": incident.reported_at.isoformat() if incident.reported_at else None,
                        "ends_at": incident.ends_at.isoformat() if incident.ends_at else None,
                        **incident.attributes,
                    }.items() if v is not None
                },
            ))
        return out


def _band(severity: Severity | None) -> str | None:
    return severity.value if severity is not None else None


def _line(geometry: dict | None) -> list[tuple[float, float]] | None:
    """(lon, lat) points of a GeoJSON LineString, or its longest part."""
    if not isinstance(geometry, dict):
        return None
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "LineString":
        parts = [coords]
    elif geometry.get("type") == "MultiLineString":
        parts = coords
    else:
        return None
    lines = [
        [(float(p[0]), float(p[1])) for p in part if isinstance(p, (list, tuple)) and len(p) >= 2]
        for part in parts
    ]
    lines = [line for line in lines if len(line) >= 2]
    return max(lines, key=len) if lines else None
