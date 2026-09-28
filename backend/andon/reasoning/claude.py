"""Claude-backed reasoning.

Structured output is used so the response is a validated object rather than
prose the UI has to parse. Any failure here — no credentials, rate limit,
timeout, schema violation — falls back to the deterministic writer rather than
taking the dashboard down: a situation-awareness system that goes dark when its
narrator is unavailable has failed at its one job.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import Settings
from ..domain.enums import Confidence
from ..domain.situation import NormalizedSituation, SituationReport, SituationSnapshot
from . import fallback
from .prompt import SYSTEM_PROMPT, LlmReport, build_user_message, report_schema

log = logging.getLogger(__name__)


class ReasoningEngine:
    """Turns a normalized situation into a human-readable report."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None
        self._disabled_reason: str | None = None

        if not settings.llm_enabled:
            self._disabled_reason = "LLM disabled by configuration (ANDON_LLM_ENABLED=false)"
            return

        try:
            import anthropic
        except ImportError:  # pragma: no cover - dependency is declared
            self._disabled_reason = "anthropic SDK not installed"
            return

        try:
            kwargs = {"timeout": settings.llm_timeout_s, "max_retries": 1}
            if settings.anthropic_api_key:
                kwargs["api_key"] = settings.anthropic_api_key
            # Resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / an `ant auth
            # login` profile.
            client = anthropic.AsyncAnthropic(**kwargs)
        except Exception as exc:  # noqa: BLE001
            self._disabled_reason = f"Anthropic client could not be created ({type(exc).__name__})"
            log.warning(
                "LLM unavailable, using rule-based reports",
                extra={"reason": self._disabled_reason},
            )
            return

        # The client constructs happily with no credentials and only fails at
        # request time, so check up front rather than burning a call per refresh.
        if not any(
            getattr(client, attr, None) for attr in ("api_key", "auth_token", "credentials")
        ):
            self._disabled_reason = (
                "no Anthropic credentials found (set ANTHROPIC_API_KEY) — "
                "reports are written by the rules engine"
            )
            log.warning(
                "LLM unavailable, using rule-based reports",
                extra={"reason": self._disabled_reason},
            )
            return

        self._client = client
        log.info("reasoning engine ready", extra={"model": settings.llm_model})

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def status(self) -> str:
        return "ready" if self.available else (self._disabled_reason or "unavailable")

    async def generate(
        self,
        situation: NormalizedSituation,
        history: list[SituationSnapshot],
    ) -> SituationReport:
        if self._client is None:
            return fallback.write_report(situation, history, note=self._disabled_reason)

        try:
            return await self._generate_with_claude(situation, history)
        except Exception as exc:  # noqa: BLE001 - never break the dashboard
            log.warning(
                "LLM report generation failed, falling back to rules",
                extra={"error": f"{type(exc).__name__}: {exc}", "model": self._settings.llm_model},
            )
            if _is_permanent(exc):
                # Retrying an auth or model-name problem on every refresh just
                # adds latency to every page load.
                self._client = None
                self._disabled_reason = f"disabled after a non-retryable failure: {exc}"
            return fallback.write_report(
                situation,
                history,
                note=(
                    f"LLM call failed ({type(exc).__name__}); "
                    "this report was written by the rules engine"
                ),
            )

    async def _generate_with_claude(
        self,
        situation: NormalizedSituation,
        history: list[SituationSnapshot],
    ) -> SituationReport:
        response = await self._client.messages.create(
            model=self._settings.llm_model,
            max_tokens=self._settings.llm_max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # Stable prefix: the per-refresh payload lives in the user turn.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": build_user_message(situation, history)}],
            output_config={
                "format": {"type": "json_schema", "schema": report_schema()},
                "effort": self._settings.llm_effort,
            },
        )

        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "explanation", None)
            raise RuntimeError(f"model declined to answer: {detail or 'no detail'}")

        text = next((block.text for block in response.content if block.type == "text"), None)
        if not text:
            raise RuntimeError(f"no text block in response (stop_reason={response.stop_reason})")

        parsed = LlmReport.model_validate(json.loads(text))
        usage = response.usage

        log.info(
            "llm report generated",
            extra={
                "model": response.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None),
                "overall": situation.overall.level.value,
            },
        )

        return SituationReport(
            situation_title=parsed.situation_title,
            summary=parsed.summary,
            contributing_factors=parsed.contributing_factors,
            improving=parsed.improving,
            worsening=parsed.worsening,
            operational_impact=parsed.operational_impact,
            outlook_30_60min=parsed.outlook_30_60min,
            confidence=Confidence(parsed.confidence),
            confidence_rationale=parsed.confidence_rationale,
            uncertainties=parsed.uncertainties,
            key_evidence=parsed.key_evidence,
            generator="llm",
            model=response.model,
            generated_at=datetime.now(timezone.utc),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()


def _is_permanent(exc: Exception) -> bool:
    """Auth, bad model name or malformed request will fail identically forever."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return False

    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    if isinstance(exc, anthropic.NotFoundError):
        return True  # unknown model id
    if isinstance(exc, anthropic.BadRequestError):
        return True  # schema or parameter the configured model rejects
    # The SDK raises a bare TypeError when no credential can be resolved.
    return isinstance(exc, TypeError) and "authentication" in str(exc).lower()
