"""Deterministic report writer.

Used whenever the LLM is unavailable — no credentials, an API failure, or
``ANDON_LLM_ENABLED=false``. It is intentionally competent rather than a stub:
the default local run has no API key, and a dashboard that says "report
unavailable" is useless in exactly the moment it is needed.

It follows the same honesty rules as the prompt: observed, forecast and
inferred evidence stay distinguishable, and nothing is invented.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..domain.enums import (
    Confidence,
    EvidenceKind,
    Freshness,
    Severity,
    SourceStatus,
    Trend,
)
from ..domain.situation import (
    NormalizedSituation,
    SignalAssessment,
    SituationReport,
    SituationSnapshot,
)

TITLES: dict[Severity, str] = {
    Severity.NONE: "Normal operating conditions",
    Severity.LOW: "Minor external friction",
    Severity.MEDIUM: "Moderate external disruption",
    Severity.HIGH: "Significant external disruption",
    Severity.SEVERE: "Severe external disruption",
}

IMPACTS: dict[Severity, list[str]] = {
    Severity.NONE: ["No weather- or traffic-driven impact on pickups or deliveries."],
    Severity.LOW: ["Expect slightly longer rider trips; no action needed yet."],
    Severity.MEDIUM: [
        "Rider trips are likely to run longer than usual.",
        "Consider widening quoted delivery times for orders going out now.",
    ],
    Severity.HIGH: [
        "Rider movement around the restaurant is materially slower.",
        "Widen quoted delivery times and stagger kitchen dispatch to avoid orders sitting ready.",
        "Longer-distance orders are the most exposed.",
    ],
    Severity.SEVERE: [
        "Rider movement is heavily impeded around the restaurant.",
        "Hold or pace new long-distance orders and widen delivery estimates now.",
        "Expect riders to refuse or abandon affected routes.",
    ],
}


def write_report(
    situation: NormalizedSituation,
    history: list[SituationSnapshot],
    note: str | None = None,
) -> SituationReport:
    overall = situation.overall
    available = [s for s in situation.signals if s.status is not SourceStatus.UNAVAILABLE]

    summary = _summary(situation, history)
    improving, worsening = _movement(situation)

    uncertainties: list[str] = []
    for missing in situation.missing_signals:
        uncertainties.append(
            f"No working source for {missing.replace('_', ' ')} — it is unknown, not confirmed clear."
        )
    uncertainties.extend(situation.coverage_gaps)
    for signal in available:
        if signal.freshness in (Freshness.AGING, Freshness.STALE) and signal.age_seconds:
            uncertainties.append(
                f"{signal.kind.value.replace('_', ' ').title()} data is "
                f"{signal.age_seconds // 60} min old."
            )
    if any(source.mode == "mock" for signal in available for source in signal.sources):
        uncertainties.append(
            "One or more sources are simulated mock providers, not live feeds."
        )
    if note:
        uncertainties.append(note)

    return SituationReport(
        situation_title=TITLES[overall.level],
        summary=summary,
        contributing_factors=_factors(situation),
        improving=improving,
        worsening=worsening,
        operational_impact=IMPACTS[overall.level],
        outlook_30_60min=_outlook(situation),
        confidence=overall.confidence,
        confidence_rationale=_confidence_rationale(situation, available),
        uncertainties=uncertainties[:5],
        key_evidence=_key_evidence(situation),
        generator="rules-fallback",
        model=None,
        generated_at=datetime.now(timezone.utc),
    )


def _summary(situation: NormalizedSituation, history: list[SituationSnapshot]) -> str:
    overall = situation.overall
    sentences: list[str] = []

    if overall.drivers:
        sentences.append(
            f"{overall.label.capitalize().replace(' disruption', ' disruption')} around "
            f"{situation.restaurant.name}: {_join(overall.drivers)}."
        )
    else:
        sentences.append(
            f"No disruptive external conditions detected around {situation.restaurant.name}."
        )

    weather, traffic, road = situation.weather, situation.traffic, situation.road_conditions

    if weather.status is not SourceStatus.UNAVAILABLE and weather.severity is not Severity.NONE:
        direction = _trend_phrase(weather.trend)
        sentences.append(f"{weather.headline} is being reported ({weather.detail}){direction}.")

    if traffic.status is not SourceStatus.UNAVAILABLE and traffic.severity is not Severity.NONE:
        sentences.append(
            f"Traffic around the restaurant is {traffic.headline.lower()} — {traffic.detail}"
            f"{_trend_phrase(traffic.trend)}."
        )

    if road.status is not SourceStatus.UNAVAILABLE and road.severity is not Severity.NONE:
        inferred_only = all(f.kind is EvidenceKind.INFERRED for f in road.facets if f.available)
        prefix = "Inferred, not reported: " if inferred_only else "Also reported nearby: "
        sentences.append(f"{prefix}{road.detail}.")

    if situation.missing_signals:
        sentences.append(
            f"{_join([m.replace('_', ' ') for m in situation.missing_signals]).capitalize()} "
            "could not be retrieved and is excluded from this assessment."
        )

    summary_note = _history_note(history)
    if summary_note:
        sentences.append(summary_note)

    return " ".join(sentences)


def _factors(situation: NormalizedSituation) -> list[str]:
    factors: list[str] = []
    ranked = sorted(
        (s for s in situation.signals if s.status is not SourceStatus.UNAVAILABLE),
        key=lambda s: s.severity.rank,
        reverse=True,
    )
    for signal in ranked:
        if signal.severity is Severity.NONE:
            continue
        top = signal.evidence[0].text if signal.evidence else signal.detail
        marker = ""
        if signal.evidence and signal.evidence[0].kind is not EvidenceKind.OBSERVED:
            marker = f" ({signal.evidence[0].kind.value})"
        factors.append(f"{top}{marker}")
    return factors or ["No signal is currently above its normal threshold."]


def _movement(situation: NormalizedSituation) -> tuple[list[str], list[str]]:
    improving: list[str] = []
    worsening: list[str] = []
    labels = {
        "weather": "Weather",
        "traffic": "Traffic",
        "road_conditions": "Road conditions",
    }
    for signal in situation.signals:
        if signal.status is SourceStatus.UNAVAILABLE:
            continue
        label = labels[signal.kind.value]
        note = f" — {signal.trend_note}" if signal.trend_note else ""
        if signal.trend is Trend.WORSENING:
            worsening.append(f"{label}{note}")
        elif signal.trend is Trend.IMPROVING:
            improving.append(f"{label}{note}")
    return improving, worsening


def _outlook(situation: NormalizedSituation) -> str:
    weather = situation.weather
    parts: list[str] = []

    if weather.status is not SourceStatus.UNAVAILABLE and weather.trend_note:
        parts.append(f"Assessment: {weather.trend_note.lower()}.")

    if situation.traffic.trend is Trend.WORSENING:
        parts.append(
            "Traffic has been building over recent readings and would normally keep "
            "building while conditions hold."
        )
    elif situation.traffic.trend is Trend.IMPROVING:
        parts.append("Traffic has been easing over recent readings.")
    elif situation.traffic.trend is Trend.UNKNOWN:
        parts.append("No traffic trend is available yet — one appears after the next refresh.")

    if situation.road_conditions.severity.rank >= Severity.MEDIUM.rank:
        parts.append(
            "Reported road conditions nearby tend to persist for longer than the weather "
            "that caused them, so allow for them beyond the next rain band."
        )

    if situation.overall.level is Severity.NONE:
        parts.append("Nothing in the current signals suggests disruption in the next hour.")
    else:
        parts.append(
            f"Expect the situation to stay near {situation.overall.level.value} over the next "
            "30-60 minutes unless the trends above reverse."
        )

    return " ".join(parts)


def _confidence_rationale(
    situation: NormalizedSituation, available: list[SignalAssessment]
) -> str:
    if not available:
        return "No source returned data, so no assessment can be made."
    fresh = sum(1 for s in available if s.freshness in (Freshness.FRESH, Freshness.RECENT))
    base = f"{fresh} of {len(available)} available signals are recent"
    if situation.missing_signals:
        base += f"; {len(situation.missing_signals)} signal(s) have no working source"
    if situation.confidence is Confidence.HIGH:
        return f"{base}, and they agree with each other."
    if situation.confidence is Confidence.MEDIUM:
        return f"{base}, which supports a working assessment but not a firm one."
    return f"{base}, so this assessment is weakly supported."


def _key_evidence(situation: NormalizedSituation) -> list[str]:
    items = [e for signal in situation.signals for e in signal.evidence]
    items.sort(key=lambda e: e.weight, reverse=True)
    out: list[str] = []
    for item in items[:5]:
        age = f", {item.age_seconds // 60} min ago" if item.age_seconds is not None else ""
        out.append(f"[{item.kind.value}] {item.text} ({item.source_name}{age})")
    return out


def _history_note(history: list[SituationSnapshot]) -> str | None:
    if len(history) < 2:
        return None
    first, last = history[0], history[-1]
    minutes = max(1, int((_utc(last.at) - _utc(first.at)).total_seconds() // 60))
    delta = last.level.rank - first.level.rank
    if delta > 0:
        return f"The situation has worsened over the last {minutes} min."
    if delta < 0:
        return f"The situation has improved over the last {minutes} min."
    return None


def _trend_phrase(trend: Trend) -> str:
    return {
        Trend.WORSENING: " and intensifying",
        Trend.IMPROVING: " and easing",
        Trend.STEADY: " and holding steady",
    }.get(trend, "")


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
