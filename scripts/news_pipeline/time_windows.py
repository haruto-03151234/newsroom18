from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
EDITION_HOURS = {"06": 6, "12": 12, "18": 18}


@dataclass(frozen=True)
class CoverageWindow:
    edition: str
    start: datetime
    end: datetime

    @property
    def id(self) -> str:
        return f"{self.end:%Y-%m-%d}-{self.edition}"


def resolve_edition(now: datetime) -> str:
    """Return the most recently completed nominal JST edition."""
    local = _as_jst(now)
    if local.hour >= 18:
        return "18"
    if local.hour >= 12:
        return "12"
    if local.hour >= 6:
        return "06"
    return "18"


def coverage_window(now: datetime, edition: str = "auto") -> CoverageWindow:
    """Resolve a fixed, half-open coverage window in Asia/Tokyo.

    A requested edition is anchored to the latest occurrence of that wall-clock
    time. This keeps a delayed 18:00 run at 01:00 on the following day attached
    to the intended previous-day edition.
    """
    local = _as_jst(now)
    resolved = resolve_edition(local) if edition == "auto" else edition.zfill(2)
    if resolved not in EDITION_HOURS:
        raise ValueError("edition must be one of auto, 06, 12, or 18")

    nominal_end = local.replace(
        hour=EDITION_HOURS[resolved], minute=0, second=0, microsecond=0
    )
    if local < nominal_end:
        nominal_end -= timedelta(days=1)

    if resolved == "06":
        start = nominal_end - timedelta(hours=12)
    else:
        start = nominal_end - timedelta(hours=6)
    return CoverageWindow(edition=resolved, start=start, end=nominal_end)


def missing_windows(
    last_completed_end: str | None,
    target: CoverageWindow,
    limit: int = 9,
) -> list[CoverageWindow]:
    """Return unprocessed logical slots up to target, oldest first."""
    if not last_completed_end:
        return [target]
    try:
        cursor = datetime.fromisoformat(last_completed_end).astimezone(JST)
    except (TypeError, ValueError):
        return [target]
    cursor = cursor.replace(minute=0, second=0, microsecond=0)
    windows: list[CoverageWindow] = []
    while cursor < target.end:
        if cursor.hour == 6:
            next_end = cursor + timedelta(hours=6)
            edition = "12"
        elif cursor.hour == 12:
            next_end = cursor + timedelta(hours=6)
            edition = "18"
        elif cursor.hour == 18:
            next_end = cursor + timedelta(hours=12)
            edition = "06"
        else:
            return [target]
        start = next_end - timedelta(hours=12 if edition == "06" else 6)
        windows.append(CoverageWindow(edition=edition, start=start, end=next_end))
        cursor = next_end
        if len(windows) > 60:
            break
    if not windows:
        return []
    return windows[-max(1, limit) :]


def _as_jst(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(JST)
