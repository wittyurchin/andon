"""RainViewer radar adapter — implemented, disabled by licence by default.

Why radar at all: a station can be dry while rain sits a kilometre away, and a
model can report rain where there is none. Radar is the only one of the three
that observes precipitation *spatially* around the restaurant.

Verified against documentation/rainviewer/ (2026-09-27):

* index: ``https://api.rainviewer.com/public/weather-maps.json`` → ``host`` and
  ``radar.past[]`` frames of ``{time, path}``;
* tiles: ``{host}{path}/{size}/{z}/{lat}/{lon}/{color}/{options}.png`` with the
  image *centred* on (lat, lon); max zoom 7; ``options`` = ``{smooth}_{snow}``;
* colour scheme 2 ("Universal Blue") is the only one offered, and its exact
  RGBA ↔ dBZ table is published as CSV (bundled next to this file). We request
  ``0_0`` (no smoothing) so pixels match table entries exactly;
* coverage mask: ``/v2/coverage/0/{size}/{z}/{lat}/{lon}/0/0_0.png``, black
  where there is no radar coverage;
* terms: "available for personal and educational use only"; commercial use by
  agreement; attribution required; no SLA. Hence the explicit opt-in.

Honest limits: at zoom 7 one pixel is ~1.2 km, so "at the restaurant" means
"in the ~1.2 km cell containing it", and the resolution is recorded on every
sample. Some colours in the table stand for dBZ *ranges*; both bounds are kept.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from ...domain.enums import HealthStatus
from ...domain.signals import FetchContext, GeoPoint, RadarSample, RadarSnapshot, SourceRef
from ...geo import bearing_label, haversine_km, offset
from ..base import RadarProvider
from ..errors import ProviderError, ProviderSchemaError

INDEX_URL = "https://api.rainviewer.com/public/weather-maps.json"
TILE_SIZE = 256
ZOOM = 7  # documented maximum
COLOR_SCHEME = 2  # Universal Blue — the only scheme currently offered
OPTIONS = "0_0"  # no smoothing, no snow colours: exact table colours
COLOR_TABLE = Path(__file__).with_name("rainviewer_api_colors_table.csv")
TERMS = (
    "RainViewer's free API is for personal and educational use only "
    "(documentation/rainviewer/api.html); commercial use needs an agreement."
)


@lru_cache(maxsize=1)
def colour_table() -> dict[str, tuple[float, float]]:
    """RGBA hex → (min dBZ, max dBZ), from the published Universal Blue column.

    The CSV holds a rain block (-32…95) followed by a snow block; with
    ``snow=0`` only rain colours are rendered, so only that block is used.
    """
    with COLOR_TABLE.open(encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    column = rows[0].index("Universal Blue")
    ranges: dict[str, list[float]] = {}
    for row in rows[1:129]:
        dbz = float(row[0])
        ranges.setdefault(row[column].strip().lower(), []).append(dbz)
    return {colour: (min(v), max(v)) for colour, v in ranges.items()}


def metres_per_pixel(lat: float, zoom: int = ZOOM, tile_size: int = TILE_SIZE) -> float:
    """Web Mercator ground resolution at ``lat``."""
    return 156543.03392 * math.cos(math.radians(lat)) / (2**zoom) * (256 / tile_size)


class RainViewerRadarProvider(RadarProvider):
    def __init__(
        self,
        timeout_s: float = 8.0,
        terms_acknowledged: bool = False,
        sample_radius_km: float = 6.0,
    ) -> None:
        self._terms_acknowledged = terms_acknowledged
        self._radius_km = sample_radius_km
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": "andon-situation-awareness/0.1"},
        )

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="rainviewer",
            name="RainViewer radar composite",
            kind="radar",
            mode="live",
            attribution="Weather data by RainViewer (https://www.rainviewer.com/)",
            docs_url="https://www.rainviewer.com/api/weather-maps-api.html",
            licence_note=TERMS,
        )

    def precheck(self) -> tuple[HealthStatus, dict[str, Any]] | None:
        if not self._terms_acknowledged:
            return HealthStatus.DISABLED, {
                "reason": TERMS + " Set ANDON_RAINVIEWER_TERMS_ACKNOWLEDGED=true only for such use.",
                "requires": "licence verification",
            }
        return None

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> RadarSnapshot:
        index = (await self._client.get(INDEX_URL)).raise_for_status().json()
        host = index.get("host")
        frames = (index.get("radar") or {}).get("past") or []
        if not host or not frames:
            raise ProviderSchemaError("radar index has no host or past frames")
        frame = frames[-1]
        frame_time = datetime.fromtimestamp(int(frame["time"]), tz=timezone.utc)
        centre = f"{point.lat:.4f}/{point.lon:.4f}"

        tile = await self._image(f"{host}{frame['path']}/{TILE_SIZE}/{ZOOM}/{centre}/{COLOR_SCHEME}/{OPTIONS}.png")
        coverage = await self._image(f"{host}/v2/coverage/0/{TILE_SIZE}/{ZOOM}/{centre}/0/0_0.png")

        snapshot = decode_samples(tile, coverage, point, self._radius_km)
        centre_sample = min(snapshot.samples, key=lambda s: s.distance_m)
        if centre_sample.covered is False:
            raise ProviderError("no radar coverage at this location", coverage=False)
        return snapshot.model_copy(
            update={"observed_at": frame_time, "frame_reference": f"rainviewer:{frame['path']}"}
        )

    async def _image(self, url: str):
        from PIL import Image  # imported lazily: only needed when enabled

        response = await self._client.get(url)
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content)).convert("RGBA")

    async def aclose(self) -> None:
        await self._client.aclose()


def decode_samples(tile, coverage, point: GeoPoint, radius_km: float) -> RadarSnapshot:
    """Sample a centred radar tile around ``point``. Pure — testable offline."""
    width, height = tile.size
    scale = metres_per_pixel(point.lat, ZOOM, width)
    reach = max(1, math.ceil(radius_km * 1000 / scale))
    cx, cy = width / 2.0, height / 2.0
    table = colour_table()

    samples: list[RadarSample] = []
    undecodable = 0
    for py in range(int(cy) - reach, int(cy) + reach):
        for px in range(int(cx) - reach, int(cx) + reach):
            if not (0 <= px < width and 0 <= py < height):
                continue
            east_m = (px + 0.5 - cx) * scale
            north_m = (cy - (py + 0.5)) * scale
            distance = math.hypot(east_m, north_m)
            if distance > radius_km * 1000:
                continue
            bearing_deg = (math.degrees(math.atan2(east_m, north_m)) + 360) % 360
            location = offset(point, bearing_deg, distance)

            covered = None
            if coverage is not None:
                # Coverage mask: black = no radar coverage, transparent = covered.
                r, g, b, a = coverage.getpixel((px, py))
                covered = not (a > 0 and r == g == b == 0)

            rgba = "#%02x%02x%02x%02x" % tile.getpixel((px, py))
            decoded = table.get(rgba)
            if decoded is None:
                undecodable += 1
            samples.append(
                RadarSample(
                    location=location,
                    distance_m=round(distance, 1),
                    bearing=bearing_label(point, location) if distance > 1 else None,
                    reflectivity_dbz=decoded[0] if decoded and covered is not False else None,
                    reflectivity_dbz_max=decoded[1] if decoded and covered is not False else None,
                    covered=covered,
                )
            )

    return RadarSnapshot(
        observed_at=datetime.now(timezone.utc),
        resolution_m=round(scale, 1),
        samples=samples,
        undecodable_samples=undecodable,
    )


__all__ = ["RainViewerRadarProvider", "decode_samples", "colour_table", "haversine_km"]
