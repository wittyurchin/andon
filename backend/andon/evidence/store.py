"""SQLite persistence for the evidence layer.

One file, stdlib ``sqlite3``, no ORM. Evidence survives a process restart,
which is what makes "is this getting worse?" answerable from history rather
than from whatever happens to be in memory.

Only *normalized* evidence is stored. Raw provider payloads are not kept;
``raw_reference`` holds a short pointer (e.g. a METAR string or frame path).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from ..domain.situation import SituationSnapshot
from ..domain.signals import GeoPoint
from .models import (
    AccessGraph,
    ChangeEvent,
    IncidentEvidence,
    Observation,
    RestaurantRecord,
    SourceHealthRecord,
)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS restaurants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    refresh_interval_seconds INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_refreshed_at TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    category TEXT NOT NULL,
    subject_id TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('observation', 'forecast')),
    observed_at TEXT NOT NULL,
    observed_at_basis TEXT NOT NULL,
    valid_at TEXT,
    received_at TEXT NOT NULL,
    lat REAL,
    lon REAL,
    spatial_json TEXT NOT NULL,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    stale_after_seconds INTEGER,
    source_record_id TEXT,
    raw_reference TEXT
);
CREATE INDEX IF NOT EXISTS ix_obs_lookup
    ON observations (restaurant_id, kind, category, source_id, subject_id, observed_at);

CREATE TABLE IF NOT EXISTS incidents (
    restaurant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    status TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (restaurant_id, id)
);

CREATE TABLE IF NOT EXISTS source_health (
    restaurant_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (restaurant_id, source_id)
);

CREATE TABLE IF NOT EXISTS access_graphs (
    restaurant_id TEXT PRIMARY KEY,
    built_at TEXT NOT NULL,
    meta_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS access_graph_approaches (
    restaurant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (restaurant_id, id)
);
CREATE TABLE IF NOT EXISTS access_graph_segments (
    restaurant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    approach_id TEXT,
    road_name TEXT,
    road_class TEXT,
    length_m REAL NOT NULL,
    distance_m REAL NOT NULL,
    source_segment_reference TEXT,
    record_json TEXT NOT NULL,
    PRIMARY KEY (restaurant_id, id)
);
CREATE TABLE IF NOT EXISTS access_graph_nodes (
    restaurant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    source_reference TEXT,
    PRIMARY KEY (restaurant_id, id)
);

CREATE TABLE IF NOT EXISTS change_events (
    id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    change_type TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_changes ON change_events (restaurant_id, detected_at);

CREATE TABLE IF NOT EXISTS evidence_state (
    restaurant_id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    state_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS situation_snapshots (
    location_key TEXT NOT NULL,
    at TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (location_key, at)
);
"""


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class EvidenceStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if self._path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()

    @property
    def path(self) -> str:
        return self._path

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- restaurants ------------------------------------------------------

    def upsert_restaurant(self, record: RestaurantRecord) -> RestaurantRecord:
        with self._tx() as db:
            db.execute(
                """INSERT INTO restaurants (id, name, lat, lon, refresh_interval_seconds,
                       created_at, updated_at, last_refreshed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                       refresh_interval_seconds=excluded.refresh_interval_seconds,
                       updated_at=excluded.updated_at""",
                (
                    record.id, record.name, record.location.lat, record.location.lon,
                    record.refresh_interval_seconds, iso(record.created_at),
                    iso(record.updated_at), iso(record.last_refreshed_at),
                ),
            )
        return self.get_restaurant(record.id)  # type: ignore[return-value]

    def get_restaurant(self, restaurant_id: str) -> RestaurantRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
        return _restaurant(row) if row else None

    def list_restaurants(self) -> list[RestaurantRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM restaurants ORDER BY created_at").fetchall()
        return [_restaurant(r) for r in rows]

    def mark_refreshed(self, restaurant_id: str, at: datetime) -> None:
        with self._tx() as db:
            db.execute("UPDATE restaurants SET last_refreshed_at=? WHERE id=?", (iso(at), restaurant_id))

    # -- observations -----------------------------------------------------

    def insert_observations(self, observations: list[Observation]) -> int:
        """Idempotent: an observation already stored (same id) is left as is."""
        if not observations:
            return 0
        with self._tx() as db:
            before = db.total_changes
            db.executemany(
                """INSERT OR IGNORE INTO observations (id, restaurant_id, source_id, source_name,
                       source_type, category, subject_id, kind, observed_at, observed_at_basis,
                       valid_at, received_at, lat, lon, spatial_json, value_json, confidence,
                       stale_after_seconds, source_record_id, raw_reference)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        o.id, o.restaurant_id, o.source_id, o.source_name, o.source_type.value,
                        o.category, o.subject_id, o.kind, iso(o.observed_at), o.observed_at_basis,
                        iso(o.valid_at), iso(o.received_at),
                        o.location.lat if o.location else None,
                        o.location.lon if o.location else None,
                        o.spatial.model_dump_json(), json.dumps(o.value, default=str),
                        o.confidence, o.stale_after_seconds, o.source_record_id, o.raw_reference,
                    )
                    for o in observations
                ],
            )
            return db.total_changes - before

    def latest_observations(self, restaurant_id: str) -> list[Observation]:
        """Newest observation per (source, category, subject) — last known, never dropped."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT o.* FROM observations o
                   JOIN (SELECT source_id, category, IFNULL(subject_id, '') AS s, MAX(observed_at) AS m
                         FROM observations WHERE restaurant_id=? AND kind='observation'
                         GROUP BY source_id, category, IFNULL(subject_id, '')) latest
                     ON o.source_id=latest.source_id AND o.category=latest.category
                    AND IFNULL(o.subject_id, '')=latest.s AND o.observed_at=latest.m
                   WHERE o.restaurant_id=? AND o.kind='observation'
                   ORDER BY o.category, o.source_id, o.subject_id""",
                (restaurant_id, restaurant_id),
            ).fetchall()
        return [_observation(r) for r in rows]

    def latest_forecasts(self, restaurant_id: str, valid_from: datetime) -> list[Observation]:
        """The most recent issue of each source's forecast, from ``valid_from`` on."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT o.* FROM observations o
                   JOIN (SELECT source_id, MAX(observed_at) AS m FROM observations
                         WHERE restaurant_id=? AND kind='forecast' GROUP BY source_id) latest
                     ON o.source_id=latest.source_id AND o.observed_at=latest.m
                   WHERE o.restaurant_id=? AND o.kind='forecast'
                     AND (o.valid_at IS NULL OR o.valid_at >= ?)
                   ORDER BY o.source_id, o.category, o.valid_at""",
                (restaurant_id, restaurant_id, iso(valid_from)),
            ).fetchall()
        return [_observation(r) for r in rows]

    def observation_history(
        self,
        restaurant_id: str,
        *,
        since: datetime,
        category: str | None = None,
        source_id: str | None = None,
        subject_id: str | None = None,
        kind: str | None = "observation",
        limit: int = 2000,
    ) -> list[Observation]:
        clauses = ["restaurant_id=?", "observed_at>=?"]
        args: list[Any] = [restaurant_id, iso(since)]
        for column, value in (("category", category), ("source_id", source_id),
                              ("subject_id", subject_id), ("kind", kind)):
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM observations WHERE {' AND '.join(clauses)} "
                "ORDER BY observed_at LIMIT ?",
                (*args, limit),
            ).fetchall()
        return [_observation(r) for r in rows]

    def count_observations(self, restaurant_id: str, source_id: str | None = None) -> int:
        with self._lock:
            if source_id:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM observations WHERE restaurant_id=? AND source_id=?",
                    (restaurant_id, source_id),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM observations WHERE restaurant_id=?", (restaurant_id,)
                ).fetchone()
        return int(row[0])

    # -- incidents --------------------------------------------------------

    def get_incidents(self, restaurant_id: str, status: str | None = None) -> list[IncidentEvidence]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT record_json FROM incidents WHERE restaurant_id=? AND status=? ORDER BY first_seen",
                    (restaurant_id, status),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT record_json FROM incidents WHERE restaurant_id=? ORDER BY first_seen",
                    (restaurant_id,),
                ).fetchall()
        return [IncidentEvidence.model_validate_json(r[0]) for r in rows]

    def save_incidents(self, incidents: list[IncidentEvidence]) -> None:
        with self._tx() as db:
            db.executemany(
                """INSERT INTO incidents (restaurant_id, id, status, first_seen, last_seen, record_json)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(restaurant_id, id) DO UPDATE SET status=excluded.status,
                       last_seen=excluded.last_seen, record_json=excluded.record_json""",
                [
                    (i.restaurant_id, i.id, i.status, iso(i.first_seen), iso(i.last_seen),
                     i.model_dump_json())
                    for i in incidents
                ],
            )

    # -- source health ----------------------------------------------------

    def get_health(self, restaurant_id: str) -> dict[str, SourceHealthRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT record_json FROM source_health WHERE restaurant_id=?", (restaurant_id,)
            ).fetchall()
        records = [SourceHealthRecord.model_validate_json(r[0]) for r in rows]
        return {r.source_id: r for r in records}

    def save_health(self, records: list[SourceHealthRecord]) -> None:
        with self._tx() as db:
            db.executemany(
                """INSERT INTO source_health (restaurant_id, source_id, status, checked_at, record_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(restaurant_id, source_id) DO UPDATE SET status=excluded.status,
                       checked_at=excluded.checked_at, record_json=excluded.record_json""",
                [
                    (r.restaurant_id, r.source_id, r.status.value, iso(r.checked_at), r.model_dump_json())
                    for r in records
                ],
            )

    # -- access graph -----------------------------------------------------

    def save_access_graph(self, graph: AccessGraph) -> None:
        rid = graph.restaurant_id
        meta = graph.model_dump(mode="json", exclude={"approaches", "segments", "nodes"})
        with self._tx() as db:
            for table in ("access_graph_approaches", "access_graph_segments", "access_graph_nodes"):
                db.execute(f"DELETE FROM {table} WHERE restaurant_id=?", (rid,))
            db.execute(
                """INSERT INTO access_graphs (restaurant_id, built_at, meta_json) VALUES (?, ?, ?)
                   ON CONFLICT(restaurant_id) DO UPDATE SET built_at=excluded.built_at,
                       meta_json=excluded.meta_json""",
                (rid, iso(graph.built_at), json.dumps(meta)),
            )
            db.executemany(
                "INSERT INTO access_graph_approaches (restaurant_id, id, record_json) VALUES (?, ?, ?)",
                [(rid, a.id, a.model_dump_json()) for a in graph.approaches],
            )
            db.executemany(
                """INSERT INTO access_graph_segments (restaurant_id, id, approach_id, road_name,
                       road_class, length_m, distance_m, source_segment_reference, record_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (rid, s.id, s.approach_id, s.road_name, s.road_class, s.length_m,
                     s.distance_m, s.source_segment_reference, s.model_dump_json())
                    for s in graph.segments
                ],
            )
            db.executemany(
                """INSERT INTO access_graph_nodes (restaurant_id, id, kind, lat, lon, source_reference)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(rid, n.id, n.kind, n.location.lat, n.location.lon, n.source_reference) for n in graph.nodes],
            )

    def load_access_graph(self, restaurant_id: str) -> AccessGraph | None:
        from .models import AccessNode, AccessSegment, Approach

        with self._lock:
            meta = self._conn.execute(
                "SELECT meta_json FROM access_graphs WHERE restaurant_id=?", (restaurant_id,)
            ).fetchone()
            if not meta:
                return None
            approaches = self._conn.execute(
                "SELECT record_json FROM access_graph_approaches WHERE restaurant_id=? ORDER BY rowid",
                (restaurant_id,),
            ).fetchall()
            segments = self._conn.execute(
                "SELECT record_json FROM access_graph_segments WHERE restaurant_id=? ORDER BY rowid",
                (restaurant_id,),
            ).fetchall()
            nodes = self._conn.execute(
                "SELECT id, kind, lat, lon, source_reference FROM access_graph_nodes WHERE restaurant_id=?",
                (restaurant_id,),
            ).fetchall()
        data = json.loads(meta[0])
        data["approaches"] = [Approach.model_validate_json(r[0]) for r in approaches]
        data["segments"] = [AccessSegment.model_validate_json(r[0]) for r in segments]
        data["nodes"] = [
            AccessNode(id=n["id"], restaurant_id=restaurant_id, kind=n["kind"],
                       location=GeoPoint(lat=n["lat"], lon=n["lon"]), source_reference=n["source_reference"])
            for n in nodes
        ]
        return AccessGraph.model_validate(data)

    # -- change events ----------------------------------------------------

    def insert_changes(self, events: list[ChangeEvent]) -> None:
        if not events:
            return
        with self._tx() as db:
            db.executemany(
                """INSERT OR IGNORE INTO change_events (id, restaurant_id, detected_at, change_type, record_json)
                   VALUES (?, ?, ?, ?, ?)""",
                [(e.id, e.restaurant_id, iso(e.detected_at), e.change_type.value, e.model_dump_json())
                 for e in events],
            )

    def list_changes(
        self, restaurant_id: str, since: datetime | None = None, limit: int = 100
    ) -> list[ChangeEvent]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT record_json FROM change_events WHERE restaurant_id=? AND detected_at>=?
                   ORDER BY detected_at DESC LIMIT ?""",
                (restaurant_id, iso(since) if since else "", limit),
            ).fetchall()
        return [ChangeEvent.model_validate_json(r[0]) for r in rows]

    # -- change-detection state ---------------------------------------------

    def get_state(self, restaurant_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT state_json FROM evidence_state WHERE restaurant_id=?", (restaurant_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_state(self, restaurant_id: str, state: dict[str, Any], at: datetime) -> None:
        with self._tx() as db:
            db.execute(
                """INSERT INTO evidence_state (restaurant_id, updated_at, state_json) VALUES (?, ?, ?)
                   ON CONFLICT(restaurant_id) DO UPDATE SET updated_at=excluded.updated_at,
                       state_json=excluded.state_json""",
                (restaurant_id, iso(at), json.dumps(state, sort_keys=True, default=str)),
            )

    # -- situation snapshots (history strip) ----------------------------------

    def snapshots(self, location_key: str, since: datetime | None = None) -> list[SituationSnapshot]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT record_json FROM situation_snapshots WHERE location_key=? AND at>=? ORDER BY at",
                (location_key, iso(since) if since else ""),
            ).fetchall()
        return [SituationSnapshot.model_validate_json(r[0]) for r in rows]

    def replace_last_snapshot(self, location_key: str, at: datetime) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM situation_snapshots WHERE location_key=? AND at=?", (location_key, iso(at)))

    def add_snapshot(self, location_key: str, snapshot: SituationSnapshot, keep: int) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO situation_snapshots (location_key, at, record_json) VALUES (?, ?, ?)",
                (location_key, iso(snapshot.at), snapshot.model_dump_json()),
            )
            db.execute(
                """DELETE FROM situation_snapshots WHERE location_key=? AND at NOT IN
                   (SELECT at FROM situation_snapshots WHERE location_key=? ORDER BY at DESC LIMIT ?)""",
                (location_key, location_key, keep),
            )

    # -- retention ------------------------------------------------------------

    def prune(self, retention_days: int, now: datetime | None = None) -> int:
        cutoff = iso((now or datetime.now(timezone.utc)) - timedelta(days=retention_days))
        with self._tx() as db:
            before = db.total_changes
            db.execute("DELETE FROM observations WHERE received_at < ?", (cutoff,))
            db.execute("DELETE FROM change_events WHERE detected_at < ?", (cutoff,))
            db.execute("DELETE FROM incidents WHERE status='cleared' AND last_seen < ?", (cutoff,))
            db.execute("DELETE FROM situation_snapshots WHERE at < ?", (cutoff,))
            return db.total_changes - before


def _restaurant(row: sqlite3.Row) -> RestaurantRecord:
    return RestaurantRecord(
        id=row["id"],
        name=row["name"],
        location=GeoPoint(lat=row["lat"], lon=row["lon"]),
        refresh_interval_seconds=row["refresh_interval_seconds"],
        created_at=parse(row["created_at"]),
        updated_at=parse(row["updated_at"]),
        last_refreshed_at=parse(row["last_refreshed_at"]),
    )


def _observation(row: sqlite3.Row) -> Observation:
    from .models import SpatialContext

    return Observation(
        id=row["id"],
        restaurant_id=row["restaurant_id"],
        source_id=row["source_id"],
        source_name=row["source_name"],
        source_type=row["source_type"],
        category=row["category"],
        subject_id=row["subject_id"],
        kind=row["kind"],
        observed_at=parse(row["observed_at"]),
        observed_at_basis=row["observed_at_basis"],
        valid_at=parse(row["valid_at"]),
        received_at=parse(row["received_at"]),
        location=GeoPoint(lat=row["lat"], lon=row["lon"]) if row["lat"] is not None else None,
        spatial=SpatialContext.model_validate_json(row["spatial_json"]),
        value=json.loads(row["value_json"]),
        confidence=row["confidence"],
        stale_after_seconds=row["stale_after_seconds"],
        source_record_id=row["source_record_id"],
        raw_reference=row["raw_reference"],
    )
