"""Source health registry.

Every provider invocation — including the ones we deliberately did not make —
updates one record per (restaurant, source):

* **healthy / degraded / stale** — the source answered; stale means its data is
  older than the source's freshness policy, degraded means some probes failed.
* **unavailable / unauthorized** — we called and it failed. These count towards
  ``consecutive_failures``.
* **misconfigured / disabled** — we did not call, because a setting is missing
  or no authorised access path exists. Not counted as failures: a missing key
  is not an outage.

Health never alters evidence. A source going stale leaves its observations
stored and visible, marked stale — it does not turn them into "normal".
"""

from __future__ import annotations

from datetime import datetime

from ..domain.enums import HealthStatus
from ..domain.signals import ProviderResponse
from .extract import source_type
from .freshness import FreshnessPolicy
from .models import SourceHealthRecord

NOT_CALLED = (HealthStatus.MISCONFIGURED, HealthStatus.DISABLED)


def _policy_category(response: ProviderResponse) -> tuple[str, str]:
    kind = response.source.kind
    if kind == "weather":
        data = response.data
        is_station = bool(data is not None and (getattr(data, "station_name", None) or getattr(data, "station_id", None)))
        return ("weather.precipitation", "observation") if is_station else ("weather.precipitation", "forecast")
    if kind == "radar":
        return "radar.reflectivity", "observation"
    if kind == "traffic":
        return "traffic.flow", "observation"
    return "incident", "observation"


def update_health(
    restaurant_id: str,
    previous: dict[str, SourceHealthRecord],
    responses: list[ProviderResponse],
    policy: FreshnessPolicy,
    now: datetime,
) -> list[SourceHealthRecord]:
    records: list[SourceHealthRecord] = []
    for response in responses:
        prev = previous.get(response.source.id)
        base = dict(
            source_id=response.source.id,
            source_name=response.source.name,
            source_kind=response.source.kind,
            source_type=source_type(response),
            restaurant_id=restaurant_id,
            checked_at=now,
            latency_ms=response.latency_ms,
            licence_note=response.source.licence_note,
            details=dict(response.details),
        )

        if response.ok:
            age = None
            if response.observed_at is not None:
                age = int((now - response.observed_at).total_seconds())
            category, kind = _policy_category(response)
            stale_after = policy.stale_after(response.source.id, category, kind)
            if age is not None and age >= stale_after:
                status = HealthStatus.STALE
            elif response.health_hint is HealthStatus.DEGRADED:
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.HEALTHY
            base["details"].update({"stale_after_seconds": stale_after, "cached": response.cached})
            grade = policy.grade_for(response.source.id, category, kind, age)
            covered = getattr(response.data, "covered_categories", None)
            if covered is not None:
                # What this feed *can* report — so absence of a category is
                # read as "not covered", never as "confirmed clear".
                base["details"]["covered_categories"] = [c.value for c in covered]
            records.append(SourceHealthRecord(
                **base,
                status=status,
                # A cache hit means we did not contact the source this time.
                last_success_at=(prev.last_success_at if (response.cached and prev) else now),
                last_failure_at=prev.last_failure_at if prev else None,
                consecutive_failures=0,
                response_age_seconds=age,
                freshness_grade=grade,
            ))
            continue

        status = response.health_hint or HealthStatus.UNAVAILABLE
        called = status not in NOT_CALLED
        records.append(SourceHealthRecord(
            **base,
            status=status,
            last_success_at=prev.last_success_at if prev else None,
            last_failure_at=now if called else (prev.last_failure_at if prev else None),
            consecutive_failures=((prev.consecutive_failures if prev else 0) + 1) if called else 0,
            error=response.error,
        ))
    return records
