"""Normalization: provider snapshots -> one internal situation model.

This layer is where raw numbers become labelled, sourced, time-stamped claims.
Three rules hold throughout:

1. A failed or missing source produces ``SourceStatus.UNAVAILABLE`` and is
   excluded from the overall score — never silently treated as "fine".
2. Anything that is not directly measured is marked ``EvidenceKind.INFERRED``
   or ``FORECAST`` and is never phrased as an observation.
3. Confidence falls as data ages or coverage shrinks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean

from ..config import Settings
from ..domain.enums import (
    Confidence,
    EvidenceKind,
    Freshness,
    IncidentCategory,
    Severity,
    SignalBasis,
    SignalKind,
    SourceStatus,
    Trend,
)
from ..domain.signals import (
    Incident,
    IncidentSnapshot,
    ProviderResponse,
    RadarSnapshot,
    RoadProbe,
    SourceRef,
    TrafficSnapshot,
    WeatherSnapshot,
)
from ..evidence.freshness import FreshnessPolicy
from ..domain.situation import (
    Evidence,
    Facet,
    NormalizedSituation,
    OverallAssessment,
    Restaurant,
    SignalAssessment,
    SituationSnapshot,
    TrendItem,
)
from ..geo import haversine_km
from ..providers.registry import SignalBundle
from . import rules

# How much a jam on the single worst approach counts relative to the average
# across all sampled approaches.
WORST_PROBE_WEIGHT = 0.55

# Dead-bands for trend classification.
RAIN_TREND_THRESHOLD_MM_H = 0.6
# Severity ranks are integers, so half a band is the smallest honest move.
RAIN_BAND_TREND_THRESHOLD = 0.5
TRAFFIC_TREND_THRESHOLD = 0.07

WATERLOGGING_CATEGORIES = {IncidentCategory.WATERLOGGING, IncidentCategory.FLOODING}


def to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def age_seconds(observed_at: datetime | None, now: datetime) -> int | None:
    observed = to_utc(observed_at)
    if observed is None:
        return None
    return int((now - observed).total_seconds())


class SituationBuilder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # The same per-source freshness policy the evidence layer uses, so the
        # dashboard and the evidence bundle never disagree about "stale".
        self._policy = FreshnessPolicy(settings.freshness_policy, settings.source_freshness)
        self._restaurant_point = None

    # -- entry point -----------------------------------------------------

    def build(
        self,
        restaurant: Restaurant,
        bundle: SignalBundle,
        history: list[SituationSnapshot],
        now: datetime | None = None,
    ) -> NormalizedSituation:
        now = now or datetime.now(timezone.utc)
        previous = _previous_snapshot(history, now)
        self._restaurant_point = restaurant.location

        weather_sources = [self._weather(r, now) for r in bundle.weather]
        weather_sources += [self._radar(r, now) for r in bundle.radar]
        weather = consolidate(SignalKind.WEATHER, weather_sources)
        traffic_sources = [self._traffic(r, now, previous) for r in bundle.traffic]
        traffic = consolidate(SignalKind.TRAFFIC, traffic_sources)
        road = self._road(bundle.incidents, weather, now, previous)

        available = [s for s in (weather, traffic, road) if s.status is not SourceStatus.UNAVAILABLE]
        level, score = rules.overall_from_signals([s.severity for s in available])

        missing = [
            s.kind.value for s in (weather, traffic, road) if s.status is SourceStatus.UNAVAILABLE
        ]
        coverage_gaps = [gap for s in (weather, traffic, road) for gap in s.coverage_gaps]

        confidence = _overall_confidence(available, missing_count=len(missing))
        overall_trend, overall_trend_note = _overall_trend(history, level, now)

        drivers = _drivers(weather, traffic, road)
        overall = OverallAssessment(
            level=level,
            label=rules.OVERALL_LABELS[level],
            score=score,
            headline=" + ".join(drivers) if drivers else "No disruptive conditions detected",
            drivers=drivers,
            trend=overall_trend,
            trend_note=overall_trend_note,
            confidence=confidence,
        )

        return NormalizedSituation(
            restaurant=restaurant,
            generated_at=now,
            overall=overall,
            weather=weather,
            weather_sources=weather_sources if len(weather_sources) > 1 else [],
            traffic=traffic,
            traffic_sources=traffic_sources if len(traffic_sources) > 1 else [],
            road_conditions=road,
            missing_signals=missing,
            coverage_gaps=coverage_gaps,
            confidence=confidence,
        )

    # -- weather ---------------------------------------------------------

    def _weather(self, response: ProviderResponse, now: datetime) -> SignalAssessment:
        source = response.source
        if not response.ok:
            return _unavailable(
                SignalKind.WEATHER,
                source,
                response.error or "source did not return data",
                "Weather unavailable",
            )

        snapshot: WeatherSnapshot = response.data
        observed = to_utc(snapshot.observed_at)
        age = age_seconds(observed, now)
        is_station = bool(snapshot.station_name or snapshot.station_id)
        freshness = self._freshness(
            age, source.id, "weather.precipitation", "observation" if is_station else "forecast"
        )

        # A band reported by the source wins over one we derive: METAR's
        # "-RA" is an observer's classification, not a number to re-bucket.
        rain = snapshot.rain_intensity or rules.rain_severity(snapshot.precipitation_mm_h)
        wind = rules.wind_severity(snapshot.wind_gust_kmh)
        visibility = rules.visibility_severity(snapshot.visibility_m)

        rain_value, rain_unit = (
            (snapshot.precipitation_mm_h, "mm/h")
            if snapshot.precipitation_mm_h is not None
            else (rules.RAIN_LABELS[rain] if rain is not None else None, None)
        )

        facets: list[Facet] = [
            Facet(
                key="rain",
                label="Rain",
                severity=rain or Severity.NONE,
                value=rain_value,
                unit=rain_unit,
                available=rain is not None,
                note=None if rain is not None else "not reported by source",
            ),
            Facet(
                key="wind",
                label="Wind gusts",
                severity=wind or Severity.NONE,
                value=snapshot.wind_gust_kmh,
                unit="km/h",
                available=wind is not None,
                note=None if wind is not None else "not reported by source",
            ),
            Facet(
                key="visibility",
                label="Visibility",
                severity=visibility or Severity.NONE,
                value=snapshot.visibility_m,
                unit="m",
                available=visibility is not None,
                note=None if visibility is not None else "not reported by source",
            ),
        ]

        severity = max(
            (f.severity for f in facets if f.available),
            default=Severity.NONE,
        )

        coverage_gaps: list[str] = []
        if not snapshot.alerts_supported:
            coverage_gaps.append(f"{source.name} does not publish severe-weather warnings")
        elif snapshot.alerts:
            worst_alert = max(
                (a.severity_hint or Severity.MEDIUM for a in snapshot.alerts),
                key=lambda s: s.rank,
            )
            severity = Severity.from_rank(max(severity.rank, worst_alert.rank))
            facets.append(
                Facet(
                    key="alerts",
                    label="Severe weather",
                    severity=worst_alert,
                    value=snapshot.alerts[0].event,
                )
            )

        trend, trend_note, trend_kind = _weather_trend(snapshot)

        base_confidence = Confidence.HIGH if source.mode == "live" else Confidence.MEDIUM
        # Open-Meteo is a numerical model rather than a station reading.
        if source.id == "open-meteo":
            base_confidence = Confidence.MEDIUM
        # A real observation taken 30 km away is still a real observation, but
        # it says progressively less about this restaurant.
        distance_penalty = rules.station_distance_penalty(snapshot.station_distance_km)
        confidence = rules.adjust_confidence(base_confidence, freshness, distance_penalty)

        if snapshot.station_name and snapshot.station_distance_km is not None:
            facets.append(
                Facet(
                    key="station",
                    label="Nearest station",
                    severity=Severity.NONE,
                    value=snapshot.station_name,
                    note=f"{snapshot.station_distance_km:.0f} km from the restaurant",
                )
            )
            if distance_penalty:
                coverage_gaps.append(
                    f"Nearest weather station is {snapshot.station_distance_km:.0f} km away — "
                    "conditions at the restaurant may differ"
                )

        evidence = self._weather_evidence(snapshot, source, observed, age, freshness, confidence)
        headline, detail = _weather_headline(snapshot, rain, wind, visibility, severity)

        return SignalAssessment(
            kind=SignalKind.WEATHER,
            status=SourceStatus.OK,
            basis=_basis_of(source, snapshot),
            source_label=_source_label(source, snapshot),
            severity=severity,
            headline=headline,
            detail=detail,
            trend=trend,
            trend_note=trend_note,
            confidence=confidence,
            observed_at=observed,
            age_seconds=age,
            freshness=freshness,
            facets=facets,
            evidence=evidence,
            sources=[source],
            coverage_gaps=coverage_gaps,
        )

    def _weather_evidence(
        self,
        snapshot: WeatherSnapshot,
        source,
        observed: datetime | None,
        age: int | None,
        freshness: Freshness,
        confidence: Confidence,
    ) -> list[Evidence]:
        items: list[Evidence] = []

        def add(text: str, kind: EvidenceKind, severity: Severity, weight_bonus: int = 0) -> None:
            items.append(
                Evidence(
                    text=text,
                    kind=kind,
                    source_id=source.id,
                    source_name=source.name,
                    observed_at=observed,
                    age_seconds=age,
                    freshness=freshness,
                    confidence=confidence,
                    weight=_weight(severity, kind, freshness) + weight_bonus,
                )
            )

        # A station observed its value somewhere; a model *estimated* one for a
        # grid cell. Only the first is an observation, and neither is "at the
        # restaurant" unless the geometry says so.
        is_station = bool(snapshot.station_name or snapshot.station_id)
        kind_now = EvidenceKind.OBSERVED if is_station else EvidenceKind.FORECAST
        if is_station:
            where = (
                f"at {snapshot.station_name}, {snapshot.station_distance_km:.0f} km away"
                if snapshot.station_distance_km is not None
                else f"at {snapshot.station_name}"
            )
            prefix = ""
        else:
            grid_km = (
                haversine_km(self._restaurant_point, snapshot.grid_location)
                if snapshot.grid_location is not None and self._restaurant_point is not None
                else None
            )
            where = (
                f"for a model grid cell {grid_km:.1f} km away" if grid_km is not None
                else "for these coordinates"
            )
            prefix = "Model estimate: "

        def phrase(text: str) -> str:
            return f"{prefix}{text}" if prefix else text[:1].upper() + text[1:]

        if snapshot.precipitation_mm_h is not None:
            rain = rules.rain_severity(snapshot.precipitation_mm_h) or Severity.NONE
            add(
                phrase(f"{rules.RAIN_LABELS[rain].lower()} {where}: {snapshot.precipitation_mm_h:.1f} mm/h"),
                kind_now,
                rain,
            )
        elif snapshot.rain_intensity is not None:
            # No rate available — report the observed band, and flag the missing
            # rate only when there is actually precipitation to quantify.
            caveat = (
                " (band only — this source reports no rainfall rate)"
                if snapshot.rain_intensity is not Severity.NONE
                else ""
            )
            add(
                f"{snapshot.condition or rules.RAIN_LABELS[snapshot.rain_intensity]} "
                f"reported {where}{caveat}",
                EvidenceKind.OBSERVED,
                snapshot.rain_intensity,
            )

        upcoming = _forecast_window(snapshot, 60)
        if upcoming:
            peak = max(p.precipitation_mm_h or 0.0 for p in upcoming)
            peak_severity = rules.rain_severity(peak) or Severity.NONE
            add(
                f"Forecast peak rainfall over the next 60 minutes: {peak:.1f} mm/h",
                EvidenceKind.FORECAST,
                peak_severity,
            )

        if snapshot.wind_gust_kmh is not None:
            wind = rules.wind_severity(snapshot.wind_gust_kmh) or Severity.NONE
            if wind is not Severity.NONE:
                add(
                    phrase(f"wind gusts {snapshot.wind_gust_kmh:.0f} km/h"),
                    kind_now,
                    wind,
                )

        if snapshot.visibility_m is not None:
            vis = rules.visibility_severity(snapshot.visibility_m) or Severity.NONE
            if vis is not Severity.NONE:
                add(
                    phrase(f"visibility {snapshot.visibility_m / 1000:.1f} km"),
                    kind_now,
                    vis,
                )

        for alert in snapshot.alerts:
            add(
                f"Weather warning in force: {alert.event}",
                EvidenceKind.OBSERVED,
                alert.severity_hint or Severity.MEDIUM,
                weight_bonus=10,
            )

        return sorted(items, key=lambda e: e.weight, reverse=True)

    # -- traffic ---------------------------------------------------------

    def _traffic(
        self,
        response: ProviderResponse,
        now: datetime,
        previous: SituationSnapshot | None,
    ) -> SignalAssessment:
        source = response.source
        if not response.ok:
            return _unavailable(
                SignalKind.TRAFFIC,
                source,
                response.error or "source did not return data",
                "Traffic unavailable",
            )

        snapshot: TrafficSnapshot = response.data
        observed = to_utc(snapshot.observed_at)
        age = age_seconds(observed, now)
        freshness = self._freshness(age, source.id, "traffic.flow")

        scored = [(p, p.congestion) for p in snapshot.probes if p.congestion is not None]
        if not scored:
            corridor_only = snapshot.probes and all(p.measurement == "corridor_eta" for p in snapshot.probes)
            return _unavailable(
                SignalKind.TRAFFIC,
                source,
                (
                    "reports corridor travel times but no free-flow reference, so no congestion "
                    "level can be scored — the travel times are in the evidence bundle"
                    if corridor_only
                    else "no road segment returned usable speed data"
                ),
                "Traffic not scorable" if corridor_only else "Traffic unavailable",
            )

        values = [c for _, c in scored]
        worst_probe, worst = max(scored, key=lambda item: item[1])
        average = mean(values)
        index = WORST_PROBE_WEIGHT * worst + (1 - WORST_PROBE_WEIGHT) * average
        severity = rules.congestion_severity(index) or Severity.NONE

        facets = [
            Facet(
                key="congestion",
                label="Congestion",
                severity=severity,
                value=round(index * 100),
                unit="%",
                note=f"{len(scored)} approach corridors sampled",
            ),
            Facet(
                key="worst_approach",
                label="Worst approach",
                severity=rules.congestion_severity(worst) or Severity.NONE,
                value=worst_probe.label,
                note=_probe_detail(worst_probe),
            ),
        ]

        closed = [p for p, _ in scored if p.road_closed]
        if closed:
            facets.append(
                Facet(
                    key="closures",
                    label="Closed segments",
                    severity=Severity.HIGH,
                    value=len(closed),
                )
            )
            severity = Severity.from_rank(max(severity.rank, Severity.HIGH.rank))

        base_confidence = Confidence.HIGH if source.mode == "live" else Confidence.MEDIUM
        partial_penalty = 1 if len(scored) < max(1, len(snapshot.probes)) else 0
        confidence = rules.adjust_confidence(base_confidence, freshness, partial_penalty)

        trend, trend_note = _traffic_trend(index, previous, now)

        evidence: list[Evidence] = []
        ranked = sorted(scored, key=lambda item: item[1], reverse=True)
        for probe, congestion in ranked[:3]:
            probe_severity = rules.congestion_severity(congestion) or Severity.NONE
            if probe_severity is Severity.NONE and probe is not worst_probe:
                continue
            evidence.append(
                Evidence(
                    text=(
                        f"{probe.label}: {rules.CONGESTION_LABELS[probe_severity].lower()} traffic, "
                        f"{_probe_detail(probe)}"
                    ),
                    kind=EvidenceKind.OBSERVED,
                    source_id=source.id,
                    source_name=source.name,
                    observed_at=observed,
                    age_seconds=age,
                    freshness=freshness,
                    confidence=confidence,
                    distance_km=round(probe.distance_m / 1000, 2) if probe.distance_m else 0.0,
                    weight=_weight(probe_severity, EvidenceKind.OBSERVED, freshness),
                )
            )

        detail = (
            f"{round(index * 100)}% below free-flow across {len(scored)} approaches"
            f" — worst: {worst_probe.label}"
        )

        return SignalAssessment(
            kind=SignalKind.TRAFFIC,
            status=SourceStatus.DEGRADED if snapshot.failed_probes else SourceStatus.OK,
            basis=SignalBasis.OBSERVATION,
            source_label=source.name,
            severity=severity,
            headline=rules.CONGESTION_LABELS[severity],
            detail=detail,
            trend=trend,
            trend_note=trend_note,
            confidence=confidence,
            observed_at=observed,
            age_seconds=age,
            freshness=freshness,
            facets=facets,
            evidence=evidence,
            sources=[source],
        )

    # -- road conditions -------------------------------------------------

    def _road(
        self,
        responses: list[ProviderResponse],
        weather: SignalAssessment,
        now: datetime,
        previous: SituationSnapshot | None,
    ) -> SignalAssessment:
        """Incidents from every feed that answered, kept side by side.

        A feed that failed contributes nothing and is named as a gap: its
        silence is not evidence that the roads are clear.
        """
        answered = [r for r in responses if r.ok]
        failed = [r for r in responses if not r.ok]
        if not answered:
            reason = "; ".join(f"{r.source.name}: {r.error}" for r in failed) or "no incident feed configured"
            return _unavailable(
                SignalKind.ROAD_CONDITIONS,
                failed[0].source if failed else None,
                reason,
                "Road conditions unavailable",
            )

        snapshots: list[tuple[SourceRef, IncidentSnapshot]] = [(r.source, r.data) for r in answered]
        observed = max(to_utc(s.observed_at) for _, s in snapshots)
        age = age_seconds(observed, now)
        freshness = self._freshness(age, answered[0].source.id, "incident")
        live = any(src.mode == "live" for src, _ in snapshots)
        confidence = rules.adjust_confidence(
            Confidence.HIGH if live else Confidence.MEDIUM, freshness, 1 if failed else 0
        )

        facets: list[Facet] = []
        evidence: list[Evidence] = []
        severity = Severity.NONE
        strongest: tuple[Severity, Incident] | None = None
        all_incidents: list[Incident] = []

        for source, snapshot in snapshots:
            for incident in snapshot.incidents:
                all_incidents.append(incident)
                incident_severity = rules.incident_severity(
                    incident.category, incident.distance_km, incident.severity_hint
                )
                facets.append(
                    Facet(
                        key=f"{incident.category.value}:{source.id}:{incident.id}",
                        label=incident.category.label,
                        severity=incident_severity,
                        value=incident.distance_km,
                        unit="km",
                        kind=EvidenceKind.OBSERVED,
                        note=incident.description,
                    )
                )
                if incident_severity.rank > severity.rank:
                    severity = incident_severity
                if strongest is None or incident_severity.rank > strongest[0].rank:
                    strongest = (incident_severity, incident)

                reported = to_utc(incident.last_reported_at or incident.reported_at)
                incident_age = age_seconds(reported, now) if reported else age
                incident_freshness = self._freshness(incident_age, source.id, "incident")
                evidence.append(
                    Evidence(
                        text=_incident_sentence(incident),
                        kind=EvidenceKind.OBSERVED,
                        source_id=source.id,
                        source_name=source.name,
                        observed_at=reported or observed,
                        age_seconds=incident_age,
                        freshness=incident_freshness,
                        confidence=incident.confidence,
                        distance_km=incident.distance_km,
                        weight=_weight(incident_severity, EvidenceKind.OBSERVED, incident_freshness),
                    )
                )

        # Inference, clearly labelled: sustained heavy rain makes waterlogging
        # plausible even where no feed reports it. Capped at MEDIUM so an
        # inference can never outrank an actual observation.
        reported_water = any(i.category in WATERLOGGING_CATEGORIES for i in all_incidents)
        inferred = _waterlogging_inference(weather) if not reported_water else None
        if inferred is not None:
            inferred_severity, note = inferred
            facets.append(
                Facet(
                    key="waterlogging_risk",
                    label="Waterlogging risk",
                    severity=inferred_severity,
                    kind=EvidenceKind.INFERRED,
                    note=note,
                )
            )
            severity = Severity.from_rank(max(severity.rank, inferred_severity.rank))
            evidence.append(
                Evidence(
                    text=f"Waterlogging is plausible but unconfirmed: {note}",
                    kind=EvidenceKind.INFERRED,
                    source_id="situation-engine",
                    source_name="Situation engine (inference)",
                    observed_at=now,
                    age_seconds=0,
                    freshness=Freshness.FRESH,
                    confidence=Confidence.LOW,
                    weight=_weight(inferred_severity, EvidenceKind.INFERRED, Freshness.FRESH),
                )
            )

        coverage_gaps: list[str] = []
        covered: set[IncidentCategory] = set()
        for _, snapshot in snapshots:
            covered.update(snapshot.covered_categories)
        if covered and IncidentCategory.WATERLOGGING not in covered:
            coverage_gaps.append("No incident feed reports waterlogging (flooding only, at best)")
        if covered and IncidentCategory.EVENT not in covered:
            coverage_gaps.append("No incident feed reports local events")
        for response in failed:
            coverage_gaps.append(f"{response.source.name} unavailable: {response.error}")

        if strongest is not None and severity is not Severity.NONE:
            headline = strongest[1].category.label
            detail = _incident_sentence(strongest[1])
        elif strongest is not None:
            # Reported, but far enough away to be operationally irrelevant.
            headline = "Nothing significant"
            detail = f"Only minor or distant reports: {_incident_sentence(strongest[1])}"
        elif inferred is not None:
            headline = "Waterlogging risk (inferred)"
            detail = inferred[1]
        else:
            radius = max(s.radius_km for _, s in snapshots)
            headline = "Nothing reported"
            detail = f"No incidents within {radius:.0f} km"

        trend, trend_note = _road_trend(severity, all_incidents, previous, now)

        return SignalAssessment(
            kind=SignalKind.ROAD_CONDITIONS,
            status=SourceStatus.DEGRADED if failed else SourceStatus.OK,
            basis=SignalBasis.OBSERVATION,
            severity=severity,
            headline=headline,
            detail=detail,
            trend=trend,
            trend_note=trend_note,
            confidence=confidence,
            observed_at=observed,
            age_seconds=age,
            freshness=freshness,
            facets=sorted(facets, key=lambda f: f.severity.rank, reverse=True),
            evidence=sorted(evidence, key=lambda e: e.weight, reverse=True),
            sources=[src for src, _ in snapshots],
            coverage_gaps=coverage_gaps,
        )

    # -- radar -----------------------------------------------------------

    def _radar(self, response: ProviderResponse, now: datetime) -> SignalAssessment:
        """Radar as a weather source: what is falling on the restaurant's cell."""
        source = response.source
        if not response.ok:
            return _unavailable(SignalKind.WEATHER, source, response.error or "no radar frame", "Radar unavailable")

        snapshot: RadarSnapshot = response.data
        decoded = [s for s in snapshot.samples if s.reflectivity_dbz is not None]
        if not decoded:
            return _unavailable(SignalKind.WEATHER, source, "no decodable radar samples near the restaurant", "Radar unavailable")

        at_site = snapshot.site_strongest()
        nearby = max((s for s in decoded if s.distance_m <= 5000), key=lambda s: s.reflectivity_dbz)
        severity = rules.radar_severity(at_site.reflectivity_dbz) or Severity.NONE
        nearby_band = rules.radar_severity(nearby.reflectivity_dbz) or Severity.NONE

        observed = to_utc(snapshot.observed_at)
        age = age_seconds(observed, now)
        freshness = self._freshness(age, source.id, "radar.reflectivity")
        confidence = rules.adjust_confidence(Confidence.MEDIUM, freshness)
        resolution = f"~{snapshot.resolution_m / 1000:.1f} km cells"

        headline = {
            Severity.NONE: "No echo overhead",
            Severity.LOW: "Light echo overhead",
            Severity.MEDIUM: "Moderate echo overhead",
            Severity.HIGH: "Heavy echo overhead",
            Severity.SEVERE: "Intense echo overhead",
        }[severity]
        detail = f"{at_site.reflectivity_dbz:.0f} dBZ over the restaurant ({resolution})"
        if nearby_band.rank > severity.rank:
            detail += f"; stronger {nearby.reflectivity_dbz:.0f} dBZ {nearby.distance_m / 1000:.1f} km {nearby.bearing or ''}".rstrip()

        facets = [
            Facet(key="rain", label="Echo overhead", severity=severity,
                  value=f"{at_site.reflectivity_dbz:.0f} dBZ", note=rules.RADAR_BAND_DERIVATION),
            Facet(key="nearby_echo", label="Strongest within 5 km", severity=nearby_band,
                  value=f"{nearby.reflectivity_dbz:.0f} dBZ · {nearby.distance_m / 1000:.1f} km {nearby.bearing or ''}".strip()),
        ]

        def item(text: str, band: Severity, distance_m: float) -> Evidence:
            return Evidence(
                text=text, kind=EvidenceKind.OBSERVED, source_id=source.id, source_name=source.name,
                observed_at=observed, age_seconds=age, freshness=freshness, confidence=confidence,
                distance_km=round(distance_m / 1000, 2),
                weight=_weight(band, EvidenceKind.OBSERVED, freshness),
            )

        evidence = [item(f"Radar: {at_site.reflectivity_dbz:.0f} dBZ echo over the restaurant ({resolution})",
                         severity, at_site.distance_m)]
        if nearby_band.rank > severity.rank:
            evidence.append(item(
                f"Radar: stronger {nearby.reflectivity_dbz:.0f} dBZ echo {nearby.distance_m / 1000:.1f} km "
                f"{nearby.bearing or ''} of the restaurant".replace("  ", " "),
                nearby_band, nearby.distance_m,
            ))

        return SignalAssessment(
            kind=SignalKind.WEATHER,
            status=SourceStatus.OK,
            basis=SignalBasis.OBSERVATION,  # remote sensing; mock-ness is on the source
            source_label=f"Radar · {resolution}",
            severity=severity,
            headline=headline,
            detail=detail,
            trend=Trend.UNKNOWN,
            trend_note="A single radar frame shows where rain is, not where it is heading",
            confidence=confidence,
            observed_at=observed,
            age_seconds=age,
            freshness=freshness,
            facets=facets,
            evidence=evidence,
            sources=[source],
            coverage_gaps=[f"Band from dBZ is {rules.RADAR_BAND_DERIVATION}"],
        )

    # -- helpers ---------------------------------------------------------

    def _freshness(
        self, age: int | None, source_id: str, category: str, kind: str = "observation"
    ) -> Freshness:
        return self._policy.grade_for(source_id, category, kind, age)


# --------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------


def _unavailable(
    kind: SignalKind, source: SourceRef | None, reason: str, headline: str
) -> SignalAssessment:
    return SignalAssessment(
        kind=kind,
        status=SourceStatus.UNAVAILABLE,
        source_label=source.name if source else None,
        severity=Severity.NONE,
        headline=headline,
        detail="No data — this signal is excluded from the assessment",
        trend=Trend.UNKNOWN,
        confidence=Confidence.LOW,
        freshness=Freshness.UNKNOWN,
        sources=[source] if source else [],
        unavailable_reason=reason,
    )


def _weight(severity: Severity, kind: EvidenceKind, freshness: Freshness) -> int:
    weight = 20 + severity.rank * 18
    if kind is EvidenceKind.OBSERVED:
        weight += 10
    elif kind is EvidenceKind.INFERRED:
        weight -= 12
    weight -= rules.FRESHNESS_PENALTY.get(freshness, 1) * 8
    return max(0, min(100, weight))


def _basis_of(source, snapshot: WeatherSnapshot) -> SignalBasis:
    """A weather value either came from an instrument somewhere, or it was
    computed for these coordinates. Whether it is *simulated* is a separate
    fact, carried by the source's mode and shown alongside."""
    if snapshot.station_name or snapshot.station_id:
        return SignalBasis.OBSERVATION
    return SignalBasis.MODEL


def _source_label(source, snapshot: WeatherSnapshot) -> str:
    """Short tile label — the station identifier is more useful than the vendor."""
    if snapshot.station_name:
        icao = snapshot.station_name.split(" ", 1)[0]
        if snapshot.station_distance_km is not None:
            return f"{icao} · {snapshot.station_distance_km:.0f} km"
        return icao
    return source.name


def consolidate(kind: SignalKind, assessments: list[SignalAssessment]) -> SignalAssessment:
    """Reduce several sources for one signal to the one used for scoring.

    Policy, in order:

    1. Drop sources that failed.
    2. Pick a primary — highest confidence, then a real observation over a
       model, then the freshest. An observation of what *is* beats a model's
       opinion about the same moment.
    3. For weather, take the trend from a source that can actually forecast,
       since a station only knows where things have been.
    4. If the sources disagree by a severity band or more, say so and drop a
       step of confidence. Disagreement is information, not something to
       average away.
    """
    if not assessments:
        return _unavailable(kind, None, "no provider configured for this signal", f"{kind.value.replace('_', ' ').title()} unavailable")

    available = [a for a in assessments if a.status is not SourceStatus.UNAVAILABLE]
    if not available:
        return assessments[0]
    if len(available) == 1 and len(assessments) == 1:
        return available[0]

    primary = max(available, key=_primary_rank)

    trend_source = primary
    if kind is SignalKind.WEATHER:
        forecaster = next(
            (a for a in available if a.basis is SignalBasis.MODEL and a.trend is not Trend.UNKNOWN),
            None,
        )
        trend_source = forecaster or primary

    severities = [a.severity for a in available]
    spread = max(severities).rank - min(severities).rank
    disagreement: str | None = None
    confidence = primary.confidence

    if spread >= 1:
        parts = [f"{a.source_label or a.sources[0].name} reports {a.severity.value}" for a in available]
        # Semicolons, not middots — station labels contain middots themselves.
        disagreement = "; ".join(parts)
        confidence = Confidence.from_rank(confidence.rank - 1)

    merged_evidence = sorted(
        (item for a in available for item in a.evidence),
        key=lambda e: e.weight,
        reverse=True,
    )
    gaps: list[str] = []
    for a in available:
        for gap in a.coverage_gaps:
            if gap not in gaps:
                gaps.append(gap)
    for a in assessments:
        if a.status is SourceStatus.UNAVAILABLE:
            name = a.source_label or (a.sources[0].name if a.sources else "a source")
            gaps.append(f"{name} unavailable: {a.unavailable_reason}")

    return SignalAssessment(
        kind=kind,
        status=SourceStatus.OK if len(available) == len(assessments) else SourceStatus.DEGRADED,
        basis=primary.basis,
        source_label=primary.source_label,
        disagreement=disagreement,
        severity=primary.severity,
        headline=primary.headline,
        detail=primary.detail,
        trend=trend_source.trend,
        trend_note=trend_source.trend_note,
        confidence=confidence,
        observed_at=primary.observed_at,
        age_seconds=primary.age_seconds,
        freshness=primary.freshness,
        facets=primary.facets,
        evidence=merged_evidence,
        sources=[source for a in available for source in a.sources],
        coverage_gaps=gaps,
    )


def consolidate_weather(assessments: list[SignalAssessment]) -> SignalAssessment:
    return consolidate(SignalKind.WEATHER, assessments)


def _primary_rank(assessment: SignalAssessment) -> tuple:
    observed = 1 if assessment.basis is SignalBasis.OBSERVATION else 0
    age = assessment.age_seconds if assessment.age_seconds is not None else 10**6
    return (assessment.confidence.rank, observed, -age)


def _weather_headline(
    snapshot: WeatherSnapshot,
    rain: Severity | None,
    wind: Severity | None,
    visibility: Severity | None,
    severity: Severity,
) -> tuple[str, str]:
    """Headline names whichever facet is actually driving the severity."""
    if severity is Severity.NONE:
        headline = snapshot.condition or "Clear"
    elif rain is not None and rain.rank >= severity.rank:
        headline = rules.RAIN_LABELS[rain]
    elif wind is not None and wind.rank >= severity.rank:
        headline = "Strong wind"
    elif visibility is not None and visibility.rank >= severity.rank:
        headline = "Poor visibility"
    else:
        headline = snapshot.condition or "Disruptive weather"

    detail_parts: list[str] = []
    if snapshot.precipitation_mm_h is not None:
        detail_parts.append(f"{snapshot.precipitation_mm_h:.1f} mm/h")
    elif snapshot.condition and snapshot.condition != headline:
        # Skip when the condition is already the headline — otherwise the card
        # reads "No significant weather / no significant weather, gusts...".
        detail_parts.append(snapshot.condition.lower())
    if snapshot.wind_gust_kmh is not None:
        detail_parts.append(f"gusts {snapshot.wind_gust_kmh:.0f} km/h")
    if snapshot.visibility_m is not None:
        detail_parts.append(f"visibility {snapshot.visibility_m / 1000:.1f} km")
    if snapshot.station_distance_km is not None:
        detail_parts.append(f"observed {snapshot.station_distance_km:.0f} km away")
    return headline, ", ".join(detail_parts) or "no detail reported"


def _probe_detail(probe: RoadProbe) -> str:
    parts: list[str] = []
    if probe.current_speed_kmh is not None and probe.free_flow_speed_kmh:
        parts.append(
            f"{probe.current_speed_kmh:.0f} km/h vs {probe.free_flow_speed_kmh:.0f} km/h free-flow"
        )
    delay = probe.delay_pct
    if delay is not None and delay > 5:
        parts.append(f"+{delay:.0f}% travel time")
    if probe.road_class:
        parts.append(probe.road_class.lower())
    if probe.road_closed:
        parts.append("road closed")
    return ", ".join(parts) or "no detail reported"


def _forecast_window(snapshot: WeatherSnapshot, minutes: int):
    if not snapshot.outlook:
        return []
    start = to_utc(snapshot.outlook[0].at)
    if start is None:
        return snapshot.outlook
    limit = start.timestamp() + minutes * 60
    return [p for p in snapshot.outlook if to_utc(p.at).timestamp() <= limit] or snapshot.outlook[:1]


def _weather_trend(snapshot: WeatherSnapshot) -> tuple[Trend, str | None, EvidenceKind]:
    current = snapshot.precipitation_mm_h
    window = _forecast_window(snapshot, 30)
    forecast_values = [p.precipitation_mm_h for p in window if p.precipitation_mm_h is not None]

    if current is not None and forecast_values:
        delta = mean(forecast_values) - current
        trend = rules.trend_from_delta(delta, True, RAIN_TREND_THRESHOLD_MM_H)
        direction = {
            Trend.WORSENING: "forecast to intensify",
            Trend.IMPROVING: "forecast to ease",
            Trend.STEADY: "forecast to hold steady",
        }.get(trend, "unclear")
        return trend, f"Rainfall {direction} over the next 30 minutes", EvidenceKind.FORECAST

    observed = [p.precipitation_mm_h for p in snapshot.recent if p.precipitation_mm_h is not None]
    if current is not None and len(observed) >= 2:
        delta = current - mean(observed[:-1])
        trend = rules.trend_from_delta(delta, True, RAIN_TREND_THRESHOLD_MM_H)
        return trend, "Based on the last hour of observations", EvidenceKind.OBSERVED

    # Sources with no forecast and no rate — METAR — still have their own
    # observation history, so the trend comes from the band moving.
    bands = [p.rain_intensity for p in snapshot.recent if p.rain_intensity is not None]
    if snapshot.rain_intensity is not None and len(bands) >= 2:
        delta = snapshot.rain_intensity.rank - mean(b.rank for b in bands[:-1])
        trend = rules.trend_from_delta(delta, True, RAIN_BAND_TREND_THRESHOLD)
        return (
            trend,
            f"Based on {len(bands)} station observations over the last few hours",
            EvidenceKind.OBSERVED,
        )

    return Trend.UNKNOWN, "No usable rainfall trend from this source", EvidenceKind.OBSERVED


def _traffic_trend(
    index: float, previous: SituationSnapshot | None, now: datetime
) -> tuple[Trend, str | None]:
    if previous is None or previous.traffic_index is None:
        return Trend.UNKNOWN, "No earlier reading yet — trend appears after the next refresh"
    delta = index - previous.traffic_index
    trend = rules.trend_from_delta(delta, True, TRAFFIC_TREND_THRESHOLD)
    minutes = max(1, int((now - to_utc(previous.at)).total_seconds() // 60))
    change = round(abs(delta) * 100)
    if trend is Trend.STEADY:
        return trend, f"Congestion within {round(TRAFFIC_TREND_THRESHOLD * 100)} points of the reading {minutes} min ago"
    direction = "up" if delta > 0 else "down"
    return trend, f"Congestion {direction} {change} points versus {minutes} min ago"


def _road_trend(
    severity: Severity,
    incidents: list[Incident],
    previous: SituationSnapshot | None,
    now: datetime,
) -> tuple[Trend, str | None]:
    recent_reports = [
        i
        for i in incidents
        if i.reported_at and (now - to_utc(i.reported_at)).total_seconds() < 900
    ]
    if previous is not None:
        delta = severity.rank - previous.road_conditions.rank
        if delta > 0:
            return Trend.WORSENING, "New or closer incidents since the last reading"
        if delta < 0:
            return Trend.IMPROVING, "Fewer or more distant incidents than the last reading"
        if severity is not Severity.NONE:
            return Trend.STEADY, "Active and unchanged since the last reading"
        return Trend.STEADY, "Still nothing reported nearby"

    if recent_reports:
        return Trend.WORSENING, f"{len(recent_reports)} incident(s) reported in the last 15 minutes"
    if severity is not Severity.NONE:
        return Trend.STEADY, "Active — no earlier reading to compare against"
    return Trend.UNKNOWN, "No earlier reading yet"


def _waterlogging_inference(weather: SignalAssessment) -> tuple[Severity, str] | None:
    """Sustained heavy rain makes waterlogging plausible. Inference only."""
    if weather.status is SourceStatus.UNAVAILABLE:
        return None
    rain_facet = next((f for f in weather.facets if f.key == "rain" and f.available), None)
    if rain_facet is None:
        return None
    if rain_facet.severity.rank < Severity.HIGH.rank:
        return None
    value = rain_facet.value if isinstance(rain_facet.value, (int, float)) else None
    amount = f"{value:.1f} mm/h" if value is not None else "heavy rainfall"
    return (
        Severity.MEDIUM,
        f"no feed reports standing water, but rainfall is running at {amount}",
    )


def _incident_sentence(incident: Incident) -> str:
    parts = [incident.description]
    if incident.distance_km is not None:
        bearing = f" {incident.bearing}" if incident.bearing else ""
        parts.append(f"{incident.distance_km:.1f} km{bearing} of the restaurant")
    if incident.road:
        parts.append(f"on {incident.road}")
    if incident.from_location and incident.to_location:
        parts.append(f"from {incident.from_location} to {incident.to_location}")
    elif incident.from_location:
        parts.append(f"starting at {incident.from_location}")
    return ", ".join(parts)


def _previous_snapshot(
    history: list[SituationSnapshot], now: datetime, max_age_minutes: int = 45
) -> SituationSnapshot | None:
    """Most recent snapshot that is still relevant for trend comparison."""
    for snapshot in reversed(history):
        at = to_utc(snapshot.at)
        if at is None:
            continue
        age_min = (now - at).total_seconds() / 60
        if 0 <= age_min <= max_age_minutes:
            return snapshot
    return None


def _overall_confidence(available: list[SignalAssessment], missing_count: int) -> Confidence:
    if not available:
        return Confidence.LOW
    lowest = min(s.confidence.rank for s in available)
    return Confidence.from_rank(lowest - (1 if missing_count else 0))


def _overall_trend(
    history: list[SituationSnapshot], level: Severity, now: datetime
) -> tuple[Trend, str | None]:
    window = [
        s for s in history if (now - to_utc(s.at)).total_seconds() <= 3600 and to_utc(s.at) <= now
    ]
    if not window:
        return Trend.UNKNOWN, "No history yet for this location"
    earliest = window[0]
    delta = level.rank - earliest.level.rank
    minutes = max(1, int((now - to_utc(earliest.at)).total_seconds() // 60))
    if delta > 0:
        return Trend.WORSENING, f"Up from {earliest.level.value} {minutes} min ago"
    if delta < 0:
        return Trend.IMPROVING, f"Down from {earliest.level.value} {minutes} min ago"
    return Trend.STEADY, f"Unchanged over the last {minutes} min"


def _drivers(*signals: SignalAssessment) -> list[str]:
    """Short phrases for the status banner, strongest signal first."""
    active = [
        s
        for s in signals
        if s.status is not SourceStatus.UNAVAILABLE and s.severity is not Severity.NONE
    ]
    active.sort(key=lambda s: s.severity.rank, reverse=True)
    return [
        f"{s.headline.lower()} traffic" if s.kind is SignalKind.TRAFFIC else s.headline.lower()
        for s in active
    ]


def build_trend_items(situation: NormalizedSituation) -> list[TrendItem]:
    """The "What's changing?" panel — derived, never invented by the LLM."""
    items: list[TrendItem] = []

    if situation.weather.status is not SourceStatus.UNAVAILABLE:
        items.append(
            TrendItem(
                label="Rain",
                trend=situation.weather.trend,
                detail=situation.weather.trend_note,
                kind=EvidenceKind.FORECAST,
            )
        )
    if situation.traffic.status is not SourceStatus.UNAVAILABLE:
        items.append(
            TrendItem(
                label="Traffic",
                trend=situation.traffic.trend,
                detail=situation.traffic.trend_note,
                kind=EvidenceKind.OBSERVED,
            )
        )
    if situation.road_conditions.status is not SourceStatus.UNAVAILABLE:
        road = situation.road_conditions
        label = road.headline if road.severity is not Severity.NONE else "Road incidents"
        items.append(
            TrendItem(
                label=label,
                trend=road.trend,
                detail=road.trend_note,
                kind=EvidenceKind.OBSERVED,
            )
        )
    items.append(
        TrendItem(
            label="Overall",
            trend=situation.overall.trend,
            detail=situation.overall.trend_note,
            kind=EvidenceKind.OBSERVED,
        )
    )
    return items
