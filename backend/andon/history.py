"""Per-location history of situation snapshots, persisted in SQLite.

Deliberately small: one tiny record per refresh, bounded per location. No
analytics — just enough to answer "is this getting worse?" and to feed the
history strip. It now survives restarts, like the rest of the evidence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .domain.enums import Trend
from .domain.situation import NormalizedSituation, SituationSnapshot
from .evidence.store import EvidenceStore

# Two consecutive snapshots closer together than this are collapsed, so a user
# hammering "Refresh Now" does not flood the timeline. Set just under the
# shortest auto-refresh interval (1 min) so normal polling still builds a line.
MIN_SNAPSHOT_GAP_S = 55


class HistoryStore:
    def __init__(self, store: EvidenceStore, max_snapshots: int = 288) -> None:
        self._store = store
        self._max = max_snapshots

    def get(self, key: str, within_minutes: int | None = None) -> list[SituationSnapshot]:
        since = None
        if within_minutes is not None:
            since = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
        return self._store.snapshots(key, since)

    def record(self, key: str, situation: NormalizedSituation) -> SituationSnapshot:
        snapshot = snapshot_from(situation)
        existing = self._store.snapshots(key)
        if existing:
            last = existing[-1]
            gap = (_utc(snapshot.at) - _utc(last.at)).total_seconds()
            if gap < MIN_SNAPSHOT_GAP_S:
                # Keep the newest reading for the same time bucket.
                self._store.replace_last_snapshot(key, last.at)
        self._store.add_snapshot(key, snapshot, keep=self._max)
        return snapshot

    @staticmethod
    def summarize(snapshots: list[SituationSnapshot]) -> str | None:
        """One sentence such as "Situation worsening over the last hour"."""
        if len(snapshots) < 2:
            return None
        first, last = snapshots[0], snapshots[-1]
        minutes = max(1, int((_utc(last.at) - _utc(first.at)).total_seconds() // 60))
        delta = last.level.rank - first.level.rank
        if delta > 0:
            return f"Situation worsening over the last {minutes} min ({first.level.value} → {last.level.value})"
        if delta < 0:
            return f"Situation improving over the last {minutes} min ({first.level.value} → {last.level.value})"
        return f"Situation steady at {last.level.value} over the last {minutes} min"

    @staticmethod
    def direction(snapshots: list[SituationSnapshot]) -> Trend:
        if len(snapshots) < 2:
            return Trend.UNKNOWN
        delta = snapshots[-1].level.rank - snapshots[0].level.rank
        if delta > 0:
            return Trend.WORSENING
        if delta < 0:
            return Trend.IMPROVING
        return Trend.STEADY


def snapshot_from(situation: NormalizedSituation) -> SituationSnapshot:
    traffic_index = next(
        (
            float(f.value) / 100
            for f in situation.traffic.facets
            if f.key == "congestion" and isinstance(f.value, (int, float))
        ),
        None,
    )
    precipitation = next(
        (
            float(f.value)
            for f in situation.weather.facets
            if f.key == "rain" and isinstance(f.value, (int, float))
        ),
        None,
    )
    return SituationSnapshot(
        at=situation.generated_at,
        level=situation.overall.level,
        score=situation.overall.score,
        headline=situation.overall.headline,
        weather=situation.weather.severity,
        traffic=situation.traffic.severity,
        road_conditions=situation.road_conditions.severity,
        traffic_index=traffic_index,
        precipitation_mm_h=precipitation,
    )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
