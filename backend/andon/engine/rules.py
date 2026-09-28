"""Deterministic classification rules.

Everything in this module is a pure function over numbers. Classification and
arithmetic are the system's job, not the LLM's — the model receives the labels
these functions produce and reasons about the *combination*, never about
whether 8.2 mm/h counts as heavy rain.

Thresholds are stated once, here, so they can be tuned or regionalised without
touching anything else.
"""

from __future__ import annotations

from ..domain.enums import (
    Confidence,
    Freshness,
    IncidentCategory,
    Severity,
    Trend,
)

# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------

# mm/h, standard meteorological bands (light < 2.5, moderate < 7.6, heavy < 50).
RAIN_BANDS: tuple[tuple[float, Severity], ...] = (
    (0.05, Severity.NONE),
    (2.5, Severity.LOW),
    (7.6, Severity.MEDIUM),
    (25.0, Severity.HIGH),
)
RAIN_TOP = Severity.SEVERE

# km/h gusts. 40 = fresh breeze on two-wheelers, 62 = gale (Beaufort 8).
WIND_BANDS: tuple[tuple[float, Severity], ...] = (
    (25.0, Severity.NONE),
    (40.0, Severity.LOW),
    (62.0, Severity.MEDIUM),
    (89.0, Severity.HIGH),
)
WIND_TOP = Severity.SEVERE

# metres. Descending: more visibility is better, so bands read the other way.
VISIBILITY_BANDS: tuple[tuple[float, Severity], ...] = (
    (5000.0, Severity.NONE),
    (2000.0, Severity.LOW),
    (1000.0, Severity.MEDIUM),
    (500.0, Severity.HIGH),
)
VISIBILITY_TOP = Severity.SEVERE

# Radar reflectivity (dBZ). An engineering convention for turning an observed
# echo strength into an operational band — NOT something any radar provider
# publishes. Evidence built from it is labelled as a derivation.
RADAR_DBZ_BANDS: tuple[tuple[float, Severity], ...] = (
    (15.0, Severity.NONE),
    (30.0, Severity.LOW),
    (40.0, Severity.MEDIUM),
    (50.0, Severity.HIGH),
)
RADAR_DBZ_TOP = Severity.SEVERE
RADAR_BAND_DERIVATION = "engineering convention on dBZ thresholds (15/30/40/50), not provider data"

# --------------------------------------------------------------------------
# Traffic
# --------------------------------------------------------------------------

# Congestion index = 1 - (current speed / free-flow speed).
CONGESTION_BANDS: tuple[tuple[float, Severity], ...] = (
    (0.15, Severity.NONE),
    (0.35, Severity.LOW),
    (0.55, Severity.MEDIUM),
    (0.75, Severity.HIGH),
)
CONGESTION_TOP = Severity.SEVERE

# --------------------------------------------------------------------------
# Incidents
# --------------------------------------------------------------------------

INCIDENT_BASE: dict[IncidentCategory, Severity] = {
    IncidentCategory.FLOODING: Severity.SEVERE,
    IncidentCategory.WATERLOGGING: Severity.HIGH,
    IncidentCategory.ROAD_CLOSURE: Severity.HIGH,
    # The road stays open, at reduced capacity.
    IncidentCategory.LANE_CLOSURE: Severity.MEDIUM,
    IncidentCategory.ACCIDENT: Severity.MEDIUM,
    IncidentCategory.EVENT: Severity.MEDIUM,
    IncidentCategory.HAZARD: Severity.MEDIUM,
    IncidentCategory.CONGESTION: Severity.LOW,
    IncidentCategory.CONSTRUCTION: Severity.LOW,
    IncidentCategory.OTHER: Severity.LOW,
}

# An incident 4 km away matters less to this kitchen than one 400 m away.
DISTANCE_ATTENUATION: tuple[tuple[float, int], ...] = (
    (1.0, 0),
    (2.5, 1),
    (5.0, 2),
)
DISTANCE_ATTENUATION_BEYOND = 3


def _band(value: float, bands: tuple[tuple[float, Severity], ...], top: Severity) -> Severity:
    for threshold, severity in bands:
        if value < threshold:
            return severity
    return top


def _band_descending(
    value: float, bands: tuple[tuple[float, Severity], ...], top: Severity
) -> Severity:
    for threshold, severity in bands:
        if value >= threshold:
            return severity
    return top


def rain_severity(mm_per_hour: float | None) -> Severity | None:
    if mm_per_hour is None:
        return None
    return _band(max(0.0, mm_per_hour), RAIN_BANDS, RAIN_TOP)


def wind_severity(gust_kmh: float | None) -> Severity | None:
    if gust_kmh is None:
        return None
    return _band(max(0.0, gust_kmh), WIND_BANDS, WIND_TOP)


def visibility_severity(metres: float | None) -> Severity | None:
    if metres is None:
        return None
    return _band_descending(max(0.0, metres), VISIBILITY_BANDS, VISIBILITY_TOP)


def radar_severity(dbz: float | None) -> Severity | None:
    """Band for a reflectivity *lower bound*; see RADAR_BAND_DERIVATION."""
    if dbz is None:
        return None
    return _band(dbz, RADAR_DBZ_BANDS, RADAR_DBZ_TOP)


def congestion_severity(index: float | None) -> Severity | None:
    if index is None:
        return None
    return _band(max(0.0, index), CONGESTION_BANDS, CONGESTION_TOP)


def incident_severity(
    category: IncidentCategory,
    distance_km: float | None,
    provider_hint: Severity | None = None,
) -> Severity:
    base = INCIDENT_BASE.get(category, Severity.LOW)
    if provider_hint is not None and provider_hint.rank > base.rank:
        base = provider_hint

    steps = DISTANCE_ATTENUATION_BEYOND
    if distance_km is None:
        steps = 1  # unknown distance inside the search radius: mild discount
    else:
        for limit, attenuation in DISTANCE_ATTENUATION:
            if distance_km <= limit:
                steps = attenuation
                break
    return Severity.from_rank(base.rank - steps)


# --------------------------------------------------------------------------
# Rain descriptors (used for headlines, not for logic)
# --------------------------------------------------------------------------

RAIN_LABELS: dict[Severity, str] = {
    Severity.NONE: "No rain",
    Severity.LOW: "Light rain",
    Severity.MEDIUM: "Moderate rain",
    Severity.HIGH: "Heavy rain",
    Severity.SEVERE: "Violent rain",
}

CONGESTION_LABELS: dict[Severity, str] = {
    Severity.NONE: "Free flowing",
    Severity.LOW: "Light",
    Severity.MEDIUM: "Moderate",
    Severity.HIGH: "Heavy",
    Severity.SEVERE: "Severe",
}

OVERALL_LABELS: dict[Severity, str] = {
    Severity.NONE: "NORMAL OPERATIONS",
    Severity.LOW: "LOW DISRUPTION",
    Severity.MEDIUM: "MODERATE DISRUPTION",
    Severity.HIGH: "HIGH DISRUPTION",
    Severity.SEVERE: "SEVERE DISRUPTION",
}


# --------------------------------------------------------------------------
# Freshness & confidence
# --------------------------------------------------------------------------


def freshness_of(
    age_seconds: int | None,
    fresh_s: int = 300,
    recent_s: int = 900,
    aging_s: int = 2700,
) -> Freshness:
    if age_seconds is None:
        return Freshness.UNKNOWN
    if age_seconds < 0:
        # Forecast-anchored timestamps can sit slightly in the future.
        return Freshness.FRESH
    if age_seconds < fresh_s:
        return Freshness.FRESH
    if age_seconds < recent_s:
        return Freshness.RECENT
    if age_seconds < aging_s:
        return Freshness.AGING
    return Freshness.STALE


FRESHNESS_PENALTY: dict[Freshness, int] = {
    Freshness.FRESH: 0,
    Freshness.RECENT: 0,
    Freshness.AGING: 1,
    Freshness.STALE: 2,
    Freshness.UNKNOWN: 1,
}


def adjust_confidence(base: Confidence, freshness: Freshness, extra_penalty: int = 0) -> Confidence:
    penalty = FRESHNESS_PENALTY.get(freshness, 1) + max(0, extra_penalty)
    return Confidence.from_rank(base.rank - penalty)


# A station observation is real, but it is real *where the station is*. Rain is
# patchy enough that a reading tens of kilometres away is weak evidence about
# this restaurant, so distance costs confidence.
STATION_DISTANCE_BANDS: tuple[tuple[float, int], ...] = (
    (10.0, 0),
    (30.0, 1),
)
STATION_DISTANCE_MAX_PENALTY = 2


def station_distance_penalty(distance_km: float | None) -> int:
    """Confidence penalty for a point observation taken ``distance_km`` away."""
    if distance_km is None:
        return 0  # not a station-based source
    for limit, penalty in STATION_DISTANCE_BANDS:
        if distance_km <= limit:
            return penalty
    return STATION_DISTANCE_MAX_PENALTY


# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------


def trend_from_delta(delta: float | None, rising_is_worse: bool, threshold: float) -> Trend:
    """Classify a change. ``threshold`` is the dead-band half-width."""
    if delta is None:
        return Trend.UNKNOWN
    if abs(delta) < threshold:
        return Trend.STEADY
    rising = delta > 0
    if rising == rising_is_worse:
        return Trend.WORSENING
    return Trend.IMPROVING


# --------------------------------------------------------------------------
# Overall disruption
# --------------------------------------------------------------------------

# Peak signal dominates; the others compound on top of it. Tuned so that one
# heavy signal alone reads MODERATE, two read HIGH, and three read SEVERE.
PEAK_WEIGHT = 0.70
SUPPORT_WEIGHT = 0.35


def overall_from_signals(severities: list[Severity]) -> tuple[Severity, int]:
    """Combine available signal severities into a level and a 0-100 score.

    Unavailable signals must be excluded by the caller rather than passed as
    ``NONE`` — "we don't know" is not the same as "it's fine".
    """
    if not severities:
        return Severity.NONE, 0

    ranks = sorted((s.rank for s in severities), reverse=True)
    combined = ranks[0] * PEAK_WEIGHT + sum(ranks[1:]) * SUPPORT_WEIGHT
    combined = min(combined, 4.0)

    level = Severity.from_rank(int(round(combined)))
    score = int(round(combined / 4.0 * 100))
    return level, max(0, min(100, score))
