"""Deterministic system context builder.

Produces a structured ``system_context`` dict that is injected at every
request entry point — before any LLM call — so downstream components
(planner, SQL compiler, narrator) never need to guess wall-clock time,
timezone, or tenant fiscal settings.

Rules
-----
* Output is derived 100% from system clock + configuration.  LLM is NEVER
  consulted.
* Every field is REQUIRED.  No nulls, no optionals.
* Object is immutable after construction (frozen dataclass).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from typing import Any

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover – fallback for older envs
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-reuse-import]

from app.core.config import settings


# ---------------------------------------------------------------------------
# Immutable context dataclass (internal representation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemContext:
    """Structured system context produced once per request."""

    current_date: str            # YYYY-MM-DD
    current_timestamp: str       # ISO 8601 with timezone offset
    timezone: str                # IANA TZ name
    fiscal_year_start: str       # YYYY-MM-DD
    default_time_window: str     # e.g. "last_30_days"
    week_start: str              # "monday" | "sunday"
    currency: str                # ISO 4217 currency code

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-serialisable representation."""
        return {
            "current_date": self.current_date,
            "current_timestamp": self.current_timestamp,
            "timezone": self.timezone,
            "fiscal_year_start": self.fiscal_year_start,
            "default_time_window": self.default_time_window,
            "week_start": self.week_start,
            "currency": self.currency,
        }

    def to_prompt_block(self) -> str:
        """Compact single-line representation suitable for LLM prompt injection."""
        return (
            f"[SYSTEM CONTEXT] date={self.current_date} "
            f"tz={self.timezone} "
            f"fiscal_year_start={self.fiscal_year_start} "
            f"currency={self.currency} "
            f"week_start={self.week_start}"
        )


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class ContextBuilder:
    """Deterministic factory for :class:`SystemContext`.

    Configuration is loaded from :data:`app.core.config.settings`.
    All temporal values are resolved against the live system clock —
    never via LLM inference.

    Usage::

        ctx = ContextBuilder().build()
        print(ctx.as_dict())
    """

    def build(self) -> SystemContext:
        """Build and return an immutable :class:`SystemContext`.

        Raises
        ------
        ValueError
            If the configured timezone is not a valid IANA name.
        """
        tz_name = settings.system_timezone
        try:
            tz = ZoneInfo(tz_name)
        except (KeyError, Exception) as exc:
            raise ValueError(
                f"[context-builder] Invalid IANA timezone in config: {tz_name!r}"
            ) from exc

        now: datetime = datetime.now(tz=tz)

        # fiscal_year_start: loaded from config, validated as YYYY-MM-DD
        fiscal_raw = settings.fiscal_year_start.strip()
        try:
            fiscal_date = date.fromisoformat(fiscal_raw)
        except ValueError as exc:
            raise ValueError(
                f"[context-builder] Invalid fiscal_year_start in config: {fiscal_raw!r}"
            ) from exc
        # Advance to current year if the configured date has already passed
        if fiscal_date.year < now.year:
            fiscal_date = fiscal_date.replace(year=now.year)
        if fiscal_date > now.date():
            # Still in the future this year → use last year's start
            fiscal_date = fiscal_date.replace(year=now.year - 1)

        return SystemContext(
            current_date=now.strftime("%Y-%m-%d"),
            current_timestamp=now.isoformat(timespec="seconds"),
            timezone=tz_name,
            fiscal_year_start=fiscal_date.isoformat(),
            default_time_window=settings.default_time_window,
            week_start=settings.week_start,
            currency=settings.system_currency,
        )
