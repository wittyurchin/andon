"""The normalized internal representation of "what is happening right now".

This is the system's centre of gravity: providers feed into it, the rules engine
builds it deterministically, the LLM reasons over it, and the UI renders it.
Adding a new signal means adding a ``SignalAssessment`` — nothing else changes.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .enums import (
    Confidence,
    EvidenceKind,
    Freshness,
    Severity,
    SignalBasis,
    SignalKind,
    SourceStatus,
    Trend,
)
from .signals import GeoPoint, SourceRef


class Evidence(BaseModel):
    """One defensible statement, with the provenance needed to trust it.

    ``kind`` is load-bearing: the UI labels it and the LLM is instructed never
    to restate a FORECAST or INFERRED item as an observation.
    """

    text: str
    kind: EvidenceKind
    source_id: str
    source_name: str
    observed_at: datetime | None = None
    age_seconds: int | None = None
    freshness: Freshness = Freshness.UNKNOWN
    confidence: Confidence = Confidence.MEDIUM
    distance_km: float | None = None
    # Ranking hint for the "Why?" panel; higher means stronger.
    weight: int = 50


class Facet(BaseModel):
    """A sub-classification inside a signal, e.g. rain / wind / visibility."""

    key: str
    label: str
    severity: Severity
    value: float | str | None = None
    unit: str | None = None
    kind: EvidenceKind = EvidenceKind.OBSERVED
    note: str | None = None
    available: bool = True


class SignalAssessment(BaseModel):
    kind: SignalKind
    status: SourceStatus
    basis: SignalBasis = SignalBasis.UNKNOWN
    # Short label for the tile when several sources cover the same signal.
    source_label: str | None = None
    # Set on a consolidated assessment when its sources do not agree.
    disagreement: str | None = None
    severity: Severity = Severity.NONE
    headline: str
    detail: str | None = None
    trend: Trend = Trend.UNKNOWN
    trend_note: str | None = None
    confidence: Confidence = Confidence.MEDIUM
    observed_at: datetime | None = None
    age_seconds: int | None = None
    freshness: Freshness = Freshness.UNKNOWN
    facets: list[Facet] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    # Populated when the source failed or covers only part of the signal.
    unavailable_reason: str | None = None
    # Things no configured source can answer, e.g. "severe weather alerts".
    coverage_gaps: list[str] = Field(default_factory=list)


class OverallAssessment(BaseModel):
    level: Severity
    label: str  # "HIGH DISRUPTION"
    score: int = Field(ge=0, le=100)
    headline: str  # "Heavy rain + traffic congestion + nearby waterlogging"
    drivers: list[str] = Field(default_factory=list)
    trend: Trend = Trend.UNKNOWN
    trend_note: str | None = None
    confidence: Confidence = Confidence.MEDIUM


class Restaurant(BaseModel):
    name: str
    location: GeoPoint


class NormalizedSituation(BaseModel):
    """Deterministic output of the rules engine — no LLM involved."""

    restaurant: Restaurant
    generated_at: datetime
    overall: OverallAssessment
    # Consolidated view used for scoring and for the rest of the pipeline.
    weather: SignalAssessment
    # One entry per configured weather provider — the UI renders a tile each.
    # Empty when only one source is configured and it *is* the consolidation.
    weather_sources: list[SignalAssessment] = Field(default_factory=list)
    traffic: SignalAssessment
    # One entry per configured traffic provider, same rule as weather.
    traffic_sources: list[SignalAssessment] = Field(default_factory=list)
    road_conditions: SignalAssessment
    missing_signals: list[str] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM
    # Stable hash of the material facts; drives LLM cache invalidation.
    fingerprint: str = ""

    @property
    def signals(self) -> list[SignalAssessment]:
        return [self.weather, self.traffic, self.road_conditions]


class TrendItem(BaseModel):
    """A row in the "What's changing?" panel."""

    label: str
    trend: Trend
    detail: str | None = None
    kind: EvidenceKind = EvidenceKind.OBSERVED


# --------------------------------------------------------------------------
# LLM report
# --------------------------------------------------------------------------


class SituationReport(BaseModel):
    """Narrative layer. Everything here is reasoning over the evidence above."""

    situation_title: str
    summary: str
    contributing_factors: list[str] = Field(default_factory=list)
    improving: list[str] = Field(default_factory=list)
    worsening: list[str] = Field(default_factory=list)
    operational_impact: list[str] = Field(default_factory=list)
    outlook_30_60min: str = ""
    confidence: Confidence = Confidence.MEDIUM
    confidence_rationale: str = ""
    uncertainties: list[str] = Field(default_factory=list)
    key_evidence: list[str] = Field(default_factory=list)

    # Meta — set by the service, not the model.
    generator: str = "llm"  # llm | rules-fallback
    model: str | None = None
    generated_at: datetime | None = None
    reused: bool = False
    reuse_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class SituationSnapshot(BaseModel):
    """One point on the history strip. Deliberately tiny."""

    at: datetime
    level: Severity
    score: int
    headline: str
    weather: Severity
    traffic: Severity
    road_conditions: Severity
    traffic_index: float | None = None
    precipitation_mm_h: float | None = None


class SituationResult(BaseModel):
    """Everything the dashboard needs for one refresh."""

    situation: NormalizedSituation
    report: SituationReport
    trends: list[TrendItem] = Field(default_factory=list)
    history: list[SituationSnapshot] = Field(default_factory=list)
    history_note: str | None = None
    sources: list["SourceHealth"] = Field(default_factory=list)


class SourceHealth(BaseModel):
    source: SourceRef
    status: SourceStatus
    observed_at: datetime | None = None
    fetched_at: datetime | None = None
    age_seconds: int | None = None
    freshness: Freshness = Freshness.UNKNOWN
    cached: bool = False
    latency_ms: int | None = None
    error: str | None = None


SituationResult.model_rebuild()
