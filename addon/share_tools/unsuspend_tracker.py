from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


@dataclass(frozen=True)
class UnsuspendEvent:
    cid: int
    nid: int
    detected_at: datetime
    scope_query: str


class FreshnessWindow(Enum):
    TODAY = "today"
    THIS_WEEK = "this_week"


_tracking_enabled = False
_locked_scope_query: Optional[str] = None
_previous_suspended_cids: set[int] = set()
_captured_events_by_cid: dict[int, UnsuspendEvent] = {}


def is_tracking_enabled() -> bool:
    return _tracking_enabled


def set_tracking_enabled(enabled: bool) -> None:
    global _tracking_enabled
    _tracking_enabled = enabled


def get_locked_scope_query() -> Optional[str]:
    return _locked_scope_query


def lock_scope(scope_query: str, suspended_cids: Iterable[int]) -> None:
    global _locked_scope_query

    _locked_scope_query = scope_query.strip()
    _previous_suspended_cids.clear()
    _previous_suspended_cids.update(int(cid) for cid in suspended_cids)
    clear_captured()
    set_tracking_enabled(True)


def record_snapshot(
    current_suspended_cids: Iterable[int],
    cid_to_nid: Callable[[int], int],
    now: Optional[datetime] = None,
) -> list[UnsuspendEvent]:
    if not _tracking_enabled or _locked_scope_query is None:
        return []

    detected_at = now or datetime.now()
    current_suspended = {int(cid) for cid in current_suspended_cids}
    remove_captured_cids(current_suspended)
    newly_unsuspended = sorted(_previous_suspended_cids - current_suspended)
    new_events: list[UnsuspendEvent] = []

    for cid in newly_unsuspended:
        if cid in _captured_events_by_cid:
            continue

        event = UnsuspendEvent(
            cid=cid,
            nid=int(cid_to_nid(cid)),
            detected_at=detected_at,
            scope_query=_locked_scope_query,
        )
        _captured_events_by_cid[cid] = event
        new_events.append(event)

    _previous_suspended_cids.clear()
    _previous_suspended_cids.update(current_suspended)

    return new_events


def get_captured_events() -> list[UnsuspendEvent]:
    return sorted(
        _captured_events_by_cid.values(),
        key=lambda event: (event.detected_at, event.cid),
    )


def get_captured_events_for_window(
    window: FreshnessWindow,
    now: Optional[datetime] = None,
) -> list[UnsuspendEvent]:
    current_time = now or datetime.now()

    if window == FreshnessWindow.TODAY:
        return [
            event
            for event in get_captured_events()
            if event.detected_at.date() == current_time.date()
        ]

    if window == FreshnessWindow.THIS_WEEK:
        week_start = start_of_week(current_time)
        return [
            event
            for event in get_captured_events()
            if week_start <= event.detected_at <= current_time
        ]

    return []


def get_captured_cids_for_window(
    window: FreshnessWindow,
    now: Optional[datetime] = None,
) -> list[int]:
    return sorted({event.cid for event in get_captured_events_for_window(window, now)})


def get_captured_nids_for_window(
    window: FreshnessWindow,
    now: Optional[datetime] = None,
) -> list[int]:
    return sorted({event.nid for event in get_captured_events_for_window(window, now)})


def count_for_window(window: FreshnessWindow, now: Optional[datetime] = None) -> int:
    return len(get_captured_cids_for_window(window, now))


def clear_captured() -> None:
    _captured_events_by_cid.clear()


def remove_captured_cids(card_ids: Iterable[int]) -> int:
    removed_count = 0

    for cid in set(int(card_id) for card_id in card_ids):
        if _captured_events_by_cid.pop(cid, None) is not None:
            removed_count += 1

    return removed_count


def clear_all() -> None:
    global _locked_scope_query

    set_tracking_enabled(False)
    _locked_scope_query = None
    _previous_suspended_cids.clear()
    clear_captured()


def start_of_week(value: datetime) -> datetime:
    week_start_date = value.date() - timedelta(days=value.weekday())
    return datetime.combine(week_start_date, time.min)


def save_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 1,
        "tracking_enabled": _tracking_enabled,
        "locked_scope_query": _locked_scope_query,
        "previous_suspended_cids": sorted(_previous_suspended_cids),
        "captured_events": [
            {
                "cid": event.cid,
                "nid": event.nid,
                "detected_at": event.detected_at.isoformat(),
                "scope_query": event.scope_query,
            }
            for event in get_captured_events()
        ],
    }
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def load_state(path: Path) -> None:
    if not path.exists():
        return

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        apply_state(state)
    except (OSError, ValueError, TypeError, KeyError):
        clear_all()


def apply_state(state: dict[str, Any]) -> None:
    global _locked_scope_query

    clear_all()
    _locked_scope_query = state.get("locked_scope_query")
    set_tracking_enabled(_locked_scope_query is not None)
    _previous_suspended_cids.update(
        int(cid) for cid in state.get("previous_suspended_cids", [])
    )

    for raw_event in state.get("captured_events", []):
        event = UnsuspendEvent(
            cid=int(raw_event["cid"]),
            nid=int(raw_event["nid"]),
            detected_at=datetime.fromisoformat(raw_event["detected_at"]),
            scope_query=str(raw_event["scope_query"]),
        )
        _captured_events_by_cid[event.cid] = event
