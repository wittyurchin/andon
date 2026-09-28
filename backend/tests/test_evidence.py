"""The evidence layer's guarantees, one class per requirement.

Every test drives the real pipeline (providers → extraction → SQLite →
health → change detection → bundle) with scripted providers and a movable
clock, so outcomes are deterministic.
"""

from __future__ import annotations

import pytest

from andon.domain.enums import Applicability, ChangeType, Freshness, HealthStatus, IncidentCategory, Severity
from andon.domain.signals import TrafficSnapshot, WeatherSnapshot
from andon.evidence.store import EvidenceStore
from andon.providers.errors import ProviderMisconfigured, ProviderUnauthorized
from tests.evidence_helpers import (
    HSR,
    Scripted,
    flow,
    incident_feed,
    make_service,
    model,
    on_approach,
    station,
)


async def refresh(service, name="HSR Kitchen"):
    record = service.register(name, HSR, 300)
    return (await service.refresh(record.id)).bundle


def health_of(bundle, source_id):
    return next(h for h in bundle.source_health if h.source_id == source_id)


class TestMissingData:
    async def test_failed_provider_is_unavailable_never_normal(self, tmp_path):
        broken = Scripted("weather", "down-weather", lambda _: ConnectionError("refused"))
        service, _, _ = make_service(tmp_path, weather=[broken])

        bundle = await refresh(service)

        assert health_of(bundle, "down-weather").status is HealthStatus.UNAVAILABLE
        assert [o for o in bundle.observations if o.source_id == "down-weather"] == []
        assert bundle.coverage["weather"]["observation_sources"] == []
        assert "No current precipitation observation" in bundle.coverage["weather"]["statement"]

    async def test_missing_credentials_are_misconfigured_and_not_counted_as_failures(self, tmp_path):
        keyless = Scripted("traffic", "keyless", lambda _: ProviderMisconfigured("set KEY"))
        service, _, _ = make_service(tmp_path, traffic=[keyless])

        first = await refresh(service)
        second = await service.refresh(first.restaurant.id)

        record = health_of(second.bundle, "keyless")
        assert record.status is HealthStatus.MISCONFIGURED
        assert record.consecutive_failures == 0


class TestStaleData:
    async def test_old_observation_is_marked_stale_but_kept(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.weather = [Scripted("weather", "old-station", station(clock=clock, band=Severity.MEDIUM, age_min=40))]
        # weather_station policy: 900 s by default.
        bundle = await refresh(service)

        obs = next(o for o in bundle.observations if o.category == "weather.precipitation")
        assert obs.stale is True
        assert obs.freshness_seconds >= 40 * 60
        assert obs.value["band"] == "medium"  # kept as reported — not reset to "none"
        assert health_of(bundle, "old-station").status is HealthStatus.STALE

    async def test_staleness_grows_as_time_passes_without_new_data(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.weather = [Scripted("weather", "st", station(clock=clock, band=Severity.LOW, age_min=1))]
        bundle = await refresh(service)
        assert not next(o for o in bundle.observations if o.category == "weather.precipitation").stale

        clock.advance(60)
        later = service.bundle(bundle.restaurant.id)
        assert next(o for o in later.observations if o.category == "weather.precipitation").stale


class TestSpatialRelevance:
    async def test_far_station_keeps_its_distance_and_loses_relevance(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.weather = [Scripted("weather", "far", station(clock=clock, band=Severity.HIGH, distance_km=33))]
        bundle = await refresh(service)

        obs = next(o for o in bundle.observations if o.category == "weather.precipitation")
        assert 32_000 < obs.spatial.distance_m < 34_000
        assert obs.spatial.applicability is Applicability.DISTANT
        assert obs.spatial.relevance < 0.3
        # A heavy-rain reading 33 km away never becomes "heavy rain here".
        assert bundle.coverage["weather"]["has_local_observation"] is False
        assert "33.0 km away" in bundle.coverage["weather"]["statement"]

    async def test_incident_on_a_corridor_is_linked_to_that_approach(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.incidents = [Scripted("incidents", "inc", incident_feed(
            clock, lambda ctx: [on_approach(ctx, 1, "W1")]))]
        bundle = await refresh(service)

        incident = bundle.incidents[0]
        east = bundle.access_graph.approaches[1]
        assert incident.spatial.on_approach is True
        assert east.id in incident.spatial.approach_ids
        assert incident.spatial.relevance == 1.0


class TestConflictingSources:
    async def test_both_observations_survive_and_the_conflict_is_listed(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.weather = [
            Scripted("weather", "the-model", model(clock, mm_h=12.0)),  # heavy
            Scripted("weather", "the-station", station(clock=clock, band=Severity.LOW)),  # light
        ]
        bundle = await refresh(service)

        assert any(o.source_id == "the-station" for o in bundle.observations)
        assert any(f.source_id == "the-model" for f in bundle.forecasts)
        conflict = next(c for c in bundle.conflicts if c.category == "precipitation")
        assert conflict.spread >= 2
        assert {m["source_id"] for m in conflict.members} == {"the-model", "the-station"}
        assert {m["kind"] for m in conflict.members} == {"forecast", "observation"}


class TestProviderIndependence:
    async def test_one_failure_does_not_stop_the_others(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        good = Scripted("traffic", "good", flow(clock, lambda i: 0.3))
        bad = Scripted("traffic", "bad", lambda _: ProviderUnauthorized("401"))
        service.registry.traffic = [bad, good]
        bundle = await refresh(service)

        assert health_of(bundle, "bad").status is HealthStatus.UNAUTHORIZED
        assert health_of(bundle, "good").status is HealthStatus.HEALTHY
        assert len([o for o in bundle.observations if o.source_id == "good"]) == 4
        assert good.calls == 1


class TestPersistence:
    async def test_evidence_survives_a_restart(self, tmp_path):
        service, store, clock = make_service(tmp_path)
        service.registry.weather = [Scripted("weather", "st", station(clock=clock, band=Severity.MEDIUM))]
        service.registry.incidents = [Scripted("incidents", "inc", incident_feed(
            clock, lambda ctx: [on_approach(ctx, 0, "A1", IncidentCategory.ACCIDENT)]))]
        first = await refresh(service)
        rid = first.restaurant.id
        store.close()

        # A new process: fresh store and service over the same file.
        reopened = EvidenceStore(tmp_path / "evidence.sqlite3")
        restarted, _, _ = make_service(tmp_path, store=reopened, clock=clock)
        bundle = restarted.bundle(rid)

        assert bundle.restaurant.name == "HSR Kitchen"
        assert any(o.source_id == "st" for o in bundle.observations)
        assert [i.source_record_id for i in bundle.incidents] == ["A1"]
        assert {h.source_id for h in bundle.source_health} >= {"st", "inc"}
        assert bundle.access_graph is not None and len(bundle.access_graph.approaches) == 4


class TestChangeDetection:
    async def test_same_state_twice_produces_no_events(self, tmp_path):
        service, store, clock = make_service(tmp_path)
        service.registry.traffic = [Scripted("traffic", "t", flow(clock, lambda i: 0.2))]
        first = await refresh(service)
        clock.advance(2)
        second = await service.refresh(first.restaurant.id)

        assert second.bundle.refresh.changes_detected == 0
        assert store.list_changes(first.restaurant.id) == []

    async def test_material_change_creates_an_event(self, tmp_path):
        service, store, clock = make_service(tmp_path)
        level = {"north": 0.2}
        service.registry.traffic = [Scripted("traffic", "t", flow(clock, lambda i: level["north"] if i == 0 else 0.1))]
        first = await refresh(service)

        clock.advance(5)
        level["north"] = 0.8  # low → severe on one approach
        second = await service.refresh(first.restaurant.id)

        types = {e.change_type for e in second.bundle.changes}
        assert ChangeType.TRAFFIC_WORSENED in types
        assert ChangeType.APPROACH_STATE_CHANGED in types
        worsened = next(e for e in second.bundle.changes if e.change_type is ChangeType.TRAFFIC_WORSENED)
        assert "north" in worsened.summary.lower()

    async def test_new_and_cleared_incidents_are_events(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        present = {"on": False}
        service.registry.incidents = [Scripted("incidents", "inc", incident_feed(
            clock, lambda ctx: [on_approach(ctx, 2, "X1", IncidentCategory.ACCIDENT)] if present["on"] else []))]
        first = await refresh(service)

        present["on"] = True
        clock.advance(3)
        appeared = await service.refresh(first.restaurant.id)
        assert ChangeType.INCIDENT_APPEARED in {e.change_type for e in appeared.bundle.changes}

        present["on"] = False
        clock.advance(3)
        cleared = await service.refresh(first.restaurant.id)
        assert ChangeType.INCIDENT_CLEARED in {e.change_type for e in cleared.bundle.changes}

    async def test_source_going_down_is_an_event(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        state = {"up": True}
        service.registry.traffic = [Scripted("traffic", "t", lambda ctx: flow(clock, lambda i: 0.2)(ctx)
                                             if state["up"] else ConnectionError("down"))]
        first = await refresh(service)
        state["up"] = False
        clock.advance(2)
        second = await service.refresh(first.restaurant.id)

        assert ChangeType.SOURCE_BECAME_UNAVAILABLE in {e.change_type for e in second.bundle.changes}


class TestIncidentLifecycle:
    async def test_first_seen_is_kept_and_a_failed_feed_never_clears(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        mode = {"state": "report"}

        def produce(ctx):
            if mode["state"] == "fail":
                return ConnectionError("feed down")
            items = [on_approach(ctx, 0, "L1", IncidentCategory.ROAD_CLOSURE)] if mode["state"] == "report" else []
            return incident_feed(clock, lambda _: items)(ctx)

        service.registry.incidents = [Scripted("incidents", "inc", produce)]
        first = await refresh(service)
        seen_at = first.incidents[0].first_seen

        clock.advance(10)
        still = await service.refresh(first.restaurant.id)
        incident = still.bundle.incidents[0]
        assert incident.first_seen == seen_at and incident.last_seen > seen_at

        mode["state"] = "fail"
        clock.advance(10)
        outage = await service.refresh(first.restaurant.id)
        # Silence from a broken feed is not an all-clear.
        assert outage.bundle.incidents[0].status == "active"

        mode["state"] = "clear"
        clock.advance(10)
        cleared = await service.refresh(first.restaurant.id)
        assert cleared.bundle.incidents[0].status == "cleared"


class TestSourceHealth:
    async def test_failures_count_up_and_success_resets(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        outcomes = iter([True, False, False, True])
        service.registry.traffic = [Scripted("traffic", "t", lambda ctx: flow(clock, lambda i: 0.2)(ctx)
                                             if next(outcomes) else ConnectionError("down"))]
        rid = (await refresh(service)).restaurant.id
        records = []
        for _ in range(3):
            clock.advance(1)
            records.append(health_of((await service.refresh(rid)).bundle, "t"))

        assert [r.status for r in records] == [HealthStatus.UNAVAILABLE, HealthStatus.UNAVAILABLE, HealthStatus.HEALTHY]
        assert [r.consecutive_failures for r in records] == [1, 2, 0]
        assert records[-1].last_failure_at is not None
        assert records[-1].last_success_at == clock.now

    async def test_freshness_grade_matches_the_same_policy_as_situation_signals(self, tmp_path):
        # weather_station policy is 900 s by default: fresh <300, recent <600,
        # aging <900, stale >=900 (thirds of the window) — the same grading
        # FreshnessPolicy.grade already applies to SignalAssessment.freshness.
        from andon.geo import offset

        service, _, clock = make_service(tmp_path)
        reading_at = clock.now  # the station's report never changes; only its age does
        service.registry.weather = [Scripted("weather", "st", lambda _ctx: WeatherSnapshot(
            observed_at=reading_at, rain_intensity=Severity.LOW, station_id="ST1", station_name="ST1 (Test)",
            station_location=offset(HSR, 30, 4000), station_distance_km=4.0, alerts_supported=False,
        ))]
        bundle = await refresh(service)
        assert health_of(bundle, "st").freshness_grade is Freshness.FRESH

        clock.advance(8)  # age 480 s: past the 300 s fresh boundary, short of the 600 s recent one
        bundle = (await service.refresh(bundle.restaurant.id, force=True)).bundle
        assert health_of(bundle, "st").freshness_grade is Freshness.RECENT

    async def test_freshness_grade_is_unknown_when_a_source_was_never_called(self, tmp_path):
        keyless = Scripted("traffic", "keyless", lambda _: ProviderMisconfigured("set KEY"))
        service, _, _ = make_service(tmp_path, traffic=[keyless])
        bundle = await refresh(service)
        assert health_of(bundle, "keyless").freshness_grade is Freshness.UNKNOWN


class TestAccessGraph:
    async def test_restaurant_has_several_corridors_with_segments(self, tmp_path):
        service, _, _ = make_service(tmp_path)
        bundle = await refresh(service)
        graph = bundle.access_graph

        assert len(graph.approaches) >= 2
        for approach in graph.approaches:
            assert approach.segment_ids
            assert all(s.approach_id == approach.id for s in graph.segments if s.id in approach.segment_ids)

    async def test_every_traffic_observation_names_its_corridor(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.traffic = [Scripted("traffic", "t", flow(clock, lambda i: 0.3))]
        bundle = await refresh(service)
        ids = {a.id for a in bundle.access_graph.approaches}
        flows = [o for o in bundle.observations if o.category == "traffic.flow"]
        assert flows and all(o.subject_id in ids and o.spatial.on_approach for o in flows)


class TestNoFabricatedFields:
    async def test_missing_free_flow_means_no_congestion_band(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.traffic = [Scripted("traffic", "t", flow(clock, lambda i: 0.3, free_flow=None))]
        bundle = await refresh(service)

        for obs in (o for o in bundle.observations if o.category == "traffic.flow"):
            assert obs.value["free_flow_speed_kmh"] is None
            assert obs.value["congestion_index"] is None
            assert obs.value["band"] is None

    async def test_station_without_gust_has_no_gust(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.weather = [Scripted("weather", "st", station(clock=clock, band=Severity.NONE, gust=None))]
        bundle = await refresh(service)
        wind = next(o for o in bundle.observations if o.category == "weather.wind")
        assert wind.value["gust_kmh"] is None
        assert wind.value["speed_kmh"] == 10.0

    async def test_station_band_is_never_turned_into_a_rate(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.weather = [Scripted("weather", "st", station(clock=clock, band=Severity.MEDIUM))]
        bundle = await refresh(service)
        precip = next(o for o in bundle.observations if o.category == "weather.precipitation")
        assert precip.value["rate_mm_h"] is None
        assert precip.value["reported_as"] == "band"

    async def test_model_output_is_always_forecast(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.weather = [Scripted("weather", "m", model(clock, mm_h=3.0))]
        bundle = await refresh(service)
        assert not [o for o in bundle.observations if o.source_id == "m"]
        assert {f.kind for f in bundle.forecasts if f.source_id == "m"} == {"forecast"}

    async def test_station_observation_carries_a_friendly_location_label(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.weather = [Scripted("weather", "st", station(clock=clock, band=Severity.LOW))]
        bundle = await refresh(service)
        precip = next(o for o in bundle.observations if o.category == "weather.precipitation")
        assert precip.value["location_label"] == "ST1 (Test)"  # not just the bare station id

    async def test_model_forecast_carries_a_generic_location_label(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.weather = [Scripted("weather", "m", model(clock, mm_h=3.0))]
        bundle = await refresh(service)
        precip = next(f for f in bundle.forecasts if f.source_id == "m" and f.category == "weather.precipitation")
        assert precip.value["location_label"] == "Model grid cell"

    async def test_model_reported_values_are_never_dropped_or_banded(self, tmp_path):
        # A model-kind source (no station identity) with undocumented-unit
        # values, e.g. WeatherAPI.com's precip_mm with no documented time
        # window: stored verbatim, not silently dropped by the model path.
        from andon.geo import offset

        service, _, clock = make_service(tmp_path)
        snapshot = WeatherSnapshot(
            observed_at=clock.now, grid_location=offset(HSR, 200, 1500),
            reported={"precip_mm": 0.3, "precip_units_documented": False}, alerts_supported=False,
        )
        service.registry.weather = [Scripted("weather", "wa", lambda _ctx: snapshot)]
        bundle = await refresh(service)
        report = next(f for f in bundle.forecasts if f.source_id == "wa" and f.category == "weather.model_report")
        assert report.value["reported"] == {"precip_mm": 0.3, "precip_units_documented": False}
        assert report.value["units_documented"] is False

    async def test_mock_evidence_is_labelled_mock(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.traffic = [Scripted("traffic", "sim", flow(clock, lambda i: 0.3), mode="mock")]
        bundle = await refresh(service)
        assert {o.source_type.value for o in bundle.observations if o.source_id == "sim"} == {"mock"}

    async def test_empty_traffic_response_is_not_scored_as_clear(self, tmp_path):
        service, _, clock = make_service(tmp_path)
        service.registry.traffic = [Scripted("traffic", "t", lambda _: TrafficSnapshot(observed_at=clock.now))]
        bundle = await refresh(service)
        assert bundle.coverage["traffic"]["approaches_observed"] == 0
        assert len(bundle.coverage["traffic"]["approaches_unobserved"]) == 4


class TestMockBackfill:
    async def test_mock_traffic_history_shows_a_trend_immediately(self, tmp_path):
        from andon.providers.scenario import ScenarioClock
        from andon.providers.traffic.mock import MockTrafficProvider

        service, _, _ = make_service(tmp_path)
        service.registry.traffic = [MockTrafficProvider(ScenarioClock("demo"))]
        bundle = await refresh(service)

        trend = next(t for t in bundle.trends if t.category == "traffic.flow" and t.subject_id.endswith("north"))
        assert trend.direction == "worsening"
        assert trend.points >= 5  # four backfilled readings + the live one

    async def test_real_providers_are_never_backfilled(self, tmp_path):
        service, store, clock = make_service(tmp_path)
        service.registry.traffic = [Scripted("traffic", "live-t", flow(clock, lambda i: 0.3))]
        bundle = await refresh(service)
        assert store.count_observations(bundle.restaurant.id, "live-t") == 4


class TestPoller:
    async def test_only_due_restaurants_are_refreshed(self, tmp_path):
        from datetime import timedelta

        from andon.evidence.poller import EvidencePoller

        service, _, clock = make_service(tmp_path)
        rid = service.register("A", HSR, refresh_interval_seconds=300).id
        poller = EvidencePoller(service)

        assert await poller.poll_due(clock.now) == [rid]  # never refreshed
        assert await poller.poll_due(clock.now + timedelta(seconds=60)) == []
        assert await poller.poll_due(clock.now + timedelta(seconds=301)) == [rid]


@pytest.mark.parametrize("band,expected", [(Severity.NONE, "none"), (Severity.HIGH, "high")])
async def test_bands_round_trip_through_sqlite(tmp_path, band, expected):
    service, _, clock = make_service(tmp_path)
    service.registry.weather = [Scripted("weather", "st", station(clock=clock, band=band))]
    bundle = await refresh(service)
    assert next(o for o in bundle.observations if o.category == "weather.precipitation").value["band"] == expected
