"""Source-specific freshness policies.

Each class of evidence goes stale at a different age: a traffic reading is old
after minutes, a station report after its reporting interval, a forecast after
the next model run. The thresholds are engineering defaults (see
``config.DEFAULT_FRESHNESS_POLICIES``), configurable per class and per source.

A stale observation stays stored and visible — it is marked stale, never
dropped and never silently treated as current.
"""

from __future__ import annotations

from ..domain.enums import Freshness


def policy_class(category: str, kind: str) -> str:
    if kind == "forecast":
        return "weather_model"
    if category.startswith("radar."):
        return "radar"
    if category.startswith("traffic."):
        return "traffic"
    if category.startswith("incident"):
        return "incident"
    return "weather_station"


class FreshnessPolicy:
    def __init__(self, policies: dict[str, int], overrides: dict[str, int] | None = None) -> None:
        self._policies = dict(policies)
        self._overrides = dict(overrides or {})

    def stale_after(self, source_id: str, category: str, kind: str = "observation") -> int:
        if source_id in self._overrides:
            return self._overrides[source_id]
        return self._policies.get(policy_class(category, kind), 900)

    @staticmethod
    def grade(age_seconds: int | None, stale_after: int) -> Freshness:
        """Fresh/recent/aging in thirds of the policy window, then stale."""
        if age_seconds is None:
            return Freshness.UNKNOWN
        if age_seconds < 0:
            return Freshness.FRESH  # forecast-anchored timestamps can be ahead
        if age_seconds < stale_after / 3:
            return Freshness.FRESH
        if age_seconds < 2 * stale_after / 3:
            return Freshness.RECENT
        if age_seconds < stale_after:
            return Freshness.AGING
        return Freshness.STALE

    def grade_for(self, source_id: str, category: str, kind: str, age_seconds: int | None) -> Freshness:
        return self.grade(age_seconds, self.stale_after(source_id, category, kind))
