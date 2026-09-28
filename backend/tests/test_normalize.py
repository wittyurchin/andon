"""Normalization tests, focused on the honesty guarantees:
a dead source must not read as "fine", and an inference must not read as an
observation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from andon.config import Settings
from andon.domain.enums import (
    Confidence,
    EvidenceKind,
    IncidentCategory,
    Severity,
    SignalBasis,
    SourceStatus,
    Trend,
)
from andon.domain.signals import (
    GeoPoint,
    Incident,
    IncidentSnapshot,
    PrecipPoint,
    ProviderResponse,
    RoadProbe,
    SourceRef,
    TrafficSnapshot,
    WeatherSnapshot,
)
from andon.domain.situation import Restaurant, SituationSnapshot
from andon.engine.normalize import SituationBuilder
from andon.providers.registry import SignalBundle

NOW = datetime(2026, 9, 18, 14, 30, tzinfo=timezone.utc)
POINT = GeoPoint(lat=12.9716, lon=77.5946)
RESTAURANT = Restaurant(name="Test Kitchen", location=POINT)


def source(kind: str, mode: str = "live") -> SourceRef:
    return SourceRef(id=f"{kind}-src", name=f"{kind.title()} Source", kind=kind, mode=mode)


def ok(kind: str, data, mode: str = "live") -> ProviderResponse:
    return ProviderResponse(
        source=source(kind, mode), status=SourceStatus.OK, fetched_at=NOW, data=data
    )


def down(kind: str, error: str = "connection refused") -> ProviderResponse:
    return ProviderResponse(
        source=source(kind), status=SourceStatus.UNAVAILABLE, fetched_at=NOW, error=error
    )


def weather(
    mm_h: float = 0.0,
    gust: float = 12.0,
    visibility: float = 9000.0,
    forecast: list[float] | None = None,
) -> WeatherSnapshot:
    outlook = [
        PrecipPoint(
            at=NOW + timedelta(minutes=15 * (i + 1)),
            precipitation_mm_h=value,
            kind=EvidenceKind.FORECAST,
        )
        for i, value in enumerate(forecast or [])
    ]
    return WeatherSnapshot(
        observed_at=NOW,
        precipitation_mm_h=mm_h,
        wind_gust_kmh=gust,
        visibility_m=visibility,
        condition="Rain" if mm_h else "Cloudy",
        outlook=outlook,
    )


def traffic(congestions: list[float]) -> TrafficSnapshot:
    free_flow = 50.0
    return TrafficSnapshot(
        observed_at=NOW,
        radius_m=900,
        probes=[
            RoadProbe(
                label=f"probe {i}",
                bearing="N",
                distance_m=900,
                current_speed_kmh=free_flow * (1 - c),
                free_flow_speed_kmh=free_flow,
            )
            for i, c in enumerate(congestions)
        ],
    )


def incidents(*items: Incident) -> IncidentSnapshot:
    return IncidentSnapshot(
        observed_at=NOW,
        incidents=list(items),
        radius_km=5.0,
        covered_categories=[IncidentCategory.WATERLOGGING, IncidentCategory.ACCIDENT],
    )


@pytest.fixture
def builder() -> SituationBuilder:
    return SituationBuilder(Settings(_env_file=None))


def build(builder, *, w=None, t=None, i=None, history=None):
    # `w` accepts a single response or a list, so multi-source weather can be
    # exercised by the same helper.
    responses = w if isinstance(w, list) else [w if w is not None else ok("weather", weather())]
    bundle = SignalBundle(
        weather=responses,
        traffic=[t if t is not None else ok("traffic", traffic([0.05, 0.05]))],
        incidents=[i if i is not None else ok("incidents", incidents())],
    )
    return builder.build(RESTAURANT, bundle, history or [], now=NOW)


class TestMissingSources:
    def test_dead_source_is_excluded_not_treated_as_clear(self, builder):
        situation = build(builder, t=down("traffic"))

        assert situation.traffic.status is SourceStatus.UNAVAILABLE
        assert situation.traffic.unavailable_reason == "connection refused"
        assert "traffic" in situation.missing_signals
        assert situation.traffic.trend is Trend.UNKNOWN

    def test_missing_signal_lowers_overall_confidence(self, builder):
        with_all = build(builder)
        with_gap = build(builder, t=down("traffic"))

        assert with_gap.confidence.rank < with_all.confidence.rank

    def test_report_still_generated_from_remaining_signals(self, builder):
        situation = build(
            builder,
            w=ok("weather", weather(mm_h=12.0)),
            t=down("traffic"),
            i=down("incidents"),
        )

        assert situation.weather.severity is Severity.HIGH
        assert situation.overall.level is not Severity.NONE
        assert set(situation.missing_signals) == {"traffic", "road_conditions"}

    def test_traffic_with_no_usable_probes_is_unavailable(self, builder):
        empty = TrafficSnapshot(observed_at=NOW, probes=[], radius_m=900)
        situation = build(builder, t=ok("traffic", empty))

        assert situation.traffic.status is SourceStatus.UNAVAILABLE


class TestWeather:
    def test_severity_follows_the_worst_facet(self, builder):
        situation = build(builder, w=ok("weather", weather(mm_h=0.0, visibility=600)))

        assert situation.weather.severity is Severity.HIGH
        assert situation.weather.headline == "Poor visibility"

    def test_rising_forecast_reads_as_worsening_and_is_labelled_forecast(self, builder):
        situation = build(builder, w=ok("weather", weather(mm_h=2.0, forecast=[9.0, 12.0])))

        assert situation.weather.trend is Trend.WORSENING
        assert "next 30 minutes" in situation.weather.trend_note
        assert any(e.kind is EvidenceKind.FORECAST for e in situation.weather.evidence)

    def test_falling_forecast_reads_as_improving(self, builder):
        situation = build(builder, w=ok("weather", weather(mm_h=10.0, forecast=[1.0, 0.5])))

        assert situation.weather.trend is Trend.IMPROVING

    def test_source_without_alert_feed_declares_a_coverage_gap(self, builder):
        snapshot = weather(mm_h=1.0)
        snapshot.alerts_supported = False
        situation = build(builder, w=ok("weather", snapshot))

        assert any("severe-weather" in gap for gap in situation.weather.coverage_gaps)


class TestTraffic:
    def test_local_worst_approach_is_weighted_above_the_average(self, builder):
        uniform = build(builder, t=ok("traffic", traffic([0.4, 0.4, 0.4, 0.4])))
        one_jam = build(builder, t=ok("traffic", traffic([0.9, 0.25, 0.25, 0.25])))

        # Same mean-ish, but a single blocked approach must register.
        assert one_jam.traffic.severity.rank > uniform.traffic.severity.rank

    def test_trend_needs_history_and_says_so(self, builder):
        situation = build(builder, t=ok("traffic", traffic([0.5, 0.5])))

        assert situation.traffic.trend is Trend.UNKNOWN
        assert "next refresh" in situation.traffic.trend_note

    def test_trend_derives_from_the_previous_snapshot(self, builder):
        earlier = SituationSnapshot(
            at=NOW - timedelta(minutes=10),
            level=Severity.LOW,
            score=20,
            headline="",
            weather=Severity.NONE,
            traffic=Severity.LOW,
            road_conditions=Severity.NONE,
            traffic_index=0.20,
        )
        situation = build(builder, t=ok("traffic", traffic([0.6, 0.6])), history=[earlier])

        assert situation.traffic.trend is Trend.WORSENING
        assert "10 min ago" in situation.traffic.trend_note


class TestRoadConditions:
    def test_reported_waterlogging_is_observed_evidence(self, builder):
        situation = build(
            builder,
            i=ok(
                "incidents",
                incidents(
                    Incident(
                        id="1",
                        category=IncidentCategory.WATERLOGGING,
                        description="Standing water",
                        distance_km=0.8,
                        bearing="S",
                        reported_at=NOW - timedelta(minutes=5),
                    )
                ),
            ),
        )

        assert situation.road_conditions.severity is Severity.HIGH
        assert situation.road_conditions.headline == "Waterlogging"
        assert all(e.kind is EvidenceKind.OBSERVED for e in situation.road_conditions.evidence)

    def test_heavy_rain_produces_an_inference_never_an_observation(self, builder):
        situation = build(builder, w=ok("weather", weather(mm_h=14.0)))
        road = situation.road_conditions

        inferred = [e for e in road.evidence if e.kind is EvidenceKind.INFERRED]
        assert len(inferred) == 1
        assert "unconfirmed" in inferred[0].text
        assert inferred[0].confidence is Confidence.LOW
        assert road.severity is Severity.MEDIUM  # inference is capped
        assert "inferred" in road.headline

    def test_no_inference_when_waterlogging_is_actually_reported(self, builder):
        situation = build(
            builder,
            w=ok("weather", weather(mm_h=14.0)),
            i=ok(
                "incidents",
                incidents(
                    Incident(
                        id="1",
                        category=IncidentCategory.WATERLOGGING,
                        description="Standing water",
                        distance_km=0.5,
                    )
                ),
            ),
        )

        assert not any(
            e.kind is EvidenceKind.INFERRED for e in situation.road_conditions.evidence
        )

    def test_distant_minor_incident_does_not_raise_the_headline(self, builder):
        situation = build(
            builder,
            i=ok(
                "incidents",
                incidents(
                    Incident(
                        id="1",
                        category=IncidentCategory.CONSTRUCTION,
                        description="Roadworks",
                        distance_km=4.2,
                    )
                ),
            ),
        )

        assert situation.road_conditions.severity is Severity.NONE
        assert situation.road_conditions.headline == "Nothing significant"


class TestOverallAssembly:
    def test_compound_disruption_outranks_any_single_signal(self, builder):
        situation = build(
            builder,
            w=ok("weather", weather(mm_h=12.0)),
            t=ok("traffic", traffic([0.8, 0.75, 0.7])),
            i=ok(
                "incidents",
                incidents(
                    Incident(
                        id="1",
                        category=IncidentCategory.WATERLOGGING,
                        description="Standing water",
                        distance_km=0.9,
                    )
                ),
            ),
        )

        assert situation.overall.level is Severity.SEVERE
        assert situation.overall.score > 80
        assert len(situation.overall.drivers) == 3

    def test_quiet_conditions_read_as_normal(self, builder):
        situation = build(builder)

        assert situation.overall.level is Severity.NONE
        assert situation.overall.headline == "No disruptive conditions detected"

    def test_history_drives_the_overall_trend(self, builder):
        earlier = SituationSnapshot(
            at=NOW - timedelta(minutes=40),
            level=Severity.LOW,
            score=20,
            headline="",
            weather=Severity.LOW,
            traffic=Severity.NONE,
            road_conditions=Severity.NONE,
        )
        situation = build(builder, w=ok("weather", weather(mm_h=14.0)), history=[earlier])

        assert situation.overall.trend is Trend.WORSENING
        assert "40 min ago" in situation.overall.trend_note


def metar_weather(
    intensity: Severity = Severity.NONE,
    distance_km: float = 4.0,
    gust: float = 12.0,
    visibility: float = 9000.0,
) -> WeatherSnapshot:
    """A station-style snapshot: a band, no rate, no forecast."""
    return WeatherSnapshot(
        observed_at=NOW,
        precipitation_mm_h=None,
        rain_intensity=intensity,
        station_name="VOBG (Test Station)",
        station_distance_km=distance_km,
        wind_gust_kmh=gust,
        visibility_m=visibility,
        condition="Rain" if intensity is not Severity.NONE else "No significant weather",
        alerts_supported=False,
    )


class TestMultiSourceWeather:
    """Several weather providers run side by side; disagreement between them is
    evidence, not something to average away."""

    def test_single_source_is_its_own_consolidation(self, builder):
        situation = build(builder, w=[ok("weather", weather(mm_h=3.0))])

        assert situation.weather_sources == []  # nothing to compare
        assert situation.weather.severity is Severity.MEDIUM

    def test_each_source_gets_its_own_assessment(self, builder):
        situation = build(
            builder,
            w=[
                ok("weather", weather(mm_h=3.0)),
                ok("weather", metar_weather(Severity.MEDIUM)),
            ],
        )

        assert len(situation.weather_sources) == 2
        assert {s.basis for s in situation.weather_sources} == {
            SignalBasis.MODEL,
            SignalBasis.OBSERVATION,
        }

    def test_observation_is_preferred_over_a_model_at_equal_confidence(self, builder):
        situation = build(
            builder,
            w=[
                ok("weather", weather(mm_h=12.0)),  # model says heavy
                ok("weather", metar_weather(Severity.LOW)),  # station says light
            ],
        )

        # The nearby station wins the "what is happening now" question.
        assert situation.weather.basis is SignalBasis.OBSERVATION
        assert situation.weather.severity is Severity.LOW

    def test_disagreement_is_reported_and_costs_confidence(self, builder):
        agreeing = build(
            builder,
            w=[
                ok("weather", weather(mm_h=1.0)),
                ok("weather", metar_weather(Severity.LOW)),
            ],
        )
        conflicting = build(
            builder,
            w=[
                ok("weather", weather(mm_h=12.0)),
                ok("weather", metar_weather(Severity.NONE)),
            ],
        )

        assert agreeing.weather.disagreement is None
        assert conflicting.weather.disagreement is not None
        assert "reports" in conflicting.weather.disagreement
        assert conflicting.weather.confidence.rank < agreeing.weather.confidence.rank

    def test_trend_comes_from_the_source_that_can_forecast(self, builder):
        situation = build(
            builder,
            w=[
                ok("weather", weather(mm_h=2.0, forecast=[9.0, 12.0])),  # model, rising
                ok("weather", metar_weather(Severity.LOW)),  # station, no forecast
            ],
        )

        # The station is primary for severity, but it cannot see ahead.
        assert situation.weather.basis is SignalBasis.OBSERVATION
        assert situation.weather.trend is Trend.WORSENING
        assert "next 30 minutes" in situation.weather.trend_note

    def test_evidence_from_every_source_reaches_the_report(self, builder):
        situation = build(
            builder,
            w=[
                ok("weather", weather(mm_h=12.0)),
                ok("weather", metar_weather(Severity.LOW)),
            ],
        )

        names = {e.source_name for e in situation.weather.evidence}
        assert len(names) >= 1
        assert len(situation.weather.evidence) > len(situation.weather_sources[1].evidence)

    def test_one_dead_source_degrades_rather_than_kills_the_signal(self, builder):
        situation = build(
            builder,
            w=[down("weather"), ok("weather", metar_weather(Severity.MEDIUM))],
        )

        assert situation.weather.status is SourceStatus.DEGRADED
        assert situation.weather.severity is Severity.MEDIUM
        assert "weather" not in situation.missing_signals
        assert any("unavailable" in gap for gap in situation.weather.coverage_gaps)

    def test_all_sources_dead_marks_the_signal_missing(self, builder):
        situation = build(builder, w=[down("weather"), down("weather")])

        assert situation.weather.status is SourceStatus.UNAVAILABLE
        assert "weather" in situation.missing_signals

    def test_a_distant_station_loses_confidence_to_a_local_model(self, builder):
        far = build(
            builder,
            w=[
                ok("weather", weather(mm_h=3.0)),
                ok("weather", metar_weather(Severity.MEDIUM, distance_km=45.0)),
            ],
        )

        # 45 km costs two steps of confidence, so the model becomes primary.
        assert far.weather.basis is SignalBasis.MODEL


class TestModelEvidenceIsNeverAnObservation:
    """Regression: the model's grid value was reported as "Heavy rain at the
    restaurant", tagged observed. It is an estimate for a grid cell."""

    def test_model_rain_is_a_forecast_and_names_its_grid_cell(self, builder):
        from andon.geo import offset

        snapshot = weather(mm_h=13.1)
        snapshot.grid_location = offset(POINT, 200, 1400)
        situation = build(builder, w=ok("weather", snapshot))

        rain = next(e for e in situation.weather.evidence if "mm/h" in e.text and "Forecast peak" not in e.text)
        assert rain.kind is EvidenceKind.FORECAST
        assert rain.text.startswith("Model estimate:")
        assert "at the restaurant" not in rain.text
        assert "grid cell 1.4 km away" in rain.text

    def test_station_rain_stays_an_observation_with_its_location(self, builder):
        situation = build(builder, w=ok("weather", metar_weather(Severity.MEDIUM, distance_km=4)))
        rain = next(e for e in situation.weather.evidence if "reported" in e.text)
        assert rain.kind is EvidenceKind.OBSERVED
        assert "4 km away" in rain.text
