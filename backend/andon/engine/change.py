"""Material-change detection — the system's main LLM cost control.

The fingerprint deliberately covers only facts that would change what a report
*says*: severity bands, trend directions, which incident categories are active,
and which sources are missing. It ignores the numbers underneath, so rain
drifting from 8.1 to 8.4 mm/h does not buy another LLM call.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from ..domain.situation import NormalizedSituation, SituationReport


def fingerprint(situation: NormalizedSituation) -> str:
    payload = {
        "overall": situation.overall.level.value,
        "signals": [
            {
                "kind": signal.kind.value,
                "status": signal.status.value,
                "severity": signal.severity.value,
                "trend": signal.trend.value,
                "facets": sorted(
                    f"{facet.key}:{facet.severity.value}"
                    for facet in signal.facets
                    if facet.available and facet.severity.value != "none"
                ),
            }
            for signal in situation.signals
        ],
        "missing": sorted(situation.missing_signals),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def should_regenerate(
    *,
    current_fingerprint: str,
    cached_fingerprint: str | None,
    cached_report: SituationReport | None,
    max_age_s: int,
    now: datetime | None = None,
    force: bool = False,
) -> tuple[bool, str]:
    """Decide whether to spend an LLM call. Returns (regenerate, reason)."""
    now = now or datetime.now(timezone.utc)

    if cached_report is None or cached_fingerprint is None:
        return True, "no cached report for this location"
    if force:
        return True, "manual refresh requested a fresh assessment"
    if cached_fingerprint != current_fingerprint:
        return True, "the normalized situation changed materially"

    generated_at = cached_report.generated_at
    if generated_at is None:
        return True, "cached report has no timestamp"
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)

    age = (now - generated_at).total_seconds()
    if age >= max_age_s:
        return True, f"cached report is {int(age // 60)} min old"

    return False, f"situation unchanged since {generated_at:%H:%M:%S}"
