"""The deterministic layer is what the LLM is trusted *not* to do, so it is
the part that needs tests."""

from __future__ import annotations

import pytest

from andon.domain.enums import Confidence, Freshness, IncidentCategory, Severity, Trend
from andon.engine import rules


@pytest.mark.parametrize(
    ("mm_per_hour", "expected"),
    [
        (None, None),
        (0.0, Severity.NONE),
        (0.04, Severity.NONE),
        (0.5, Severity.LOW),
        (2.4, Severity.LOW),
        (2.5, Severity.MEDIUM),
        (7.5, Severity.MEDIUM),
        (7.6, Severity.HIGH),
        (24.9, Severity.HIGH),
        (60.0, Severity.SEVERE),
    ],
)
def test_rain_severity_bands(mm_per_hour, expected):
    assert rules.rain_severity(mm_per_hour) is expected


@pytest.mark.parametrize(
    ("gust", "expected"),
    [(10, Severity.NONE), (30, Severity.LOW), (50, Severity.MEDIUM), (70, Severity.HIGH), (120, Severity.SEVERE)],
)
def test_wind_severity_bands(gust, expected):
    assert rules.wind_severity(gust) is expected


@pytest.mark.parametrize(
    ("metres", "expected"),
    [(10000, Severity.NONE), (3000, Severity.LOW), (1500, Severity.MEDIUM), (700, Severity.HIGH), (200, Severity.SEVERE)],
)
def test_visibility_severity_is_inverted(metres, expected):
    assert rules.visibility_severity(metres) is expected


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0.0, Severity.NONE), (0.2, Severity.LOW), (0.45, Severity.MEDIUM), (0.6, Severity.HIGH), (0.9, Severity.SEVERE)],
)
def test_congestion_bands(index, expected):
    assert rules.congestion_severity(index) is expected


def test_missing_values_stay_unknown_rather_than_none_severity():
    assert rules.rain_severity(None) is None
    assert rules.congestion_severity(None) is None
    assert rules.visibility_severity(None) is None


def test_incident_severity_attenuates_with_distance():
    close = rules.incident_severity(IncidentCategory.WATERLOGGING, 0.4)
    mid = rules.incident_severity(IncidentCategory.WATERLOGGING, 2.0)
    far = rules.incident_severity(IncidentCategory.WATERLOGGING, 4.0)
    very_far = rules.incident_severity(IncidentCategory.WATERLOGGING, 20.0)

    assert close is Severity.HIGH
    assert close.rank > mid.rank > far.rank >= very_far.rank


def test_provider_hint_can_raise_but_not_lower_a_category():
    raised = rules.incident_severity(IncidentCategory.CONSTRUCTION, 0.5, Severity.HIGH)
    lowered = rules.incident_severity(IncidentCategory.ROAD_CLOSURE, 0.5, Severity.LOW)

    assert raised is Severity.HIGH
    assert lowered is Severity.HIGH  # category floor wins


class TestOverall:
    def test_no_signals_means_none(self):
        assert rules.overall_from_signals([]) == (Severity.NONE, 0)

    def test_all_clear(self):
        level, score = rules.overall_from_signals([Severity.NONE] * 3)
        assert level is Severity.NONE
        assert score == 0

    def test_one_heavy_signal_alone_is_moderate(self):
        level, _ = rules.overall_from_signals([Severity.HIGH, Severity.NONE, Severity.NONE])
        assert level is Severity.MEDIUM

    def test_two_heavy_signals_compound_to_high(self):
        level, _ = rules.overall_from_signals([Severity.HIGH, Severity.HIGH, Severity.NONE])
        assert level is Severity.HIGH

    def test_three_heavy_signals_are_severe(self):
        level, score = rules.overall_from_signals([Severity.HIGH] * 3)
        assert level is Severity.SEVERE
        assert score == 100

    def test_score_is_monotonic_in_severity(self):
        scores = [
            rules.overall_from_signals([s, Severity.NONE, Severity.NONE])[1]
            for s in (Severity.NONE, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.SEVERE)
        ]
        assert scores == sorted(scores)
        assert len(set(scores)) == len(scores)


class TestFreshness:
    def test_bands(self):
        assert rules.freshness_of(60) is Freshness.FRESH
        assert rules.freshness_of(600) is Freshness.RECENT
        assert rules.freshness_of(1800) is Freshness.AGING
        assert rules.freshness_of(4000) is Freshness.STALE

    def test_unknown_age(self):
        assert rules.freshness_of(None) is Freshness.UNKNOWN

    def test_forecast_anchored_future_timestamps_are_fresh(self):
        assert rules.freshness_of(-120) is Freshness.FRESH

    def test_stale_data_lowers_confidence(self):
        assert rules.adjust_confidence(Confidence.HIGH, Freshness.FRESH) is Confidence.HIGH
        assert rules.adjust_confidence(Confidence.HIGH, Freshness.AGING) is Confidence.MEDIUM
        assert rules.adjust_confidence(Confidence.HIGH, Freshness.STALE) is Confidence.LOW
        assert rules.adjust_confidence(Confidence.MEDIUM, Freshness.STALE) is Confidence.LOW


class TestTrend:
    def test_dead_band_reads_as_steady(self):
        assert rules.trend_from_delta(0.03, True, 0.6) is Trend.STEADY

    def test_rising_bad_thing_is_worsening(self):
        assert rules.trend_from_delta(3.0, True, 0.6) is Trend.WORSENING

    def test_falling_bad_thing_is_improving(self):
        assert rules.trend_from_delta(-3.0, True, 0.6) is Trend.IMPROVING

    def test_no_data_is_unknown_not_steady(self):
        assert rules.trend_from_delta(None, True, 0.6) is Trend.UNKNOWN


class TestEnumOrdering:
    """Regression guard: these are ``str`` enums, so the default comparison is
    alphabetical — "none" would outrank "high"."""

    def test_severity_compares_by_rank_not_alphabetically(self):
        assert max(Severity.HIGH, Severity.NONE) is Severity.HIGH
        assert min(Severity.LOW, Severity.SEVERE) is Severity.LOW
        assert sorted([Severity.NONE, Severity.SEVERE, Severity.LOW]) == [
            Severity.NONE,
            Severity.LOW,
            Severity.SEVERE,
        ]

    def test_confidence_compares_by_rank(self):
        assert max(Confidence.HIGH, Confidence.LOW) is Confidence.HIGH
        assert min(Confidence.MEDIUM, Confidence.LOW) is Confidence.LOW

    def test_from_rank_clamps_out_of_range_values(self):
        assert Severity.from_rank(-3) is Severity.NONE
        assert Severity.from_rank(99) is Severity.SEVERE
        assert Confidence.from_rank(-1) is Confidence.LOW
