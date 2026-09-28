"""Vocabulary shared by every layer of the system.

These enums are the contract between providers, the rules engine, the LLM and
the UI. Providers never leak their own vocabulary past the normalization layer.
"""

from __future__ import annotations

from enum import Enum


class Severity(str, Enum):
    """How disruptive a signal is.

    Ordering is by rank, not by the string value — without the comparison
    operators below, ``max(Severity.HIGH, Severity.NONE)`` would return
    ``NONE`` because "none" sorts after "high" alphabetically.
    """

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    SEVERE = "severe"

    @property
    def rank(self) -> int:
        return _SEVERITY_ORDER[self]

    @classmethod
    def from_rank(cls, rank: int) -> "Severity":
        rank = max(0, min(rank, len(_SEVERITY_ORDER) - 1))
        return _RANK_TO_SEVERITY[rank]

    def __lt__(self, other) -> bool:
        if isinstance(other, Severity):
            return self.rank < other.rank
        return NotImplemented

    def __le__(self, other) -> bool:
        if isinstance(other, Severity):
            return self.rank <= other.rank
        return NotImplemented

    def __gt__(self, other) -> bool:
        if isinstance(other, Severity):
            return self.rank > other.rank
        return NotImplemented

    def __ge__(self, other) -> bool:
        if isinstance(other, Severity):
            return self.rank >= other.rank
        return NotImplemented

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.NONE: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.SEVERE: 4,
}
_RANK_TO_SEVERITY = {rank: sev for sev, rank in _SEVERITY_ORDER.items()}


class Trend(str, Enum):
    IMPROVING = "improving"
    STEADY = "steady"
    WORSENING = "worsening"
    UNKNOWN = "unknown"


class Freshness(str, Enum):
    FRESH = "fresh"
    RECENT = "recent"
    AGING = "aging"
    STALE = "stale"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2}[self.value]

    @classmethod
    def from_rank(cls, rank: int) -> "Confidence":
        rank = max(0, min(rank, 2))
        return [Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH][rank]

    def __lt__(self, other) -> bool:
        if isinstance(other, Confidence):
            return self.rank < other.rank
        return NotImplemented

    def __le__(self, other) -> bool:
        if isinstance(other, Confidence):
            return self.rank <= other.rank
        return NotImplemented

    def __gt__(self, other) -> bool:
        if isinstance(other, Confidence):
            return self.rank > other.rank
        return NotImplemented

    def __ge__(self, other) -> bool:
        if isinstance(other, Confidence):
            return self.rank >= other.rank
        return NotImplemented


class EvidenceKind(str, Enum):
    """Epistemic status of a statement. The UI and the LLM must keep these apart."""

    OBSERVED = "observed"
    FORECAST = "forecast"
    INFERRED = "inferred"


class SourceStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class HealthStatus(str, Enum):
    """Operational state of one source, as recorded by the source health registry.

    ``disabled`` is ours: the adapter exists but is deliberately never called
    because no verified, authorised access path exists (see
    docs/provider-verification.md). It is distinct from ``misconfigured``,
    which means "could work, but a setting is missing".
    """

    HEALTHY = "healthy"
    STALE = "stale"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    MISCONFIGURED = "misconfigured"
    UNAUTHORIZED = "unauthorized"
    DISABLED = "disabled"

    @property
    def usable(self) -> bool:
        """Does this source currently contribute evidence at all?"""
        return self in (HealthStatus.HEALTHY, HealthStatus.STALE, HealthStatus.DEGRADED)


class SourceType(str, Enum):
    """Where a piece of evidence comes from, at the coarsest level."""

    LIVE = "live"  # an external provider, called at runtime
    MOCK = "mock"  # simulated for development — never real
    DERIVED = "derived"  # computed by us from other data (e.g. the access graph)
    CONFIGURED = "configured"  # entered by an operator, e.g. approximate road points


class Applicability(str, Enum):
    """How far a piece of spatial evidence can be applied to the restaurant."""

    AT_SITE = "at_site"  # within ~150 m
    LOCAL = "local"  # within ~1 km
    NEARBY = "nearby"  # within ~5 km
    REGIONAL = "regional"  # within ~30 km
    DISTANT = "distant"  # beyond that — context only
    UNKNOWN = "unknown"  # no location reported


class ChangeType(str, Enum):
    PRECIPITATION_INTENSIFIED = "precipitation_intensified"
    PRECIPITATION_WEAKENED = "precipitation_weakened"
    TRAFFIC_WORSENED = "traffic_worsened"
    TRAFFIC_IMPROVED = "traffic_improved"
    APPROACH_STATE_CHANGED = "approach_state_changed"
    INCIDENT_APPEARED = "incident_appeared"
    INCIDENT_CLEARED = "incident_cleared"
    SOURCE_BECAME_AVAILABLE = "source_became_available"
    SOURCE_BECAME_UNAVAILABLE = "source_became_unavailable"
    SOURCE_HEALTH_CHANGED = "source_health_changed"
    CONFIDENCE_CHANGED = "confidence_changed"
    SITUATION_LEVEL_CHANGED = "situation_level_changed"


class SignalBasis(str, Enum):
    """What kind of thing a signal rests on.

    Two sources can report the same quantity and still deserve different
    treatment: a station measured it somewhere, a model computed it here.
    """

    OBSERVATION = "observation"
    MODEL = "model"
    SIMULATED = "simulated"
    UNKNOWN = "unknown"


class SignalKind(str, Enum):
    WEATHER = "weather"
    TRAFFIC = "traffic"
    ROAD_CONDITIONS = "road_conditions"


class IncidentCategory(str, Enum):
    WATERLOGGING = "waterlogging"
    FLOODING = "flooding"
    ROAD_CLOSURE = "road_closure"
    # A lane closed while the road stays open (TomTom iconCategory 7). Not a
    # road closure: traffic still passes, slower.
    LANE_CLOSURE = "lane_closure"
    ACCIDENT = "accident"
    CONSTRUCTION = "construction"
    CONGESTION = "congestion"
    EVENT = "event"
    HAZARD = "hazard"
    OTHER = "other"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()
