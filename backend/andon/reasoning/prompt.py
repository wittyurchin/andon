"""Prompt construction for the reasoning step.

The model is handed *classified* signals, never raw numbers to bucket. Its job
is the part rules are bad at: reading the combination, saying what it means for
a kitchen and its riders, and being explicit about what is not known.

The system prompt is deliberately static so it forms a stable, cacheable prefix;
everything that changes per refresh lives in the user message.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import SourceStatus
from ..domain.situation import NormalizedSituation, SituationSnapshot

SYSTEM_PROMPT = """\
You are the reasoning layer of a situation-awareness system for a food-delivery \
restaurant. A deterministic rules engine has already fetched external signals, \
classified their severity, and computed trends. You do not classify or \
recompute anything.

Your job is to read the *combination* of signals and explain, to a kitchen \
manager or dispatcher, what is happening around this restaurant right now, how \
it is changing, and what it means operationally for the kitchen and for riders \
getting orders out.

Hard rules:

1. Use only the evidence in the input. Never introduce a condition, incident, \
   road, event or measurement that is not there. If something is not in the \
   input, it is not known — say so rather than filling the gap.
2. Every piece of evidence carries a `kind`:
   - `observed`  — measured or reported. You may state this as fact.
   - `forecast`  — a prediction. Always attribute it ("forecast to...", "expected to...").
   - `inferred`  — the engine's own deduction, not a report. Always mark it as \
     an inference ("no feed reports this, but...") and never as an observation.
   Never promote a forecast or an inference to an observed fact.
3. `missing_signals` lists signals with no working source. Name them in \
   `uncertainties` and let them lower your confidence. A missing signal is \
   unknown, not benign.
4. `coverage_gaps` lists things no configured source can report. Treat these as \
   blind spots, not as "nothing is happening".
5. Prefer fresh evidence. When something is more than ~30 minutes old, say so.
6. If a source has `mode: "mock"`, the data is simulated for local development. \
   Reason over it normally, but note it once in `uncertainties`.
7. A signal may be covered by several sources at once (`weather_sources`). Each \
   carries a `basis`: `observation` was measured somewhere real, `model` was \
   computed for these coordinates. When they conflict, a non-null \
   `disagreement` says so — report the conflict and lower your confidence \
   rather than picking a winner or averaging them. Note where an observation \
   was actually taken; a station reading from 30 km away is evidence about the \
   station, not about this street.

Style: calm, concrete, operational. No hedging filler, no weather-report \
padding, no exclamation marks. Short sentences. Name distances and times when \
the input has them. Write for someone deciding whether to hold orders, widen \
delivery estimates, or pull the delivery radius in — not for someone browsing a \
weather app.

Length: `summary` is 2-4 sentences. List items are single short sentences.
"""


class LlmReport(BaseModel):
    """Exactly what the model is asked to produce.

    Kept separate from the transport model in ``domain.situation`` so that
    server-set metadata can never be hallucinated into the response.
    """

    model_config = ConfigDict(extra="forbid")

    situation_title: str = Field(
        description="Four to eight words naming the situation, e.g. 'Severe external disruption'."
    )
    summary: str = Field(
        description="2-4 sentences describing what is happening around the restaurant right now."
    )
    contributing_factors: list[str] = Field(
        description="The factors driving the current situation, strongest first. 1-5 items."
    )
    improving: list[str] = Field(description="What is getting better. Empty list if nothing is.")
    worsening: list[str] = Field(description="What is getting worse. Empty list if nothing is.")
    operational_impact: list[str] = Field(
        description=(
            "Concrete effects on kitchen and rider operations, e.g. slower pickups, "
            "riders avoiding a flooded approach. 1-4 items."
        )
    )
    outlook_30_60min: str = Field(
        description=(
            "2-3 sentences on the next 30-60 minutes, written as an assessment rather "
            "than a certainty. Ground it in forecast and trend evidence."
        )
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description="Confidence in this assessment, given source freshness and coverage."
    )
    confidence_rationale: str = Field(description="One sentence explaining the confidence level.")
    uncertainties: list[str] = Field(
        description="Missing signals, coverage gaps, stale data, simulated sources. 0-4 items."
    )
    key_evidence: list[str] = Field(
        description=(
            "The 2-5 strongest pieces of evidence behind this assessment, each restated "
            "with its epistemic status (observed / forecast / inferred)."
        )
    )


def report_schema() -> dict:
    """JSON schema for ``output_config.format``."""
    schema = LlmReport.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(schema.get("properties", {}).keys())
    return schema


def build_user_message(
    situation: NormalizedSituation,
    history: list[SituationSnapshot],
) -> str:
    """Render the normalized situation as compact, readable JSON-ish text."""
    import json

    payload = {
        "restaurant": {
            "name": situation.restaurant.name,
            "lat": situation.restaurant.location.lat,
            "lon": situation.restaurant.location.lon,
        },
        "assessed_at": situation.generated_at.isoformat(),
        "overall": {
            "level": situation.overall.level.value,
            "score_0_100": situation.overall.score,
            "drivers": situation.overall.drivers,
            "trend": situation.overall.trend.value,
            "trend_note": situation.overall.trend_note,
            "confidence": situation.overall.confidence.value,
        },
        "signals": {signal.kind.value: _signal_payload(signal) for signal in situation.signals},
        # When several weather sources are configured, the model sees each one
        # plus whether they agree. Disagreement is evidence about uncertainty,
        # not something to resolve silently.
        "weather_sources": [
            {"label": s.source_label, "basis": s.basis.value, **_signal_payload(s)}
            for s in situation.weather_sources
        ],
        "missing_signals": situation.missing_signals,
        "coverage_gaps": situation.coverage_gaps,
        "history_last_hour": [
            {"at": s.at.strftime("%H:%M"), "level": s.level.value, "score": s.score}
            for s in history[-12:]
        ],
    }

    return (
        "Current situation input for this restaurant.\n\n"
        f"```json\n{json.dumps(payload, indent=2, default=str)}\n```\n\n"
        "Produce the situation report."
    )


def _signal_payload(signal) -> dict:
    if signal.status is SourceStatus.UNAVAILABLE:
        return {
            "status": "unavailable",
            "reason": signal.unavailable_reason,
            "note": "Excluded from the overall assessment. Treat as unknown, not as benign.",
            "sources": [s.name for s in signal.sources],
        }

    return {
        "status": signal.status.value,
        "basis": signal.basis.value,
        "disagreement": signal.disagreement,
        "severity": signal.severity.value,
        "headline": signal.headline,
        "detail": signal.detail,
        "trend": signal.trend.value,
        "trend_note": signal.trend_note,
        "confidence": signal.confidence.value,
        "data_age_minutes": (
            round(signal.age_seconds / 60, 1) if signal.age_seconds is not None else None
        ),
        "freshness": signal.freshness.value,
        "facets": [
            {
                "label": facet.label,
                "severity": facet.severity.value,
                "value": facet.value,
                "unit": facet.unit,
                "kind": facet.kind.value,
                "note": facet.note,
            }
            for facet in signal.facets
            if facet.available
        ],
        "evidence": [
            {
                "text": item.text,
                "kind": item.kind.value,
                "source": item.source_name,
                "source_mode": _mode_for(signal, item.source_id),
                "age_minutes": (
                    round(item.age_seconds / 60, 1) if item.age_seconds is not None else None
                ),
                "distance_km": item.distance_km,
                "confidence": item.confidence.value,
            }
            for item in signal.evidence
        ],
        "coverage_gaps": signal.coverage_gaps,
    }


def _mode_for(signal, source_id: str) -> str:
    for source in signal.sources:
        if source.id == source_id:
            return source.mode
    return "derived"
