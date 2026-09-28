"""New adapters and the access graph, tested offline.

Payload shapes follow the downloaded documentation in documentation/ — the
point is that each adapter reads exactly the documented fields and fills in
nothing else.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from andon.domain.enums import HealthStatus, Severity, SourceStatus
from andon.domain.signals import ApproachTarget, FetchContext, GeoPoint
from andon.evidence import access_graph as ag
from andon.evidence import spatial
from andon.providers import disabled
from andon.providers.radar import rainviewer
from andon.providers.traffic.mappls import MapplsCorridorTrafficProvider, corridor_probe
from andon.providers.traffic.tomtom import parse_segment
from andon.providers.weather.imd import ImdWeatherProvider, parse_aws
from andon.providers.weather.open_meteo import OpenMeteoWeatherProvider

FIXTURES = Path(__file__).parent / "fixtures"
HSR = GeoPoint(lat=12.912233, lon=77.651282)
NOW = datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------
# Access graph (OpenStreetMap)
# --------------------------------------------------------------------------


class TestOsmAccessGraph:
    @pytest.fixture(scope="class")
    def graph(self):
        payload = json.loads((FIXTURES / "overpass_hsr_layout.json").read_text())
        return ag.build_from_osm("r1", HSR, payload, radius_m=700, max_approaches=6, now=NOW)

    def test_corridors_are_real_named_roads(self, graph):
        labels = [a.label for a in graph.approaches]
        assert "27th Main Road · north" in labels
        assert "27th Main Road · south" in labels
        assert any(label.startswith("Frontage · ") for label in labels)
        assert not any(a.is_approximation for a in graph.approaches)

    def test_segments_carry_osm_references_and_belong_to_an_approach(self, graph):
        ids = {a.id for a in graph.approaches}
        assert graph.segments
        for segment in graph.segments:
            assert segment.source_segment_reference.startswith("osm:way/")
            assert segment.approach_id in ids
            assert segment.length_m > 0

    def test_no_junction_stubs(self, graph):
        for approach in graph.approaches:
            if not approach.label.startswith("Frontage"):
                assert approach.length_m >= ag.MIN_CORRIDOR_M

    def test_probe_sits_on_its_corridor(self, graph):
        by_id = {s.id: s for s in graph.segments}
        for approach in graph.approaches:
            ctx = spatial.road(HSR, approach.probe, graph)
            assert approach.id in ctx.approach_ids
            assert all(sid in by_id for sid in approach.segment_ids)

    def test_attribution_and_derivation_are_stated(self, graph):
        assert "OpenStreetMap contributors" in graph.attribution
        assert "not a routing analysis" in graph.derivation


class TestAccessGraphFallbacks:
    def test_radial_fallback_says_it_is_not_a_road(self):
        graph = ag.build_radial("r1", HSR, 700, "overpass timed out")
        assert all(a.is_approximation for a in graph.approaches)
        assert all("not a mapped road" in a.label for a in graph.approaches)
        assert graph.notes == ["overpass timed out"]

    def test_configured_points_are_labelled_approximations(self):
        graph = ag.build_configured("r1", HSR, [{"label": "Main gate", "lat": 12.913, "lon": 77.651}], 700)
        assert graph.approaches[0].label == "Main gate (configured approximation)"
        assert graph.approaches[0].is_approximation

    async def test_unreachable_overpass_falls_back_with_the_reason(self):
        builder = ag.AccessGraphBuilder("auto", 700, 6, 30, ["http://127.0.0.1:9/api/interpreter"], [], timeout_s=2)
        graph = await builder.build("r1", HSR)
        assert graph.source == "radial"
        assert "OpenStreetMap road data unavailable" in graph.notes[0]

    def test_evidence_on_an_approximation_is_worth_less(self):
        graph = ag.build_radial("r1", HSR, 700, "test")
        real = spatial.road(HSR, graph.approaches[0].probe, None, approach_id="x")
        approx = spatial.road(HSR, graph.approaches[0].probe, None, approach_id="x", is_approximation=True)
        assert approx.relevance < real.relevance
        assert "radial approximation" in approx.basis


# --------------------------------------------------------------------------
# IMD
# --------------------------------------------------------------------------


class TestImd:
    @pytest.mark.parametrize(
        ("kwargs", "requires"),
        [
            (dict(api_key=None, auth_header=None, auth_query_param=None, state_id="7"), "credentials"),
            (dict(api_key="k", auth_header=None, auth_query_param=None, state_id="7"), "verification"),
            (dict(api_key="k", auth_header="X-Key", auth_query_param=None, state_id=None), "configuration"),
        ],
    )
    def test_never_guesses_missing_configuration(self, kwargs, requires):
        status, details = ImdWeatherProvider(**kwargs).precheck()
        assert status is HealthStatus.MISCONFIGURED
        assert details["requires"] == requires

    def test_documented_fields_are_kept_verbatim_and_never_classified(self):
        payload = [
            {"ID": "A1", "STATION": "Far", "CURR_TEMP": 25, "RH": 80, "WIND_SPEED": 12,
             "WEATHER_CODE": 61, "Latitude": 13.5, "Longitude": 77.6},
            {"ID": "B2", "STATION": "Near", "CURR_TEMP": 24, "RH": 90, "WIND_SPEED": 7,
             "WEATHER_CODE": 63, "MSLP": 1008, "Latitude": 12.93, "Longitude": 77.66},
        ]
        snap = parse_aws(payload, HSR, NOW)

        assert snap.station_id == "B2"
        assert snap.reported["WIND_SPEED"] == 7  # unit undocumented → untouched
        assert snap.wind_speed_kmh is None and snap.wind_gust_kmh is None
        assert snap.temperature_c is None
        assert snap.rain_intensity is None  # WEATHER_CODE table undocumented
        assert snap.observed_at_basis == "received"  # no timestamp is documented

    async def test_rejected_key_is_unauthorized(self):
        provider = ImdWeatherProvider("bad", "X-Key", None, "7")
        provider._client = mock_client(lambda request: httpx.Response(401, json={"error": "API key missing"}))
        response = await provider.fetch(HSR)
        assert response.health_hint is HealthStatus.UNAUTHORIZED

    async def test_key_goes_where_configured_and_nowhere_else(self):
        seen = {}

        def handler(request):
            seen["header"] = request.headers.get("X-Key")
            seen["query"] = dict(request.url.params)
            return httpx.Response(200, json=[{"ID": "B2", "Latitude": 12.93, "Longitude": 77.66}])

        provider = ImdWeatherProvider("secret", "X-Key", None, "7")
        provider._client = mock_client(handler)
        await provider.fetch(HSR)
        assert seen["header"] == "secret"
        assert "secret" not in json.dumps(seen["query"])


class TestDisabledSources:
    @pytest.mark.parametrize("factory", [
        disabled.ksndmc_weather, disabled.mappls_incidents,
        disabled.bengaluru_traffic_police, disabled.bmc_disaster_management,
    ])
    async def test_disabled_sources_are_never_called_and_say_why(self, factory):
        provider = factory()
        response = await provider.fetch(HSR)
        assert response.status is SourceStatus.UNAVAILABLE
        assert response.health_hint is HealthStatus.DISABLED
        assert response.error and len(response.error) > 30


# --------------------------------------------------------------------------
# Mappls
# --------------------------------------------------------------------------


CORRIDOR = ApproachTarget("r1:a", "27th Main Road · north", GeoPoint(lat=12.915, lon=77.651),
                          entry=GeoPoint(lat=12.9189, lon=77.6517))


def route(time_s: float, length_km: float) -> dict:
    # Shape from documentation/mappls/rest-apis-main/predictive-routing-api.md
    return {"trip": {"summary": {"time": time_s, "length": length_km}}}


class TestMappls:
    def test_without_a_key_it_is_misconfigured(self):
        status, _ = MapplsCorridorTrafficProvider(None).precheck()
        assert status is HealthStatus.MISCONFIGURED

    def test_optimal_eta_is_never_treated_as_free_flow(self):
        probe = corridor_probe(HSR, CORRIDOR, route(240, 0.8), route(180, 0.8))
        assert probe.free_flow_speed_kmh is None
        assert probe.congestion is None
        assert probe.reference_travel_time_s == 180
        assert probe.reference_kind == "mappls_optimal_eta"
        assert probe.current_speed_kmh == 12.0  # 0.8 km in 240 s
        assert probe.measurement == "corridor_eta"

    async def test_documented_403_means_quota_not_unauthorized(self):
        provider = MapplsCorridorTrafficProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(403))
        response = await provider.fetch(HSR, FetchContext(approaches=(CORRIDOR,)))
        assert response.health_hint is HealthStatus.UNAVAILABLE
        assert response.details.get("quota_exceeded") is True

    async def test_401_is_unauthorized(self):
        provider = MapplsCorridorTrafficProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(401))
        response = await provider.fetch(HSR, FetchContext(approaches=(CORRIDOR,)))
        assert response.health_hint is HealthStatus.UNAUTHORIZED
        assert response.details["key_problem"] == "rejected"
        assert response.error.startswith("API key rejected")

    async def test_401_keeps_mappls_reason(self):
        # Observed live: a valid key without Predictive Routing enabled.
        body = {"responsecode": 401, "error_code": "ASSET_ACCESS_DENIED", "error": "Api Access Denied"}
        provider = MapplsCorridorTrafficProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(401, json=body))
        response = await provider.fetch(HSR, FetchContext(approaches=(CORRIDOR,)))
        assert "ASSET_ACCESS_DENIED" in response.error
        assert response.details["provider_error"] == "ASSET_ACCESS_DENIED"

    async def test_403_quota_is_a_key_problem(self):
        provider = MapplsCorridorTrafficProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(403))
        response = await provider.fetch(HSR, FetchContext(approaches=(CORRIDOR,)))
        assert response.details["key_problem"] == "limit_reached"
        assert response.error.startswith("API usage limit reached")

    async def test_key_is_redacted_from_errors(self):
        provider = MapplsCorridorTrafficProvider("topsecretkey")
        provider._client = mock_client(lambda request: httpx.Response(500))
        response = await provider.fetch(HSR, FetchContext(approaches=(CORRIDOR,)))
        assert "topsecretkey" not in (response.error or "")


# --------------------------------------------------------------------------
# TomTom
# --------------------------------------------------------------------------


def segment(lat_offset: float = 0.0, **overrides) -> dict:
    base = {
        "frc": "FRC2", "currentSpeed": 20, "freeFlowSpeed": 40, "currentTravelTime": 90,
        "freeFlowTravelTime": 45, "confidence": 0.95, "roadClosure": False,
        "coordinates": {"coordinate": [
            {"latitude": 12.9150 + lat_offset, "longitude": 77.6510},
            {"latitude": 12.9160 + lat_offset, "longitude": 77.6511},
        ]},
    }
    base.update(overrides)
    return base


class TestTomTomFlow:
    TARGET = ApproachTarget("r1:a", "27th Main Road · north", GeoPoint(lat=12.9155, lon=77.65105),
                            geometry=(((77.6510, 12.9150), (77.6511, 12.9160)),))

    def test_matched_segment_on_the_corridor_has_a_small_gap(self):
        probe = parse_segment(segment(), HSR, self.TARGET)
        assert probe.match_distance_m is not None and probe.match_distance_m < 5
        assert probe.congestion == pytest.approx(0.5)

    def test_a_segment_on_another_road_is_flagged_by_distance(self):
        probe = parse_segment(segment(lat_offset=0.01), HSR, self.TARGET)  # ~1 km away
        assert probe.match_distance_m > 500

    def test_missing_free_flow_leaves_congestion_unknown(self):
        probe = parse_segment(segment(freeFlowSpeed=None), HSR, self.TARGET)
        assert probe.free_flow_speed_kmh is None and probe.congestion is None

    def test_incident_request_asks_for_record_ids(self):
        from andon.providers.incidents.tomtom import FIELDS

        assert "properties{id," in FIELDS

    def test_requests_a_zoom_that_matches_local_roads(self):
        from andon.providers.traffic.tomtom import ENDPOINT

        assert "/absolute/18/" in ENDPOINT


class TestKeyFailuresAreLoud:
    """A bad key or an exhausted plan is reported as exactly that — never masked."""

    @pytest.mark.parametrize("status", [401, 403])
    async def test_tomtom_rejected_key(self, status):
        from andon.providers.traffic.tomtom import TomTomTrafficProvider

        provider = TomTomTrafficProvider("secretkey123")
        provider._client = mock_client(lambda request: httpx.Response(status))
        response = await provider.fetch(HSR, FetchContext(approaches=(TestTomTomFlow.TARGET,)))
        assert response.health_hint is HealthStatus.UNAUTHORIZED
        assert response.details["key_problem"] == "rejected"
        assert response.error.startswith("API key rejected — invalid, expired")
        assert "secretkey123" not in response.error

    async def test_tomtom_rate_limit(self):
        from andon.providers.incidents.tomtom import TomTomIncidentProvider

        provider = TomTomIncidentProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(429))
        response = await provider.fetch(HSR)
        assert response.health_hint is HealthStatus.UNAVAILABLE
        assert response.details["key_problem"] == "limit_reached"

    async def test_keyless_source_is_not_blamed_on_a_key(self):
        provider = OpenMeteoWeatherProvider()
        provider._client = mock_client(lambda request: httpx.Response(403))
        response = await provider.fetch(HSR)
        assert "key_problem" not in response.details
        assert "API key" not in (response.error or "")

    async def test_key_problem_is_logged_as_error(self, caplog):
        from andon.providers.traffic.tomtom import TomTomTrafficProvider

        provider = TomTomTrafficProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(403))
        with caplog.at_level("WARNING", logger="andon.providers.base"):
            await provider.fetch(HSR, FetchContext(approaches=(TestTomTomFlow.TARGET,)))
        assert any(r.levelname == "ERROR" and r.getMessage() == "provider key problem" for r in caplog.records)


class TestKeysReplaceMocks:
    def settings(self, **values):
        from andon.config import Settings

        return Settings(_env_file=None, mode="mock", **values)

    def test_mock_mode_without_keys_is_all_mock(self):
        s = self.settings()
        assert s.resolved_traffic_providers == ["mock", "mock_outage"]
        assert s.resolved_incident_providers == ["mock"]
        assert s.resolved_weather_providers == ["mock", "mock_station"]
        assert s.needs_real_roads is False

    def test_a_key_replaces_the_mock_even_in_mock_mode(self):
        s = self.settings(tomtom_api_key="k", mappls_access_token="t")
        assert s.resolved_traffic_providers == ["tomtom", "mappls"]
        assert s.resolved_incident_providers == ["tomtom"]
        assert s.needs_real_roads is True

    def test_weather_keys_replace_their_mocks(self):
        s = self.settings(open_meteo_api_key="k", imd_api_key="k")
        assert s.resolved_weather_providers == ["open_meteo", "imd"]

    def test_auto_mode_uses_the_keyed_provider_not_a_mock(self):
        from andon.config import Settings

        s = Settings(_env_file=None, tomtom_api_key="k")
        assert "mock" not in s.resolved_traffic_providers
        assert "mock" not in s.resolved_incident_providers

    def test_explicit_lists_are_honoured(self):
        s = self.settings(tomtom_api_key="k", traffic_providers="mock")
        assert s.resolved_traffic_providers == ["mock"]

    def test_simulated_roads_are_rebuilt_when_real_roads_are_wanted(self):
        builder = ag.AccessGraphBuilder(source="auto", radius_m=700, max_approaches=6,
                                        max_age_days=30, overpass_endpoints=[], configured_points=[])
        mock_graph = ag.build_mock("r1", HSR, 700, datetime.now(timezone.utc))
        assert builder.needs_rebuild(mock_graph, datetime.now(timezone.utc)) is True


# --------------------------------------------------------------------------
# Open-Meteo
# --------------------------------------------------------------------------


class TestOpenMeteoAccess:
    async def test_commercial_key_uses_the_customer_endpoint(self):
        seen = {}

        def handler(request):
            seen["host"] = request.url.host
            seen["apikey"] = request.url.params.get("apikey")
            return httpx.Response(200, json={"latitude": 12.97, "longitude": 77.56,
                                             "current": {"time": "2026-09-27T07:00", "interval": 900}})

        provider = OpenMeteoWeatherProvider(api_key="paid-key")
        provider._client = mock_client(handler)
        response = await provider.fetch(HSR)

        assert seen == {"host": "customer-api.open-meteo.com", "apikey": "paid-key"}
        assert response.source.licence_note is None
        assert response.data.grid_location == GeoPoint(lat=12.97, lon=77.56)

    def test_free_tier_carries_the_licence_note(self):
        assert "non-commercial" in OpenMeteoWeatherProvider().source.licence_note


# --------------------------------------------------------------------------
# OpenWeatherMap
# --------------------------------------------------------------------------


def owm_payload(**overrides) -> dict:
    base = {
        "coord": {"lon": 77.65, "lat": 12.91},
        "weather": [{"id": 500, "main": "Rain", "description": "light rain"}],
        "main": {"temp": 27.3, "humidity": 80},
        "visibility": 6000,
        "wind": {"speed": 3.5, "deg": 200, "gust": 6.2},
        "rain": {"1h": 1.8},
        "dt": 1790000000,
    }
    base.update(overrides)
    return base


class TestOpenWeatherMap:
    def test_missing_key_is_misconfigured_not_a_network_call(self):
        from andon.providers.weather.openweathermap import OpenWeatherMapProvider

        status, details = OpenWeatherMapProvider(None).precheck()
        assert status is HealthStatus.MISCONFIGURED
        assert "ANDON_OPENWEATHERMAP_API_KEY" in details["reason"]

    async def test_401_is_unauthorized_and_key_is_redacted(self):
        from andon.providers.weather.openweathermap import OpenWeatherMapProvider

        provider = OpenWeatherMapProvider("topsecretkey")
        provider._client = mock_client(lambda request: httpx.Response(401))
        response = await provider.fetch(HSR)
        assert response.health_hint is HealthStatus.UNAUTHORIZED
        assert response.details["key_problem"] == "rejected"
        assert "topsecretkey" not in response.error

    async def test_wind_is_converted_from_metre_per_second_even_under_metric_units(self):
        # OpenWeather documents wind speed/gust as m/s under BOTH standard and
        # metric — only `units=imperial` changes them (to mph).
        from andon.providers.weather.openweathermap import OpenWeatherMapProvider

        provider = OpenWeatherMapProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(200, json=owm_payload()))
        response = await provider.fetch(HSR)
        snap = response.data
        assert snap.wind_speed_kmh == pytest.approx(round(3.5 * 3.6, 1))
        assert snap.wind_gust_kmh == pytest.approx(round(6.2 * 3.6, 1))
        assert snap.temperature_c == 27.3
        assert snap.visibility_m == 6000
        assert snap.precipitation_mm_h == 1.8
        assert snap.condition == "light rain"
        assert snap.grid_location == GeoPoint(lat=12.91, lon=77.65)

    async def test_absent_rain_field_means_no_precipitation_not_zero_fabricated(self):
        from andon.providers.weather.openweathermap import OpenWeatherMapProvider

        payload = owm_payload()
        del payload["rain"]
        provider = OpenWeatherMapProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(200, json=payload))
        response = await provider.fetch(HSR)
        assert response.data.precipitation_mm_h is None

    async def test_no_station_identity_so_it_is_classified_as_model_evidence(self):
        # No station_id/station_name is ever set — the evidence layer routes
        # anything without those through the same "model" path as Open-Meteo.
        from andon.providers.weather.openweathermap import OpenWeatherMapProvider

        provider = OpenWeatherMapProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(200, json=owm_payload()))
        response = await provider.fetch(HSR)
        assert response.data.station_id is None
        assert response.data.station_name is None

    async def test_no_alerts_are_claimed(self):
        from andon.providers.weather.openweathermap import OpenWeatherMapProvider

        provider = OpenWeatherMapProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(200, json=owm_payload()))
        response = await provider.fetch(HSR)
        assert response.data.alerts == []
        assert response.data.alerts_supported is False

    def test_source_declares_correctly(self):
        from andon.providers.weather.openweathermap import OpenWeatherMapProvider

        source = OpenWeatherMapProvider("k").source
        assert source.id == "openweathermap"
        assert source.kind == "weather"


# --------------------------------------------------------------------------
# WeatherAPI.com
# --------------------------------------------------------------------------


def weatherapi_payload(**overrides) -> dict:
    base = {
        "location": {"lat": 12.91, "lon": 77.65},
        "current": {
            "last_updated_epoch": 1790000000,
            "temp_c": 26.8,
            "wind_kph": 14.4,
            "gust_kph": 21.6,
            "precip_mm": 0.3,
            "vis_km": 6.0,
            "humidity": 78,
            "condition": {"text": "Patchy rain nearby", "code": 1063},
        },
    }
    base.update(overrides)
    return base


class TestWeatherApiCom:
    def test_missing_key_is_misconfigured(self):
        from andon.providers.weather.weatherapi import WeatherApiComProvider

        status, details = WeatherApiComProvider(None).precheck()
        assert status is HealthStatus.MISCONFIGURED
        assert "ANDON_WEATHERAPI_KEY" in details["reason"]

    async def test_401_is_unauthorized(self):
        from andon.providers.weather.weatherapi import WeatherApiComProvider

        provider = WeatherApiComProvider("topsecretkey")
        provider._client = mock_client(lambda request: httpx.Response(401))
        response = await provider.fetch(HSR)
        assert response.health_hint is HealthStatus.UNAUTHORIZED
        assert response.details["key_problem"] == "rejected"
        assert "topsecretkey" not in response.error

    async def test_403_is_quota_not_unauthorized(self):
        # WeatherAPI documents 403 as the monthly quota (error 2007), not auth.
        from andon.providers.weather.weatherapi import WeatherApiComProvider

        provider = WeatherApiComProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(403))
        response = await provider.fetch(HSR)
        assert response.health_hint is HealthStatus.UNAVAILABLE
        assert response.details["key_problem"] == "limit_reached"

    async def test_wind_and_visibility_need_no_conversion(self):
        from andon.providers.weather.weatherapi import WeatherApiComProvider

        provider = WeatherApiComProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(200, json=weatherapi_payload()))
        response = await provider.fetch(HSR)
        snap = response.data
        assert snap.wind_speed_kmh == 14.4
        assert snap.wind_gust_kmh == 21.6
        assert snap.visibility_m == 6000
        assert snap.temperature_c == 26.8
        assert snap.condition == "Patchy rain nearby"
        assert snap.grid_location == GeoPoint(lat=12.91, lon=77.65)

    async def test_precip_mm_is_never_treated_as_a_rate(self):
        # No documented time window for precip_mm, unlike Open-Meteo/OpenWeatherMap's mm/h.
        from andon.providers.weather.weatherapi import WeatherApiComProvider

        provider = WeatherApiComProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(200, json=weatherapi_payload()))
        response = await provider.fetch(HSR)
        snap = response.data
        assert snap.precipitation_mm_h is None
        assert snap.reported["precip_mm"] == 0.3
        assert snap.reported["precip_units_documented"] is False

    async def test_no_station_identity_and_no_alerts_claimed(self):
        from andon.providers.weather.weatherapi import WeatherApiComProvider

        provider = WeatherApiComProvider("k")
        provider._client = mock_client(lambda request: httpx.Response(200, json=weatherapi_payload()))
        response = await provider.fetch(HSR)
        assert response.data.station_id is None
        assert response.data.station_name is None
        assert response.data.alerts == []
        assert response.data.alerts_supported is False

    def test_source_declares_correctly(self):
        from andon.providers.weather.weatherapi import WeatherApiComProvider

        source = WeatherApiComProvider("k").source
        assert source.id == "weatherapi"
        assert source.kind == "weather"


# --------------------------------------------------------------------------
# NDMA Sachet
# --------------------------------------------------------------------------


class TestSachetClassification:
    """Ported from the source Apps Script's rlClassifyAlert_ — same fixtures,
    same expected grades, so the port is provably faithful."""

    def test_heavy_rain_keywords(self):
        from andon.providers.weather.ndma_sachet import classify_alert

        assert classify_alert("Heavy to Very Heavy rainfall expected", "IMD Pune") == "heavy"
        assert classify_alert("Cloudburst likely over isolated pockets", "IMD Shimla") == "heavy"

    def test_moderate_rain_keywords(self):
        from andon.providers.weather.ndma_sachet import classify_alert

        assert classify_alert("Moderate Rain with thunder likely", "IMD Lucknow") == "moderate"

    def test_light_rain_is_watch(self):
        from andon.providers.weather.ndma_sachet import classify_alert

        assert classify_alert("Light rain and drizzle expected", "IMD Chennai") == "watch"

    def test_no_rain_keywords_is_ignored(self):
        from andon.providers.weather.ndma_sachet import classify_alert

        assert classify_alert("Heatwave conditions likely over parts of the state", "IMD Bhopal") == "ignore"

    def test_cwc_sender_is_always_a_watch_not_a_rain_alert(self):
        # River-level flood forecasts, not a rain nowcast.
        from andon.providers.weather.ndma_sachet import classify_alert

        assert classify_alert("Severe flood situation, heavy rainfall in catchment", "CWC") == "watch"

    def test_next_24_hour_outlook_is_downgraded_from_heavy_to_watch(self):
        from andon.providers.weather.ndma_sachet import classify_alert

        assert classify_alert("Heavy rain very likely in the next 24 hours", "IMD Guwahati") == "watch"

    def test_imminent_heavy_rain_is_not_downgraded(self):
        # "next 3 hours" is not a day-ahead outlook.
        from andon.providers.weather.ndma_sachet import classify_alert

        assert classify_alert("Heavy rain likely in the next 3 hours", "IMD Lucknow") == "heavy"


class TestSachetFeedParsing:
    def test_rss_ids_are_extracted(self):
        from andon.providers.weather.ndma_sachet import _parse_rss_ids

        ids = _parse_rss_ids((FIXTURES / "sachet_rss_sample.xml").read_text())
        assert ids == ["1790590664714013", "1790589786767013", "1790589787006016"]

    def test_cap_alert_is_parsed_from_the_english_info_block(self):
        from andon.providers.weather.ndma_sachet import _parse_cap

        parsed = _parse_cap("1790590664714013", (FIXTURES / "sachet_cap_sample.xml").read_text())
        assert parsed.level == "moderate"
        assert parsed.sender == "Uttar-Pradesh-SDMA"
        assert "Baghpat" in parsed.headline
        assert parsed.effective.isoformat() == "2026-09-28T15:46:00+05:30"
        assert parsed.expires.isoformat() == "2026-09-28T18:46:00+05:30"

    def test_state_match_is_hyphen_and_case_insensitive(self):
        from andon.providers.weather.ndma_sachet import _parse_cap

        parsed = _parse_cap("1790590664714013", (FIXTURES / "sachet_cap_sample.xml").read_text())
        assert parsed.covers_state("Uttar Pradesh") is True
        assert parsed.covers_state("uttar pradesh") is True
        assert parsed.covers_state("Karnataka") is False

    def test_active_window_is_respected(self):
        from andon.providers.weather.ndma_sachet import _parse_cap

        parsed = _parse_cap("1790590664714013", (FIXTURES / "sachet_cap_sample.xml").read_text())
        before = parsed.effective - timedelta(minutes=1)
        during = parsed.effective + timedelta(minutes=1)
        after = parsed.expires + timedelta(minutes=1)
        assert parsed.active_at(before) is False
        assert parsed.active_at(during) is True
        assert parsed.active_at(after) is False

    def test_malformed_cap_xml_is_skipped_not_raised(self):
        from andon.providers.weather.ndma_sachet import _parse_cap

        assert _parse_cap("x", "<not valid xml") is None


class TestSachetProvider:
    async def test_matching_state_produces_an_alert_with_its_caveat(self):
        from andon.providers.weather.ndma_sachet import NdmaSachetAlertProvider

        provider = NdmaSachetAlertProvider()

        def handler(request):
            if "rss" in str(request.url):
                return httpx.Response(200, text=(FIXTURES / "sachet_rss_sample.xml").read_text())
            if "FetchXMLFile" in str(request.url):
                return httpx.Response(200, text=(FIXTURES / "sachet_cap_sample.xml").read_text())
            if "nominatim" in str(request.url):
                return httpx.Response(200, json={"address": {"state": "Uttar Pradesh", "country_code": "in"}})
            return httpx.Response(404)

        provider._client = mock_client(handler)
        response = await provider.fetch(GeoPoint(lat=28.98, lon=77.70), FetchContext())
        assert response.ok
        assert len(response.data.alerts) >= 1
        alert = response.data.alerts[0]
        assert "state-level, exact area not confirmed" in alert.headline
        assert alert.severity_hint is Severity.MEDIUM

    async def test_non_matching_state_produces_no_alerts(self):
        from andon.providers.weather.ndma_sachet import NdmaSachetAlertProvider

        provider = NdmaSachetAlertProvider()

        def handler(request):
            if "rss" in str(request.url):
                return httpx.Response(200, text=(FIXTURES / "sachet_rss_sample.xml").read_text())
            if "FetchXMLFile" in str(request.url):
                return httpx.Response(200, text=(FIXTURES / "sachet_cap_sample.xml").read_text())
            if "nominatim" in str(request.url):
                return httpx.Response(200, json={"address": {"state": "Karnataka", "country_code": "in"}})
            return httpx.Response(404)

        provider._client = mock_client(handler)
        response = await provider.fetch(HSR, FetchContext())
        assert response.ok
        assert response.data.alerts == []

    async def test_outside_india_never_matches_by_construction(self):
        from andon.providers.weather.ndma_sachet import NdmaSachetAlertProvider

        provider = NdmaSachetAlertProvider()

        def handler(request):
            if "rss" in str(request.url):
                return httpx.Response(200, text=(FIXTURES / "sachet_rss_sample.xml").read_text())
            if "FetchXMLFile" in str(request.url):
                return httpx.Response(200, text=(FIXTURES / "sachet_cap_sample.xml").read_text())
            if "nominatim" in str(request.url):
                return httpx.Response(200, json={"address": {"state": "England", "country_code": "gb"}})
            return httpx.Response(404)

        provider._client = mock_client(handler)
        response = await provider.fetch(GeoPoint(lat=51.5262, lon=-0.0784), FetchContext())
        assert response.ok
        assert response.data.alerts == []

    async def test_state_lookup_is_cached_across_fetches(self):
        # Nominatim's usage policy requires caching results on our side.
        from andon.providers.weather.ndma_sachet import NdmaSachetAlertProvider

        provider = NdmaSachetAlertProvider()
        geocode_calls = []

        def handler(request):
            if "rss" in str(request.url):
                return httpx.Response(200, text=(FIXTURES / "sachet_rss_sample.xml").read_text())
            if "FetchXMLFile" in str(request.url):
                return httpx.Response(200, text=(FIXTURES / "sachet_cap_sample.xml").read_text())
            if "nominatim" in str(request.url):
                geocode_calls.append(1)
                return httpx.Response(200, json={"address": {"state": "Karnataka", "country_code": "in"}})
            return httpx.Response(404)

        provider._client = mock_client(handler)
        await provider.fetch(HSR, FetchContext())
        await provider.fetch(HSR, FetchContext())
        assert len(geocode_calls) == 1

    def test_precheck_needs_no_key(self):
        from andon.providers.weather.ndma_sachet import NdmaSachetAlertProvider

        assert NdmaSachetAlertProvider().precheck() is None

    def test_source_declares_correctly(self):
        from andon.providers.weather.ndma_sachet import NdmaSachetAlertProvider

        source = NdmaSachetAlertProvider().source
        assert source.id == "ndma-sachet"
        assert source.kind == "weather"
        assert source.licence_note is None  # public domain, stated by the feed itself


# --------------------------------------------------------------------------
# RainViewer radar
# --------------------------------------------------------------------------


def _hex_for(dbz: int) -> tuple[int, int, int, int]:
    table = rainviewer.colour_table()
    colour = next(c for c, (lo, hi) in table.items() if lo == hi == dbz)
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5, 7))


class TestRainViewer:
    def test_disabled_until_terms_are_acknowledged(self):
        status, details = rainviewer.RainViewerRadarProvider().precheck()
        assert status is HealthStatus.DISABLED
        assert "personal and educational" in details["reason"]
        assert rainviewer.RainViewerRadarProvider(terms_acknowledged=True).precheck() is None

    def test_ambiguous_colours_decode_to_ranges(self):
        table = rainviewer.colour_table()
        assert table["#00000000"] == (-32.0, -11.0)  # transparent: no meaningful echo
        assert table["#00ff00ff"] == (75.0, 95.0)

    def test_decodes_the_documented_colours_around_the_restaurant(self):
        from PIL import Image

        tile = Image.new("RGBA", (256, 256), _hex_for(20))
        tile.putpixel((128, 128), _hex_for(42))  # the restaurant's cell
        tile.putpixel((0, 0), (1, 2, 3, 255))  # not in the table, and far away
        coverage = Image.new("RGBA", (256, 256), (0, 0, 0, 0))  # all covered

        snap = rainviewer.decode_samples(tile, coverage, HSR, radius_km=3)
        # The restaurant sits on the corner of four cells; one of them is 42 dBZ.
        assert len(snap.site_samples()) == 4
        strongest = snap.site_strongest()
        assert strongest.reflectivity_dbz == 42 and strongest.reflectivity_dbz_max == 42
        assert all(s.reflectivity_dbz in (20, 42) for s in snap.samples)
        assert 1000 < snap.resolution_m < 1300  # zoom 7 at this latitude

    def test_no_coverage_means_no_value_not_no_rain(self):
        from PIL import Image

        tile = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        coverage = Image.new("RGBA", (256, 256), (0, 0, 0, 255))  # black = no coverage
        snap = rainviewer.decode_samples(tile, coverage, HSR, radius_km=2)
        assert all(s.reflectivity_dbz is None and s.covered is False for s in snap.samples)

    def test_unknown_colours_are_counted_not_guessed(self):
        from PIL import Image

        tile = Image.new("RGBA", (256, 256), (1, 2, 3, 255))
        snap = rainviewer.decode_samples(tile, None, HSR, radius_km=2)
        assert snap.undecodable_samples == len(snap.samples)
        assert all(s.reflectivity_dbz is None for s in snap.samples)


# --------------------------------------------------------------------------
# Radar in the situation engine
# --------------------------------------------------------------------------


async def test_radar_becomes_its_own_weather_tile(tmp_path):
    from andon.config import Settings
    from andon.domain.situation import Restaurant
    from andon.engine.normalize import SituationBuilder
    from andon.providers.radar.mock import MockRadarProvider
    from andon.providers.registry import SignalBundle
    from andon.providers.scenario import ScenarioClock

    radar = await MockRadarProvider(ScenarioClock("demo")).fetch(HSR)
    situation = SituationBuilder(Settings(_env_file=None)).build(
        Restaurant(name="T", location=HSR),
        SignalBundle(weather=[], traffic=[], incidents=[], radar=[radar]),
        [],
    )
    tile = situation.weather
    assert tile.source_label.startswith("Radar")
    assert tile.severity is Severity.MEDIUM
    assert "stronger" in tile.detail  # the heavier cell to the south-west
    assert situation.traffic.status is SourceStatus.UNAVAILABLE  # none configured ≠ clear
