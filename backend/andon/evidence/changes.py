"""Deterministic change detection over successive evidence states.

Each refresh reduces the evidence bundle to a small *state digest* — bands,
indices, incident ids, source statuses — and compares it with the previous
digest. Differences become ``ChangeEvent`` records. The same state twice
produces nothing; that is the property that lets a downstream reasoning step
ask "what changed?" without diffing raw JSON or calling an LLM on a timer.

The first refresh for a restaurant establishes the baseline and emits nothing:
there is no "previous" to change from.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from ..domain.enums import ChangeType, HealthStatus, Severity
from .models import ChangeEvent, EvidenceBundle

RANK = {s.value: s.rank for s in Severity}


def confidence_band(value: float | None) -> str | None:
    if value is None:
        return None
    return "high" if value >= 0.7 else "medium" if value >= 0.4 else "low"


def digest(bundle: EvidenceBundle) -> dict[str, Any]:
    """Only the facts that would change what an operator is told."""
    precip: dict[str, str] = {}
    confidence: dict[str, str | None] = {}
    traffic: dict[str, dict[str, Any]] = {}
    approach_worst: dict[str, str] = {}

    for obs in bundle.observations:
        if obs.stale:
            continue  # stale evidence cannot move the current state
        confidence.setdefault(obs.source_id, confidence_band(obs.confidence))
        if obs.category == "weather.precipitation" and obs.value.get("band"):
            precip[f"{obs.source_id}|{obs.subject_id}"] = obs.value["band"]
        elif obs.category == "radar.reflectivity" and obs.subject_id == "at_site" and obs.value.get("band"):
            precip[f"{obs.source_id}|at_site"] = obs.value["band"]
        elif obs.category == "traffic.flow":
            key = f"{obs.source_id}|{obs.subject_id}"
            traffic[key] = {
                "band": obs.value.get("band"),
                "index": obs.value.get("congestion_index"),
                "label": obs.value.get("approach_label"),
            }
            band = obs.value.get("band")
            if band and obs.subject_id:
                current = approach_worst.get(obs.subject_id)
                if current is None or RANK[band] > RANK[current]:
                    approach_worst[obs.subject_id] = band
        elif obs.category == "traffic.corridor_eta":
            traffic[f"{obs.source_id}|{obs.subject_id}"] = {
                "travel_time_s": obs.value.get("travel_time_s"),
                "label": obs.value.get("approach_label"),
            }

    # The model's value for "now" is part of the picture too.
    for fc in bundle.forecasts:
        if fc.category == "weather.precipitation" and fc.valid_at == fc.observed_at and fc.value.get("band"):
            precip[f"{fc.source_id}|{fc.subject_id}"] = fc.value["band"]

    return {
        "precipitation": precip,
        "traffic": traffic,
        "approaches": approach_worst,
        "incidents": {
            i.id: {"type": i.incident_type.value, "source": i.source_id, "description": i.description,
                   "approaches": i.spatial.approach_ids}
            for i in bundle.incidents if i.status == "active"
        },
        "health": {h.source_id: h.status.value for h in bundle.source_health},
        "confidence": confidence,
    }


def detect(
    restaurant_id: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    now: datetime,
    *,
    traffic_index_delta: float = 0.15,
    travel_time_pct: float = 25.0,
    approach_labels: dict[str, str] | None = None,
) -> list[ChangeEvent]:
    if previous is None:
        return []
    labels = approach_labels or {}
    events: list[ChangeEvent] = []

    def add(change_type: ChangeType, category: str, summary: str, *, subject=None, source=None,
            before=None, after=None, magnitude=None) -> None:
        key = f"{restaurant_id}|{change_type.value}|{category}|{subject}|{source}|{now.isoformat()}"
        events.append(ChangeEvent(
            id=hashlib.sha1(key.encode()).hexdigest()[:20],
            restaurant_id=restaurant_id,
            detected_at=now,
            change_type=change_type,
            category=category,
            subject_id=subject,
            source_id=source,
            summary=summary,
            previous=before,
            current=after,
            magnitude=magnitude,
        ))

    # Precipitation bands, per source.
    for key, band in current["precipitation"].items():
        old = previous.get("precipitation", {}).get(key)
        if old is None or old == band:
            continue
        source, subject = key.split("|", 1)
        up = RANK[band] > RANK[old]
        add(ChangeType.PRECIPITATION_INTENSIFIED if up else ChangeType.PRECIPITATION_WEAKENED,
            "weather.precipitation",
            f"{source}: precipitation {'intensified' if up else 'weakened'} from {old} to {band}",
            subject=subject, source=source, before={"band": old}, after={"band": band},
            magnitude=RANK[band] - RANK[old])

    # Traffic, per source and approach.
    for key, now_state in current["traffic"].items():
        old = previous.get("traffic", {}).get(key)
        if not old:
            continue
        source, subject = key.split("|", 1)
        label = now_state.get("label") or labels.get(subject, subject)
        worse = better = False
        magnitude = None
        if now_state.get("band") and old.get("band") and now_state["band"] != old["band"]:
            worse = RANK[now_state["band"]] > RANK[old["band"]]
            better = not worse
            magnitude = RANK[now_state["band"]] - RANK[old["band"]]
        elif now_state.get("index") is not None and old.get("index") is not None:
            delta = now_state["index"] - old["index"]
            if abs(delta) >= traffic_index_delta:
                worse, better, magnitude = delta > 0, delta < 0, round(delta, 3)
        elif now_state.get("travel_time_s") and old.get("travel_time_s"):
            pct = (now_state["travel_time_s"] / old["travel_time_s"] - 1) * 100
            if abs(pct) >= travel_time_pct:
                worse, better, magnitude = pct > 0, pct < 0, round(pct, 1)
        if worse or better:
            add(ChangeType.TRAFFIC_WORSENED if worse else ChangeType.TRAFFIC_IMPROVED, "traffic",
                f"{label}: traffic {'worsened' if worse else 'improved'} ({source})",
                subject=subject, source=source, before=old, after=now_state, magnitude=magnitude)

    # Approach state: the worst band across sources.
    for approach, band in current["approaches"].items():
        old = previous.get("approaches", {}).get(approach)
        if old and old != band:
            add(ChangeType.APPROACH_STATE_CHANGED, "access",
                f"{labels.get(approach, approach)}: now {band} (was {old})",
                subject=approach, before={"band": old}, after={"band": band},
                magnitude=RANK[band] - RANK[old])

    # Incidents appearing and clearing.
    old_incidents = previous.get("incidents", {})
    for incident_id, info in current["incidents"].items():
        if incident_id not in old_incidents:
            on = f" on {', '.join(labels.get(a, a) for a in info['approaches'])}" if info["approaches"] else ""
            add(ChangeType.INCIDENT_APPEARED, "incident",
                f"New {info['type'].replace('_', ' ')}{on}: {info['description']}",
                subject=incident_id, source=info["source"], after=info)
    for incident_id, info in old_incidents.items():
        if incident_id not in current["incidents"]:
            add(ChangeType.INCIDENT_CLEARED, "incident",
                f"No longer reported: {info['type'].replace('_', ' ')} — {info['description']}",
                subject=incident_id, source=info["source"], before=info)

    # Source availability.
    for source, status in current["health"].items():
        old = previous.get("health", {}).get(source)
        if old is None or old == status:
            continue
        was_usable = HealthStatus(old).usable
        is_usable = HealthStatus(status).usable
        if was_usable and not is_usable:
            change = ChangeType.SOURCE_BECAME_UNAVAILABLE
        elif is_usable and not was_usable:
            change = ChangeType.SOURCE_BECAME_AVAILABLE
        else:
            change = ChangeType.SOURCE_HEALTH_CHANGED
        add(change, "source", f"{source}: {old} → {status}", source=source,
            before={"status": old}, after={"status": status})

    # Confidence bands, per source.
    for source, band in current["confidence"].items():
        old = previous.get("confidence", {}).get(source)
        if old and band and old != band:
            add(ChangeType.CONFIDENCE_CHANGED, "confidence", f"{source}: confidence {old} → {band}",
                source=source, before={"band": old}, after={"band": band})

    return events
