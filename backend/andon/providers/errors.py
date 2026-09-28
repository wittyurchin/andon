"""Typed provider failures.

Each maps onto one source-health state, so the registry can report *why* a
source is not contributing without parsing error strings.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import HealthStatus


class ProviderError(Exception):
    health: HealthStatus = HealthStatus.UNAVAILABLE

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class ProviderDisabled(ProviderError):
    """No verified, authorised access path exists; the adapter is never called."""

    health = HealthStatus.DISABLED


class ProviderMisconfigured(ProviderError):
    """A credential or setting the adapter needs is missing."""

    health = HealthStatus.MISCONFIGURED


class ProviderUnauthorized(ProviderError):
    """The provider rejected our credentials or entitlement."""

    health = HealthStatus.UNAUTHORIZED


class ProviderSchemaError(ProviderError):
    """The response did not have the documented shape. Treated as unavailable."""

    health = HealthStatus.UNAVAILABLE
