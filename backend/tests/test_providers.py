"""Provider-layer behaviour: failures are contained, secrets are not leaked,
and caching keeps external call volume down."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from andon.domain.enums import Severity, SourceStatus
from andon.domain.signals import GeoPoint, SourceRef, WeatherSnapshot
from andon.providers.base import WeatherProvider, redact
from andon.providers.cache import CachedProvider
from andon.providers.scenario import ScenarioClock, rush_hour_factor, site_jitter
from andon.providers.traffic.mock import MockTrafficProvider
from andon.providers.weather.awc_metar import (
    AwcMetarWeatherProvider,
    parse_visibility,
    parse_weather,
)
from andon.providers.weather.mock import MockWeatherProvider
from andon.providers.weather.open_meteo import OpenMeteoWeatherProvider

POINT = GeoPoint(lat=12.9716, lon=77.5946)


class CountingProvider(WeatherProvider):
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    @property
    def source(self) -> SourceRef:
        return SourceRef(id="counter", name="Counter", kind="weather", mode="mock")

    async def _fetch(self, point: GeoPoint, context=None) -> WeatherSnapshot:
        self.calls += 1
        if self.fail:
            raise ConnectionError("upstream refused the connection")
        return WeatherSnapshot(observed_at=datetime.now(timezone.utc), precipitation_mm_h=1.0)


class TestFailureContainment:
    async def test_provider_failure_becomes_an_unavailable_response(self):
        response = await CountingProvider(fail=True).fetch(POINT)

        assert response.status is SourceStatus.UNAVAILABLE
        assert response.data is None
        assert "upstream refused" in response.error
        assert response.latency_ms is not None

    async def test_success_is_wrapped_with_provenance(self):
        response = await CountingProvider().fetch(POINT)

        assert response.ok
        assert response.source.id == "counter"
        assert response.observed_at is not None


class TestSecretRedaction:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://api.x.com/v1?key=abc123&point=1,2", "https://api.x.com/v1?key=***&point=1,2"),
            ("https://api.x.com/v1?point=1,2&api_key=s3cret", "https://api.x.com/v1?point=1,2&api_key=***"),
            ("https://api.x.com/v1?subscription-key=zzz", "https://api.x.com/v1?subscription-key=***"),
            ("https://api.x.com/v1?token=tok_9", "https://api.x.com/v1?token=***"),
        ],
    )
    def test_query_credentials_are_masked(self, raw, expected):
        assert redact(raw) == expected

    def test_harmless_text_is_untouched(self):
        assert redact("connection timed out after 8s") == "connection timed out after 8s"

    async def test_error_responses_are_redacted(self):
        class LeakyProvider(CountingProvider):
            async def _fetch(self, point: GeoPoint, context=None) -> WeatherSnapshot:
                raise RuntimeError("401 for url 'https://api.tomtom.com/x?key=supersecret&p=1'")

        response = await LeakyProvider().fetch(POINT)

        assert "supersecret" not in response.error
        assert "key=***" in response.error


class TestCaching:
    async def test_repeat_calls_within_ttl_hit_the_cache(self):
        inner = CountingProvider()
        cached = CachedProvider(inner, ttl_s=300)

        first = await cached.fetch(POINT)
        second = await cached.fetch(POINT)

        assert inner.calls == 1
        assert first.cached is False
        assert second.cached is True

    async def test_invalidate_forces_a_refetch(self):
        inner = CountingProvider()
        cached = CachedProvider(inner, ttl_s=300)

        await cached.fetch(POINT)
        cached.invalidate(POINT)
        await cached.fetch(POINT)

        assert inner.calls == 2

    async def test_nearby_but_distinct_locations_do_not_share_a_cache_entry(self):
        inner = CountingProvider()
        cached = CachedProvider(inner, ttl_s=300)

        await cached.fetch(POINT)
        await cached.fetch(GeoPoint(lat=19.0760, lon=72.8777))

        assert inner.calls == 2

    async def test_failures_are_not_cached(self):
        inner = CountingProvider(fail=True)
        cached = CachedProvider(inner, ttl_s=300)

        await cached.fetch(POINT)
        await cached.fetch(POINT)

        assert inner.calls == 2  # a broken source is retried, not remembered


class TestMockProviders:
    async def test_weather_mock_is_coherent_with_its_scenario(self):
        storm = MockWeatherProvider(ScenarioClock("storm"))
        calm = MockWeatherProvider(ScenarioClock("calm"))

        storm_data = (await storm.fetch(POINT)).data
        calm_data = (await calm.fetch(POINT)).data

        assert storm_data.precipitation_mm_h > 10
        assert calm_data.precipitation_mm_h == 0
        assert storm_data.visibility_m < calm_data.visibility_m

    async def test_model_mock_labels_every_block_as_model_output(self):
        """A model's value for the past is still a model's value — never "observed"."""
        data = (await MockWeatherProvider(ScenarioClock("deteriorating")).fetch(POINT)).data

        assert {p.kind.value for p in data.recent} == {"forecast"}
        assert {p.kind.value for p in data.outlook} == {"forecast"}
        assert data.recent[-1].at <= data.outlook[0].at
        assert data.station_name is None and data.grid_location is not None

    async def test_traffic_mock_speeds_stay_physically_plausible(self):
        data = (await MockTrafficProvider(ScenarioClock("storm")).fetch(POINT)).data

        assert len(data.probes) == 5
        for probe in data.probes:
            assert 0 < probe.current_speed_kmh <= probe.free_flow_speed_kmh
            assert probe.congestion <= 0.9
            # Gridlock still creeps; absurd derived travel times are a tell of
            # a mock that has run away from reality.
            assert probe.delay_pct < 1200

    async def test_traffic_mock_does_not_invent_street_names(self):
        data = (await MockTrafficProvider(ScenarioClock("storm")).fetch(POINT)).data
        assert all(probe.road_name is None for probe in data.probes)

    def test_site_jitter_is_stable_per_location(self):
        a = site_jitter("12.9716,77.5946", "traffic")
        b = site_jitter("12.9716,77.5946", "traffic")
        c = site_jitter("19.0760,72.8777", "traffic")

        assert a == b
        assert a != c
        assert -0.08 <= a <= 0.08

    def test_rush_hour_peaks_at_meal_times(self):
        assert rush_hour_factor(19.5) > rush_hour_factor(4.0)
        assert rush_hour_factor(13.0) > rush_hour_factor(16.5)
        assert 0.0 <= rush_hour_factor(3.0) <= 1.0


class TestOpenMeteoParsing:
    """Parsing is tested against a captured payload — no network in tests."""

    PAYLOAD = {
        "current": {
            "time": "2026-09-18T14:00",
            "interval": 900,
            "precipitation": 0.30,  # mm per 15 min -> 1.2 mm/h
            "weather_code": 63,
            "wind_speed_10m": 14.8,
            "wind_gusts_10m": 31.3,
            "temperature_2m": 22.3,
        },
        "minutely_15": {
            "time": [
                "2026-09-18T13:30",
                "2026-09-18T13:45",
                "2026-09-18T14:00",
                "2026-09-18T14:15",
                "2026-09-18T14:30",
            ],
            "precipitation": [0.3, 0.3, 0.3, 0.6, 0.9],
            "precipitation_probability": [86, 86, 86, 86, 85],
            "visibility": [2600.0, 2260.0, 1900.0, 2160.0, 2440.0],
            "wind_gusts_10m": [28.8, 30.2, 31.3, 33.1, 34.9],
        },
    }

    def test_accumulation_is_converted_to_a_rate(self):
        snapshot = OpenMeteoWeatherProvider()._parse(self.PAYLOAD)
        assert snapshot.precipitation_mm_h == pytest.approx(1.2)

    def test_every_block_is_model_output_not_observation(self):
        """Open-Meteo's past blocks are model analysis. Calling them "observed"
        is exactly the blurring the evidence rules forbid."""
        snapshot = OpenMeteoWeatherProvider()._parse(self.PAYLOAD)

        assert len(snapshot.recent) == 3
        assert len(snapshot.outlook) == 2
        assert all(p.kind.value == "forecast" for p in snapshot.recent + snapshot.outlook)
        # 0.9 mm per 15-minute block is 3.6 mm/h.
        assert snapshot.outlook[-1].precipitation_mm_h == pytest.approx(3.6)

    def test_condition_code_is_translated(self):
        assert OpenMeteoWeatherProvider()._parse(self.PAYLOAD).condition == "Rain"

    def test_absence_of_an_alert_feed_is_declared_not_assumed(self):
        snapshot = OpenMeteoWeatherProvider()._parse(self.PAYLOAD)

        assert snapshot.alerts == []
        assert snapshot.alerts_supported is False


class TestMetarWeatherParsing:
    """METAR reports intensity as a band, not a rate. The provider must carry
    the band through rather than inventing millimetres."""

    # Two stations so nearest-selection is exercised; VOBG is ~4 km from the
    # test point, VOBL ~33 km.
    PAYLOAD = [
        {
            "icaoId": "VOBL", "name": "Bangaluru Intl, KA, IN",
            "lat": 13.1979, "lon": 77.7063, "obsTime": 1789743600,
            "temp": 28, "wspd": 5, "wgst": None, "visib": 3.73,
            "wxString": "+RA",
        },
        {
            "icaoId": "VOBG", "name": "Bangaluru/Hal Arpt, KA, IN",
            "lat": 12.9497, "lon": 77.6685, "obsTime": 1789741800,
            "temp": 27, "wspd": 3, "wgst": None, "visib": 3.11,
            "wxString": "-RA BR",
        },
        {
            "icaoId": "VOBG", "name": "Bangaluru/Hal Arpt, KA, IN",
            "lat": 12.9497, "lon": 77.6685, "obsTime": 1789743600,
            "temp": 27, "wspd": 3, "wgst": 9, "visib": 2.49,
            "wxString": "RA BR",
        },
    ]
    POINT = GeoPoint(lat=12.912233, lon=77.651282)

    @pytest.mark.parametrize(
        ("wx", "expected"),
        [
            (None, Severity.NONE),
            ("", Severity.NONE),
            ("BR", Severity.NONE),       # mist is not precipitation
            ("HZ", Severity.NONE),       # haze is not precipitation
            ("-RA", Severity.LOW),
            ("RA", Severity.MEDIUM),
            ("+RA", Severity.HIGH),
            ("-SHRA", Severity.LOW),
            ("TSRA", Severity.MEDIUM),
            ("+TSRA", Severity.SEVERE),  # thunderstorm escalates heavy rain
            ("TS BR", Severity.MEDIUM),  # thunderstorm with no rain reported
            ("GR", Severity.HIGH),       # hail
            ("-RA BR", Severity.LOW),
        ],
    )
    def test_intensity_bands(self, wx, expected):
        assert parse_weather(wx)[0] is expected

    def test_obscurations_are_described_but_not_counted_as_rain(self):
        severity, description = parse_weather("HZ")
        assert severity is Severity.NONE
        assert "haze" in description.lower()

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(3.11, 5005), (2.49, 4007), ("6+", 9656), (None, None), ("junk", None)],
    )
    def test_visibility_converts_statute_miles_to_metres(self, raw, expected):
        assert parse_visibility(raw) == expected

    def test_picks_the_nearest_station_not_the_first(self):
        snapshot = AwcMetarWeatherProvider()._build(self.PAYLOAD, self.POINT)

        assert "VOBG" in snapshot.station_name
        assert snapshot.station_distance_km < 10

    def test_reports_a_band_and_never_a_fabricated_rate(self):
        snapshot = AwcMetarWeatherProvider()._build(self.PAYLOAD, self.POINT)

        assert snapshot.rain_intensity is Severity.MEDIUM  # latest VOBG report
        assert snapshot.precipitation_mm_h is None

    def test_history_is_observed_and_carries_bands(self):
        snapshot = AwcMetarWeatherProvider()._build(self.PAYLOAD, self.POINT)

        assert len(snapshot.recent) == 2  # both VOBG reports, oldest first
        assert [p.rain_intensity for p in snapshot.recent] == [Severity.LOW, Severity.MEDIUM]
        assert all(p.kind.value == "observed" for p in snapshot.recent)
        assert all(p.precipitation_mm_h is None for p in snapshot.recent)

    def test_has_no_forecast_and_declares_no_alert_feed(self):
        snapshot = AwcMetarWeatherProvider()._build(self.PAYLOAD, self.POINT)

        assert snapshot.outlook == []
        assert snapshot.alerts_supported is False

    def test_knots_are_converted_to_kmh(self):
        snapshot = AwcMetarWeatherProvider()._build(self.PAYLOAD, self.POINT)
        assert snapshot.wind_gust_kmh == pytest.approx(16.7, abs=0.1)  # 9 kt

    def test_empty_response_fails_loudly_rather_than_returning_nothing(self):
        with pytest.raises(RuntimeError, match="no station coordinates|coordinates"):
            AwcMetarWeatherProvider()._build([{"icaoId": "X"}], self.POINT)
