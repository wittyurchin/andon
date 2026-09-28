"""Deterministic scenario engine behind the mock providers.

Mock data has one job: let the whole vertical slice run without credentials and
still exercise every code path. So the mocks are driven by a single shared
intensity curve — when mock weather says "heavy rain", mock traffic and mock
incidents agree with it. Everything is a pure function of elapsed time, which
makes runs reproducible and makes trends actually move while you watch.

Nothing here ever reaches the UI unlabelled: mock providers report
``mode="mock"`` and the dashboard shows it on every card.
"""

from __future__ import annotations

import hashlib
import math
import time

from ..config import MockScenario


class ScenarioClock:
    """Shared intensity curve, 0.0 (benign) .. 1.0 (severe)."""

    def __init__(
        self,
        scenario: MockScenario = "deteriorating",
        ramp_minutes: float = 20.0,
        monotonic=time.monotonic,
    ) -> None:
        self.scenario = scenario
        self.ramp_minutes = max(1.0, ramp_minutes)
        # Injectable so tests can move time forward deterministically.
        self._monotonic = monotonic
        self._started = monotonic()

    def elapsed_minutes(self) -> float:
        return (self._monotonic() - self._started) / 60.0

    def advance(self, minutes: float) -> None:
        """Move the scenario forward (tests and demos only)."""
        self._started -= minutes * 60.0

    # -- the "demo" scenario ----------------------------------------------
    #
    # A fixed, deliberately mixed picture for demos and local development:
    #   * moderate rain at the kitchen, intensifying over the past hour
    #   * the forecast model over-reads it as heavy — a genuine source conflict
    #   * traffic on the first approach worsening, the others steady
    #   * waterlogging on the second approach, present from the start
    #   * an accident on the third approach appearing a few minutes in
    #   * one traffic feed simulating an outage

    DEMO_MODEL_BIAS = 0.22
    DEMO_ACCIDENT_AFTER_MIN = 3.0

    def demo_rain(self, offset_minutes: float = 0.0) -> float:
        t = self.elapsed_minutes() + offset_minutes
        if t < 0:
            # 60 min ago light (0.35), rising to moderate (0.50) now.
            return max(0.0, 0.50 - 0.15 * min(60.0, -t) / 60.0)
        return 0.50 + 0.02 * math.sin(t / 5.0)

    def demo_approach_congestion(self, index: int, offset_minutes: float = 0.0) -> float:
        t = self.elapsed_minutes() + offset_minutes
        if index == 0:
            # Began worsening 30 min before start; tips into heavy after the ramp.
            progress = min(1.0, max(0.0, (t + 30.0) / (30.0 + self.ramp_minutes)))
            eased = progress * progress * (3 - 2 * progress)
            return 0.25 + 0.47 * eased
        steady = (0.18, 0.26, 0.22, 0.30, 0.20)
        return steady[(index - 1) % len(steady)] + 0.02 * math.sin((t + index) / 6.0)

    def demo_accident_active(self, offset_minutes: float = 0.0) -> bool:
        return self.elapsed_minutes() + offset_minutes >= self.DEMO_ACCIDENT_AFTER_MIN

    def intensity(self, offset_minutes: float = 0.0) -> float:
        """Intensity ``offset_minutes`` from now (negative = past)."""
        if self.scenario == "demo":
            return self.demo_rain(offset_minutes)
        t = max(0.0, self.elapsed_minutes() + offset_minutes)
        progress = min(1.0, t / self.ramp_minutes)
        # Gentle S-curve so the ramp does not look like a straight line.
        eased = progress * progress * (3 - 2 * progress)

        if self.scenario == "calm":
            # Zero, not "nearly zero": with per-site jitter and wobble layered
            # on top, anything higher drizzles on some locations.
            base = 0.0
        elif self.scenario == "storm":
            base = 0.88
        elif self.scenario == "clearing":
            base = 0.92 - 0.82 * eased
        else:  # deteriorating
            base = 0.10 + 0.82 * eased

        # Small oscillation keeps consecutive refreshes from looking frozen.
        wobble = 0.03 * math.sin((t + offset_minutes) / 7.0)
        return max(0.0, min(1.0, base + wobble))


def site_jitter(location_key: str, salt: str, spread: float = 0.08) -> float:
    """Stable per-location offset in ``[-spread, +spread]``.

    Two restaurants a few km apart should not produce byte-identical mock data.
    """
    digest = hashlib.sha256(f"{location_key}:{salt}".encode()).digest()
    unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF  # 0..1
    return (unit * 2 - 1) * spread


def pseudo_local_hour(lon: float, epoch_s: float | None = None) -> float:
    """Approximate local hour from longitude — good enough for mock rush hours."""
    now = epoch_s if epoch_s is not None else time.time()
    utc_hour = (now % 86400) / 3600.0
    return (utc_hour + lon / 15.0) % 24.0


def rush_hour_factor(local_hour: float) -> float:
    """0.0 off-peak .. 1.0 at the centre of a meal-time traffic peak."""
    peaks = ((13.0, 1.6, 0.55), (19.5, 2.0, 1.0), (9.0, 1.5, 0.45))
    factor = 0.0
    for centre, width, height in peaks:
        distance = min(abs(local_hour - centre), 24 - abs(local_hour - centre))
        factor = max(factor, height * math.exp(-((distance / width) ** 2)))
    return min(1.0, factor)
