"""Mock radar: a deterministic reflectivity field around the restaurant.

Shares the scenario clock with the other mocks, so its echo agrees with mock
rain. In the ``demo`` scenario a heavier cell sits ~3 km south-west of the
kitchen — the "dry here, raining nearby" pattern radar exists to catch.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from ...domain.signals import FetchContext, GeoPoint, RadarSample, RadarSnapshot, SourceRef
from ...geo import bearing_label, offset
from ..base import RadarProvider
from ..scenario import ScenarioClock

GRID_STEP_M = 1000.0
GRID_HALF = 4  # 9 x 9 samples, ±4 km


class MockRadarProvider(RadarProvider):
    CELL_BEARING_DEG = 225.0
    CELL_DISTANCE_M = 3000.0

    def __init__(self, clock: ScenarioClock) -> None:
        self._clock = clock

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="mock-radar",
            name=f"Mock radar ({self._clock.scenario})",
            kind="radar",
            mode="mock",
            attribution="Simulated data — not a real observation",
        )

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> RadarSnapshot:
        now = datetime.now(timezone.utc)
        frame_time = now.replace(minute=(now.minute // 10) * 10, second=0, microsecond=0)
        intensity = self._clock.intensity(0)
        cell = offset(point, self.CELL_BEARING_DEG, self.CELL_DISTANCE_M)

        samples: list[RadarSample] = []
        for gy in range(-GRID_HALF, GRID_HALF + 1):
            for gx in range(-GRID_HALF, GRID_HALF + 1):
                east, north = gx * GRID_STEP_M, gy * GRID_STEP_M
                distance = math.hypot(east, north)
                bearing_deg = (math.degrees(math.atan2(east, north)) + 360) % 360
                location = offset(point, bearing_deg, distance) if distance else point

                # Background echo tracks the scenario; the cell adds up to +11 dBZ.
                base = -32.0 if intensity < 0.12 else 18.0 + 36.0 * intensity
                cell_km = _km(location, cell)
                bump = 11.0 * math.exp(-((cell_km / 1.5) ** 2)) if base > 0 else 0.0
                dbz = round(base + bump)
                samples.append(
                    RadarSample(
                        location=location,
                        distance_m=round(distance, 1),
                        bearing=bearing_label(point, location) if distance else None,
                        reflectivity_dbz=dbz,
                        reflectivity_dbz_max=dbz,
                        covered=True,
                    )
                )

        return RadarSnapshot(
            observed_at=frame_time,
            resolution_m=GRID_STEP_M,
            samples=samples,
            frame_reference=f"mock-radar:{frame_time:%Y%m%dT%H%M}",
        )


def _km(a: GeoPoint, b: GeoPoint) -> float:
    dy = (a.lat - b.lat) * 111.0
    dx = (a.lon - b.lon) * 111.0 * math.cos(math.radians((a.lat + b.lat) / 2))
    return math.hypot(dx, dy)
