"""Change detection decides when we spend money on the LLM."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from andon.config import Settings
from andon.domain.enums import Severity
from andon.domain.situation import SituationReport
from andon.engine import change
from andon.engine.normalize import SituationBuilder
from tests.test_normalize import NOW, RESTAURANT, incidents, ok, traffic, weather
from andon.providers.registry import SignalBundle


@pytest.fixture
def builder() -> SituationBuilder:
    return SituationBuilder(Settings(_env_file=None))


def situation_for(builder, mm_h: float, congestions: list[float]):
    bundle = SignalBundle(
        weather=[ok("weather", weather(mm_h=mm_h))],
        traffic=[ok("traffic", traffic(congestions))],
        incidents=[ok("incidents", incidents())],
    )
    result = builder.build(RESTAURANT, bundle, [], now=NOW)
    result.fingerprint = change.fingerprint(result)
    return result


class TestFingerprint:
    def test_is_stable_for_identical_input(self, builder):
        a = situation_for(builder, 3.0, [0.3, 0.3])
        b = situation_for(builder, 3.0, [0.3, 0.3])
        assert a.fingerprint == b.fingerprint

    def test_ignores_noise_inside_the_same_severity_band(self, builder):
        a = situation_for(builder, 3.0, [0.30, 0.30])
        b = situation_for(builder, 3.4, [0.32, 0.31])
        assert a.fingerprint == b.fingerprint

    def test_changes_when_a_severity_band_is_crossed(self, builder):
        light = situation_for(builder, 2.0, [0.3, 0.3])
        heavy = situation_for(builder, 9.0, [0.3, 0.3])
        assert light.fingerprint != heavy.fingerprint

    def test_changes_when_a_source_goes_down(self, builder):
        healthy = situation_for(builder, 3.0, [0.3, 0.3])
        bundle = SignalBundle(
            weather=[ok("weather", weather(mm_h=3.0))],
            traffic=[ok("traffic", traffic([]))],  # no usable probes -> unavailable
            incidents=[ok("incidents", incidents())],
        )
        degraded = builder.build(RESTAURANT, bundle, [], now=NOW)
        degraded.fingerprint = change.fingerprint(degraded)

        assert healthy.fingerprint != degraded.fingerprint


class TestShouldRegenerate:
    def report(self, minutes_old: float = 1.0) -> SituationReport:
        return SituationReport(
            situation_title="t",
            summary="s",
            generated_at=NOW - timedelta(minutes=minutes_old),
        )

    def test_first_request_for_a_location_always_generates(self):
        regenerate, reason = change.should_regenerate(
            current_fingerprint="abc",
            cached_fingerprint=None,
            cached_report=None,
            max_age_s=1800,
            now=NOW,
        )
        assert regenerate
        assert "no cached report" in reason

    def test_unchanged_situation_reuses_the_report(self):
        regenerate, reason = change.should_regenerate(
            current_fingerprint="abc",
            cached_fingerprint="abc",
            cached_report=self.report(),
            max_age_s=1800,
            now=NOW,
        )
        assert not regenerate
        assert "unchanged" in reason

    def test_changed_situation_regenerates(self):
        regenerate, reason = change.should_regenerate(
            current_fingerprint="def",
            cached_fingerprint="abc",
            cached_report=self.report(),
            max_age_s=1800,
            now=NOW,
        )
        assert regenerate
        assert "changed materially" in reason

    def test_stale_report_regenerates_even_when_unchanged(self):
        regenerate, reason = change.should_regenerate(
            current_fingerprint="abc",
            cached_fingerprint="abc",
            cached_report=self.report(minutes_old=40),
            max_age_s=1800,
            now=NOW,
        )
        assert regenerate
        assert "40 min old" in reason

    def test_force_overrides_an_unchanged_situation(self):
        regenerate, _ = change.should_regenerate(
            current_fingerprint="abc",
            cached_fingerprint="abc",
            cached_report=self.report(),
            max_age_s=1800,
            now=NOW,
            force=True,
        )
        assert regenerate

    def test_naive_timestamps_are_treated_as_utc(self):
        report = SituationReport(
            situation_title="t",
            summary="s",
            generated_at=datetime(2026, 9, 18, 14, 29),  # naive
        )
        regenerate, _ = change.should_regenerate(
            current_fingerprint="abc",
            cached_fingerprint="abc",
            cached_report=report,
            max_age_s=1800,
            now=datetime(2026, 9, 18, 14, 30, tzinfo=timezone.utc),
        )
        assert not regenerate


def test_severity_change_alone_moves_the_fingerprint(builder):
    calm = situation_for(builder, 0.0, [0.05, 0.05])
    assert calm.overall.level is Severity.NONE
    busy = situation_for(builder, 0.0, [0.8, 0.8])
    assert busy.overall.level is not Severity.NONE
    assert calm.fingerprint != busy.fingerprint
