"""Slot arithmetic for the hourly UTC schedule. Pure functions, no clock reads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _utc(moment: datetime) -> datetime:
    """``moment`` as UTC, refusing a naive one: ``astimezone`` would read it as LOCAL time, and the
    runtime sets TZ=Europe/Stockholm, so a naive clock read would shift every slot by 1 or 2 hours."""
    if moment.tzinfo is None:
        raise ValueError("slot arithmetic needs a timezone-aware datetime (use datetime.now(UTC))")
    return moment.astimezone(UTC)


def _hour_start(moment: datetime) -> datetime:
    return _utc(moment).replace(minute=0, second=0, microsecond=0)


def latest_slot(minutes: tuple[int, ...], now: datetime) -> datetime:
    """The most recent slot at or before ``now`` (UTC, second precision zero)."""
    base = _hour_start(now)
    candidates = [base - timedelta(hours=1) + timedelta(minutes=m) for m in minutes]
    candidates += [base + timedelta(minutes=m) for m in minutes]
    return max(c for c in candidates if c <= _utc(now))


def next_slot(minutes: tuple[int, ...], now: datetime) -> datetime:
    """The first slot strictly after ``now``."""
    base = _hour_start(now)
    candidates = [base + timedelta(minutes=m) for m in minutes]
    candidates += [base + timedelta(hours=1, minutes=m) for m in minutes]
    return min(c for c in candidates if c > _utc(now))
