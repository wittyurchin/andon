"""NDMA Sachet — India's national disaster-alert feed.

Ported from a real, production Google Apps Script (a restaurant-chain "Live
Weather" sheet) that fuses this feed with WeatherAPI.com and a satellite
rainfall job to decide rain intensity per store. That sheet matches alerts to
stores by a maintained store-to-district table this project does not have;
this adapter's honest substitute, and its real limitation, is explained below.

Verified live (2026-09-28):

* ``https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml`` — a
  public RSS index, no key, ``<copyright>public domain</copyright>`` stated in
  the feed itself. Each item's ``<link>`` is
  ``FetchXMLFile?identifier=<id>``, returning the alert as CAP 1.2 XML
  (``xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2"``), with one
  ``<cap:info>`` block per language (English is always present, tagged
  ``en``/``en-IN``).
* Each ``<cap:info>`` carries a ``<cap:parameter>`` with ``valueName`` "Polygon
  URL" pointing at ``FetchPolygonXMLFile?identifier=<id>`` — the actual alert
  geometry. **That endpoint returned HTTP 403 in every live test from this
  environment**, so point-in-polygon matching against a restaurant's exact
  coordinates is not available to us; only ``<cap:areaDesc>`` free text (e.g.
  "WEST UP", not a clean district name) is reachable.
* Without a maintained district/zone alias table (what the source Apps Script
  has and this project does not), district-level text matching would be
  guessing at names we cannot verify. What we *can* verify reliably is the
  **state** a restaurant sits in — a small, stable list, unlike India's
  districts — via reverse geocoding, and check whether that state's name
  appears in the alert's sender/area/headline text. **This is coarser than the
  source system (state, not district) and every alert produced here says so
  in its own headline.**
* State is resolved once per restaurant via OpenStreetMap Nominatim
  (``nominatim.openstreetmap.org/reverse``, ODbL, public, no key). Nominatim's
  usage policy caps public use at 1 request/second and requires caching
  results — satisfied here because a restaurant's state never changes, so the
  lookup is cached for the process's lifetime after the first call.
* Severity is graded by keyword, not by CAP's own ``<cap:severity>`` — ported
  directly from the source script's comment: "states use labels differently."
  A same-day outlook ("next 24/48/72 hours", "tomorrow") is downgraded to
  ``watch`` rather than treated as an imminent warning, matching the source
  logic. CWC-sourced alerts (Central Water Commission — river-level flood
  forecasts) are always ``watch``, not a rain alert.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ...domain.enums import HealthStatus, Severity
from ...domain.signals import FetchContext, GeoPoint, SourceRef, WeatherAlert, WeatherSnapshot
from ...geo import location_key
from ..base import WeatherProvider

RSS_URL = "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"
CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
REVERSE_GEOCODE_URL = "https://nominatim.openstreetmap.org/reverse"

# How often the nationwide feed itself is re-polled, independent of the
# per-restaurant cache the rest of the app wraps every provider in — the
# feed is the same for every restaurant, so it would be wasteful (and rude to
# a free government feed) to re-fetch it once per restaurant per refresh.
FEED_REFRESH_S = 300
CAP_BATCH = 20

RANK = {"watch": Severity.LOW, "moderate": Severity.MEDIUM, "heavy": Severity.HIGH}

_HEAVY_RE = re.compile(
    r"(very heavy|extremely heavy|heavy rain|heavy to very|heavy spells?|heavy falls?|"
    r"intense rain|torrential|cloud ?burst|flash flood|severe thunderstorm|"
    r"thunderstorms? with hail|hail ?storm)",
    re.I,
)
_MODERATE_RE = re.compile(
    r"(moderate rain|moderate to heavy|rather heavy|moderate thunderstorm|moderate spell)", re.I
)
_WATCH_RE = re.compile(r"(rain|shower|thunder|lightning|drizzle)", re.I)
_OUTLOOK_RE = re.compile(r"(next|coming)\s+(24|48|72)\s*(hours|hrs)|next\s+\d+\s+days|tomorrow", re.I)


def classify_alert(text: str, sender: str) -> str:
    """Grades an alert by its words, not its official severity label.

    Ported from the production Apps Script's ``rlClassifyAlert_`` (river-level
    CWC forecasts are a watch, not a rain alert; a same-day outlook is a
    watch, not an imminent warning).
    """
    if "cwc" in (sender or "").lower():
        return "watch"
    t = f" {text.lower()} "
    if _HEAVY_RE.search(t):
        level = "heavy"
    elif _MODERATE_RE.search(t.replace("light to moderate", "light")):
        level = "moderate"
    elif _WATCH_RE.search(t):
        level = "watch"
    else:
        return "ignore"
    if _OUTLOOK_RE.search(t) and level in ("heavy", "moderate"):
        return "watch"
    return level


def _norm(s: str) -> str:
    return " " + re.sub(r"[^a-z]+", " ", (s or "").lower()).strip() + " "


class _ParsedAlert:
    __slots__ = ("identifier", "level", "text", "sender", "headline", "effective", "expires")

    def __init__(self, identifier, level, text, sender, headline, effective, expires):
        self.identifier = identifier
        self.level = level
        self.text = text
        self.sender = sender
        self.headline = headline
        self.effective = effective
        self.expires = expires

    def active_at(self, now: datetime) -> bool:
        if self.level == "ignore":
            return False
        if self.effective and self.effective > now:
            return False
        if self.expires and self.expires < now:
            return False
        return True

    def covers_state(self, state: str) -> bool:
        return _norm(state) in self.text


class NdmaSachetAlertProvider(WeatherProvider):
    """No key, no licence gate — the feed states its own copyright as public
    domain. Outside India this always yields no alerts, by construction: no
    Indian state name will ever match."""

    def __init__(self, timeout_s: float = 8.0, clock=None) -> None:
        # Injectable so tests can sit inside a captured alert's validity window.
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": "andon-situation-awareness/0.1"},
        )
        self._alerts: dict[str, _ParsedAlert] = {}
        self._feed_fetched_at: float = 0.0
        self._state_cache: dict[str, str | None] = {}

    @property
    def source(self) -> SourceRef:
        return SourceRef(
            id="ndma-sachet",
            name="NDMA Sachet (India disaster alerts)",
            kind="weather",
            mode="live",
            attribution="National Disaster Management Authority (Sachet) — public domain",
            docs_url="https://sachet.ndma.gov.in/",
        )

    def precheck(self) -> tuple[HealthStatus, dict[str, Any]] | None:
        return None

    async def _fetch(self, point: GeoPoint, context: FetchContext) -> WeatherSnapshot:
        now = self._clock()
        await self._ensure_feed_fresh()
        state = await self._state_for(point)

        alerts: list[WeatherAlert] = []
        if state:
            active = [a for a in self._alerts.values() if a.active_at(now) and a.covers_state(state)]
            active.sort(key=lambda a: (-RANK[a.level].rank, a.expires or now))
            for a in active:
                alerts.append(WeatherAlert(
                    event=a.headline[:120],
                    headline=(
                        f"[{state} — state-level, exact area not confirmed] {a.headline} "
                        f"(source: {a.sender})"
                    ),
                    severity_hint=RANK[a.level],
                    starts_at=a.effective,
                    ends_at=a.expires,
                ))

        return WeatherSnapshot(
            observed_at=now,
            reported={"state_checked": state} if state else {},
            alerts=alerts,
            alerts_supported=True,
        )

    # -- the nationwide feed, fetched once and shared across restaurants ----

    async def _ensure_feed_fresh(self) -> None:
        if time.monotonic() - self._feed_fetched_at < FEED_REFRESH_S and self._feed_fetched_at:
            return
        response = await self._client.get(RSS_URL)
        response.raise_for_status()
        ids = _parse_rss_ids(response.text)

        new_ids = [i for i in ids if i not in self._alerts]
        for start in range(0, len(new_ids), CAP_BATCH):
            batch = new_ids[start:start + CAP_BATCH]
            responses = await asyncio.gather(
                *(self._client.get(_cap_url(i)) for i in batch), return_exceptions=True
            )
            for identifier, resp in zip(batch, responses, strict=True):
                if isinstance(resp, BaseException) or resp.status_code != 200:
                    continue
                parsed = _parse_cap(identifier, resp.text)
                if parsed is not None:
                    self._alerts[identifier] = parsed

        now = self._clock()
        stale = [i for i, a in self._alerts.items() if a.expires and a.expires < now - timedelta(hours=6)]
        for i in stale:
            del self._alerts[i]
        self._feed_fetched_at = time.monotonic()

    # -- which Indian state a restaurant is in, resolved once and cached ----

    async def _state_for(self, point: GeoPoint) -> str | None:
        key = location_key(point, precision=2)  # ~1.1 km — well inside state borders
        if key in self._state_cache:
            return self._state_cache[key]
        response = await self._client.get(
            REVERSE_GEOCODE_URL,
            params={"lat": f"{point.lat:.5f}", "lon": f"{point.lon:.5f}", "format": "jsonv2", "zoom": 5},
            headers={"User-Agent": "andon-situation-awareness/0.1 (github.com/wittyurchin/andon)"},
        )
        response.raise_for_status()
        payload = response.json() or {}
        address = payload.get("address") or {}
        state = address.get("state") if address.get("country_code") == "in" else None
        self._state_cache[key] = state
        return state

    async def aclose(self) -> None:
        await self._client.aclose()


def _cap_url(identifier: str) -> str:
    return f"https://sachet.ndma.gov.in/cap_public_website/FetchXMLFile?identifier={identifier}"


def _parse_rss_ids(xml_text: str) -> list[str]:
    from xml.etree import ElementTree as ET

    root = ET.fromstring(xml_text)
    return [g.text.strip() for item in root.iter("item") if (g := item.find("guid")) is not None and g.text]


def _parse_cap(identifier: str, xml_text: str) -> _ParsedAlert | None:
    from xml.etree import ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    infos = root.findall("cap:info", CAP_NS)
    info = next((i for i in infos if (i.findtext("cap:language", "", CAP_NS) or "").lower().startswith("en")), None)
    if info is None:
        info = infos[0] if infos else None
    if info is None:
        return None

    def text(tag: str) -> str:
        return (info.findtext(f"cap:{tag}", "", CAP_NS) or "").strip()

    sender = (root.findtext("cap:sender", "", CAP_NS) or "").strip()
    headline = text("headline") or text("event")
    areas = " ".join(a.findtext("cap:areaDesc", "", CAP_NS) or "" for a in info.findall("cap:area", CAP_NS))
    combined = " ".join([sender, areas, text("event"), headline, text("description")])
    level = classify_alert(combined, sender)

    return _ParsedAlert(
        identifier=identifier,
        level=level,
        text=_norm(combined),
        sender=sender or "unknown sender",
        headline=headline or "Weather alert",
        effective=_parse_cap_time(text("effective") or text("onset")),
        expires=_parse_cap_time(text("expires")),
    )


def _parse_cap_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
