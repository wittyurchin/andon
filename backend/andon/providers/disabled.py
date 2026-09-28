"""Adapter boundaries for sources with no verified, authorised access path.

Each entry states the exact blocker (see docs/provider-verification.md). They
appear in source health as ``disabled`` and are never called. Enabling one
means implementing a real provider class — the rest of the system already
knows how to use its evidence.
"""

from __future__ import annotations

from ..domain.signals import SourceRef
from .base import DisabledProvider


def ksndmc_weather() -> DisabledProvider:
    return DisabledProvider(
        SourceRef(
            id="ksndmc",
            name="KSNDMC (Karnataka) rain gauges and weather stations",
            kind="weather",
            mode="live",
            attribution="Karnataka State Natural Disaster Monitoring Centre",
            docs_url="https://ksndmc.org/en/Activities/Weather",
        ),
        "No documented API or feed; KSNDMC's site disclaimer restricts commercial and "
        "decision-making use. Requires a data-access agreement (office@ksndmc.org).",
        requires="authorisation",
    )


def mappls_incidents() -> DisabledProvider:
    return DisabledProvider(
        SourceRef(
            id="mappls-events",
            name="Mappls traffic events",
            kind="incidents",
            mode="live",
            attribution="Mappls (MapmyIndia)",
            docs_url="https://about.mappls.com/traffic/",
        ),
        "Mappls advertises traffic-event feeds but publishes no endpoint for them "
        "(checked GitHub REST docs, both auth branches). Confirm availability with Mappls.",
        requires="verification",
    )


def bengaluru_traffic_police() -> DisabledProvider:
    return DisabledProvider(
        SourceRef(
            id="btp",
            name="Bengaluru Traffic Police advisories",
            kind="incidents",
            mode="live",
            attribution="Bengaluru Traffic Police",
        ),
        "Interactive map only; no documented machine-readable feed. Not scraped.",
        requires="verification",
    )


def bmc_disaster_management() -> DisabledProvider:
    return DisabledProvider(
        SourceRef(
            id="bmc-dm",
            name="BMC Disaster Management (Mumbai)",
            kind="incidents",
            mode="live",
            attribution="Brihanmumbai Municipal Corporation",
            docs_url="https://dm.mcgm.gov.in/",
        ),
        "Portal only; no documented machine-readable feed for waterlogging or road incidents.",
        requires="verification",
    )
