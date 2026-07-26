from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .tracker_database import (
    DEFAULT_RETENTION_DAYS,
    StoredTrackerState,
    StoredUnsuspendEvent,
    TrackerDatabase,
    TrackerMigrationError,
)


@dataclass(frozen=True)
class UnsuspendEvent:
    cid: int
    nid: int
    detected_at: datetime
    scope_query: str


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("Date range start must be on or before its end.")


class FreshnessWindow(Enum):
    TODAY = "today"
    THIS_WEEK = "this_week"


_tracking_enabled = False
_locked_scope_query: Optional[str] = None
_previous_suspended_cids: set[int] = set()
_captured_events_by_cid: dict[int, UnsuspendEvent] = {}
_database: Optional[TrackerDatabase] = None
_retention_days = DEFAULT_RETENTION_DAYS


def is_tracking_enabled() -> bool:
    return _tracking_enabled


def set_tracking_enabled(enabled: bool) -> None:
    global _tracking_enabled
    _tracking_enabled = enabled


def get_locked_scope_query() -> Optional[str]:
    return _locked_scope_query


def get_retention_days() -> int:
    return _retention_days


def set_retention_days(
    retention_days: int,
    now: Optional[datetime] = None,
) -> int:
    global _retention_days

    if retention_days < 0:
        raise ValueError("Retention days cannot be negative.")

    cutoff_date = retention_cutoff_date(retention_days, (now or datetime.now()).date())
    cutoff = (
        datetime.combine(cutoff_date, time.min) if cutoff_date is not None else None
    )

    if _database is not None:
        _database.set_retention_days_and_sweep(retention_days, cutoff)

    _retention_days = retention_days
    return _remove_events_detected_before(cutoff_date)


def lock_scope(scope_query: str, suspended_cids: Iterable[int]) -> None:
    global _locked_scope_query

    _locked_scope_query = scope_query.strip()
    _previous_suspended_cids.clear()
    _previous_suspended_cids.update(int(cid) for cid in suspended_cids)
    _captured_events_by_cid.clear()
    set_tracking_enabled(True)
    persist_state()


def record_snapshot(
    current_suspended_cids: Iterable[int],
    cid_to_nid: Callable[[int], Optional[int]],
    now: Optional[datetime] = None,
    current_in_scope_cids: Optional[Iterable[int]] = None,
) -> list[UnsuspendEvent]:
    if not _tracking_enabled or _locked_scope_query is None:
        return []

    detected_at = now or datetime.now()
    current_suspended = {int(cid) for cid in current_suspended_cids}
    previous_suspended = set(_previous_suspended_cids)
    current_in_scope = (
        {int(cid) for cid in current_in_scope_cids}
        if current_in_scope_cids is not None
        else previous_suspended | current_suspended
    )
    current_suspended &= current_in_scope
    cutoff_date = retention_cutoff_date(_retention_days, detected_at.date())
    expired_event_cids = {
        event.cid
        for event in _captured_events_by_cid.values()
        if cutoff_date is not None and event.detected_at.date() < cutoff_date
    }
    removed_event_cids = (
        current_suspended & _captured_events_by_cid.keys()
    ) | expired_event_cids
    newly_unsuspended = sorted(
        (previous_suspended - current_suspended) & current_in_scope
    )
    new_events: list[UnsuspendEvent] = []

    for cid in newly_unsuspended:
        if cid in _captured_events_by_cid and cid not in expired_event_cids:
            continue

        nid = cid_to_nid(cid)

        if nid is None:
            continue

        event = UnsuspendEvent(
            cid=cid,
            nid=int(nid),
            detected_at=detected_at,
            scope_query=_locked_scope_query,
        )
        new_events.append(event)

    persist_snapshot(
        baseline_added=current_suspended - previous_suspended,
        baseline_removed=previous_suspended - current_suspended,
        removed_event_cids=removed_event_cids,
        added_events=new_events,
    )
    _remove_captured_cids(removed_event_cids)
    _captured_events_by_cid.update((event.cid, event) for event in new_events)
    _previous_suspended_cids.clear()
    _previous_suspended_cids.update(current_suspended)

    return new_events


def sync_baseline_without_capturing(current_suspended_cids: Iterable[int]) -> None:
    if not _tracking_enabled or _locked_scope_query is None:
        return

    current_suspended = {int(cid) for cid in current_suspended_cids}
    previous_suspended = set(_previous_suspended_cids)
    removed_event_cids = current_suspended & _captured_events_by_cid.keys()
    persist_snapshot(
        baseline_added=current_suspended - previous_suspended,
        baseline_removed=previous_suspended - current_suspended,
        removed_event_cids=removed_event_cids,
        added_events=[],
    )
    _remove_captured_cids(removed_event_cids)
    _previous_suspended_cids.clear()
    _previous_suspended_cids.update(current_suspended)


def get_captured_events() -> list[UnsuspendEvent]:
    return sorted(
        _captured_events_by_cid.values(),
        key=lambda event: (event.detected_at, event.cid),
    )


def get_captured_events_for_window(
    window: FreshnessWindow,
    now: Optional[datetime] = None,
) -> list[UnsuspendEvent]:
    return get_captured_events_for_date_range(date_range_for_window(window, now))


def date_range_for_window(
    window: FreshnessWindow,
    now: Optional[datetime] = None,
) -> DateRange:
    current_time = now or datetime.now()

    if window == FreshnessWindow.TODAY:
        return DateRange(start=current_time.date(), end=current_time.date())

    if window == FreshnessWindow.THIS_WEEK:
        return DateRange(
            start=start_of_week(current_time).date(),
            end=current_time.date(),
        )

    raise ValueError(f"Unsupported freshness window: {window}")


def get_captured_events_for_date_range(
    date_range: DateRange,
) -> list[UnsuspendEvent]:
    return [
        event
        for event in get_captured_events()
        if date_range.start <= event.detected_at.date() <= date_range.end
    ]


def get_captured_cids_for_window(
    window: FreshnessWindow,
    now: Optional[datetime] = None,
) -> list[int]:
    return sorted({event.cid for event in get_captured_events_for_window(window, now)})


def get_captured_cids_for_date_range(date_range: DateRange) -> list[int]:
    return sorted(
        {event.cid for event in get_captured_events_for_date_range(date_range)}
    )


def get_captured_nids_for_window(
    window: FreshnessWindow,
    now: Optional[datetime] = None,
) -> list[int]:
    return sorted({event.nid for event in get_captured_events_for_window(window, now)})


def get_captured_nids_for_date_range(date_range: DateRange) -> list[int]:
    return sorted(
        {event.nid for event in get_captured_events_for_date_range(date_range)}
    )


def count_for_window(window: FreshnessWindow, now: Optional[datetime] = None) -> int:
    return len(get_captured_cids_for_window(window, now))


def count_for_date_range(date_range: DateRange) -> int:
    return len(get_captured_cids_for_date_range(date_range))


def retention_cutoff_date(retention_days: int, today: date) -> Optional[date]:
    if retention_days < 0:
        raise ValueError("Retention days cannot be negative.")

    if retention_days == 0:
        return None

    return today - timedelta(days=retention_days - 1)


def sweep_expired_events(now: Optional[datetime] = None) -> int:
    cutoff_date = retention_cutoff_date(
        _retention_days,
        (now or datetime.now()).date(),
    )

    if cutoff_date is None:
        return 0

    if _database is not None:
        _database.sweep_events_before(datetime.combine(cutoff_date, time.min))

    return _remove_events_detected_before(cutoff_date)


def _remove_events_detected_before(cutoff: Optional[date]) -> int:
    if cutoff is None:
        return 0

    expired_card_ids = {
        event.cid
        for event in _captured_events_by_cid.values()
        if event.detected_at.date() < cutoff
    }
    return _remove_captured_cids(expired_card_ids)


def clear_captured() -> None:
    if _database is not None:
        _database.clear_events()

    _captured_events_by_cid.clear()


def remove_captured_cids(card_ids: Iterable[int]) -> int:
    normalized_card_ids = {int(card_id) for card_id in card_ids}
    removed_count = len(normalized_card_ids & _captured_events_by_cid.keys())

    if _database is not None:
        _database.remove_events(normalized_card_ids)

    _remove_captured_cids(normalized_card_ids)
    return removed_count


def _remove_captured_cids(card_ids: Iterable[int]) -> int:
    removed_count = 0

    for cid in set(int(card_id) for card_id in card_ids):
        if _captured_events_by_cid.pop(cid, None) is not None:
            removed_count += 1

    return removed_count


def clear_all() -> None:
    global _locked_scope_query, _retention_days

    set_tracking_enabled(False)
    _locked_scope_query = None
    _retention_days = DEFAULT_RETENTION_DAYS
    _previous_suspended_cids.clear()
    _captured_events_by_cid.clear()
    persist_state()


def start_of_week(value: datetime) -> datetime:
    week_start_date = value.date() - timedelta(days=value.weekday())
    return datetime.combine(week_start_date, time.min)


def initialize_storage(
    database_path: Path,
    legacy_json_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> None:
    global _database

    shutdown_storage(clear_runtime=True)
    destination_existed = database_path.exists()
    database = TrackerDatabase(database_path)
    stored_state = database.load()

    if stored_state is not None:
        _database = database
        apply_stored_state(stored_state)
        sweep_expired_events(now)
        return

    if legacy_json_path is not None and legacy_json_path.exists():
        try:
            legacy_contents = legacy_json_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise _build_migration_error(
                category="source-read",
                source_path=legacy_json_path,
                destination_path=database_path,
                destination_existed=destination_existed,
            ) from exc

        try:
            decoded_state = decode_legacy_tracker_state(json.loads(legacy_contents))
        except (ValueError, TypeError, KeyError) as exc:
            raise _build_migration_error(
                category="invalid-payload",
                source_path=legacy_json_path,
                destination_path=database_path,
                destination_existed=destination_existed,
            ) from exc

        try:
            database.save(decoded_state)
        except Exception as exc:
            raise _build_migration_error(
                category="destination-write",
                source_path=legacy_json_path,
                destination_path=database_path,
                destination_existed=destination_existed,
            ) from exc

        try:
            reloaded_state = database.load()
        except Exception as exc:
            raise _build_migration_error(
                category="destination-readback",
                source_path=legacy_json_path,
                destination_path=database_path,
                destination_existed=destination_existed,
            ) from exc

        if reloaded_state != decoded_state:
            raise _build_migration_error(
                category="verification",
                source_path=legacy_json_path,
                destination_path=database_path,
                destination_existed=destination_existed,
            )

        _database = database
        apply_stored_state(decoded_state)
        sweep_expired_events(now)
        return

    _database = database
    persist_state()


def shutdown_storage(*, clear_runtime: bool = False) -> None:
    global _database
    _database = None

    if clear_runtime:
        _clear_runtime_state()


def persist_state() -> None:
    if _database is None:
        return

    _database.save(
        StoredTrackerState(
            locked_scope_query=_locked_scope_query,
            previous_suspended_cids=tuple(sorted(_previous_suspended_cids)),
            captured_events=tuple(
                StoredUnsuspendEvent(
                    cid=event.cid,
                    nid=event.nid,
                    detected_at=event.detected_at,
                    scope_query=event.scope_query,
                )
                for event in get_captured_events()
            ),
            retention_days=_retention_days,
        )
    )


def persist_snapshot(
    baseline_added: set[int],
    baseline_removed: set[int],
    removed_event_cids: set[int],
    added_events: list[UnsuspendEvent],
) -> None:
    if _database is None or not (
        baseline_added or baseline_removed or removed_event_cids or added_events
    ):
        return

    _database.apply_snapshot(
        baseline_added=baseline_added,
        baseline_removed=baseline_removed,
        removed_event_cids=removed_event_cids,
        added_events=[
            StoredUnsuspendEvent(
                cid=event.cid,
                nid=event.nid,
                detected_at=event.detected_at,
                scope_query=event.scope_query,
            )
            for event in added_events
        ],
    )


def load_state(path: Path) -> None:
    stored_state = TrackerDatabase(path).load()

    if stored_state is None:
        return

    apply_stored_state(stored_state)


def save_state(path: Path) -> None:
    TrackerDatabase(path).save(
        StoredTrackerState(
            locked_scope_query=_locked_scope_query,
            previous_suspended_cids=tuple(sorted(_previous_suspended_cids)),
            captured_events=tuple(
                StoredUnsuspendEvent(
                    cid=event.cid,
                    nid=event.nid,
                    detected_at=event.detected_at,
                    scope_query=event.scope_query,
                )
                for event in get_captured_events()
            ),
            retention_days=_retention_days,
        )
    )


def apply_state(state: dict[str, Any]) -> None:
    apply_stored_state(decode_legacy_tracker_state(state))
    persist_state()


def decode_legacy_tracker_state(payload: object) -> StoredTrackerState:
    if not isinstance(payload, dict):
        raise ValueError("Legacy tracker payload must be an object.")

    version = payload.get("version")
    if version is not None and not _is_integer(version):
        raise ValueError("Legacy tracker version must be an integer.")

    tracking_enabled = payload.get("tracking_enabled")
    if tracking_enabled is not None and not isinstance(tracking_enabled, bool):
        raise ValueError("Legacy tracking_enabled must be a boolean.")

    locked_scope_query = payload.get("locked_scope_query")
    if locked_scope_query is not None and not isinstance(locked_scope_query, str):
        raise ValueError("Legacy locked_scope_query must be a string or null.")

    raw_baseline = payload.get("previous_suspended_cids")
    if not isinstance(raw_baseline, list):
        raise ValueError("Legacy previous_suspended_cids must be an array.")

    baseline: list[int] = []
    seen_card_ids: set[int] = set()
    for raw_cid in raw_baseline:
        cid = _require_integer(raw_cid, "baseline card ID")
        if cid in seen_card_ids:
            raise ValueError("Legacy tracker payload contains duplicate card IDs.")
        seen_card_ids.add(cid)
        baseline.append(cid)

    raw_events = payload.get("captured_events")
    if not isinstance(raw_events, list):
        raise ValueError("Legacy captured_events must be an array.")

    events: list[StoredUnsuspendEvent] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            raise ValueError("Each legacy captured event must be an object.")

        cid = _require_integer(raw_event.get("cid"), "event card ID")
        if cid in seen_card_ids:
            raise ValueError("Legacy tracker payload contains duplicate card IDs.")
        seen_card_ids.add(cid)
        nid = _require_integer(raw_event.get("nid"), "event note ID")
        detected_at_value = raw_event.get("detected_at")
        if not isinstance(detected_at_value, str):
            raise ValueError("Legacy event detected_at must be a timestamp string.")

        try:
            detected_at = datetime.fromisoformat(detected_at_value)
        except ValueError as exc:
            raise ValueError(
                "Legacy event detected_at must be a valid ISO timestamp."
            ) from exc

        scope_query = raw_event.get("scope_query")
        if not isinstance(scope_query, str):
            raise ValueError("Legacy event scope_query must be a string.")

        events.append(
            StoredUnsuspendEvent(
                cid=cid,
                nid=nid,
                detected_at=detected_at,
                scope_query=scope_query,
            )
        )

    retention_days = _require_integer(
        payload.get("retention_days", DEFAULT_RETENTION_DAYS),
        "retention_days",
    )
    if retention_days < 0:
        raise ValueError("Legacy retention_days cannot be negative.")

    return StoredTrackerState(
        locked_scope_query=locked_scope_query,
        previous_suspended_cids=tuple(sorted(baseline)),
        captured_events=tuple(
            sorted(events, key=lambda event: (event.detected_at.isoformat(), event.cid))
        ),
        retention_days=retention_days,
    )


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_integer(value: object, field_name: str) -> int:
    if not _is_integer(value):
        raise ValueError(f"Legacy {field_name} must be an integer.")
    return int(value)


def _build_migration_error(
    category: str,
    source_path: Path,
    destination_path: Path,
    destination_existed: bool,
) -> TrackerMigrationError:
    try:
        quarantine_path = _discard_failed_migration_destination(
            destination_path,
            destination_existed,
        )
    except OSError:
        return TrackerMigrationError(
            category="destination-cleanup",
            source_path=source_path,
            destination_path=destination_path,
        )

    return TrackerMigrationError(
        category=category,
        source_path=source_path,
        destination_path=destination_path,
        quarantine_path=quarantine_path,
    )


def _discard_failed_migration_destination(
    database_path: Path,
    destination_existed: bool,
) -> Optional[Path]:
    if not database_path.exists():
        return None

    if not destination_existed:
        database_path.unlink()
        return None

    quarantine_path = database_path.with_name(f"{database_path.name}.migration-failed")
    suffix = 1
    while quarantine_path.exists():
        quarantine_path = database_path.with_name(
            f"{database_path.name}.migration-failed-{suffix}"
        )
        suffix += 1
    database_path.replace(quarantine_path)
    return quarantine_path


def apply_stored_state(state: StoredTrackerState) -> None:
    global _locked_scope_query, _retention_days

    _clear_runtime_state()
    _locked_scope_query = state.locked_scope_query
    _retention_days = state.retention_days
    set_tracking_enabled(_locked_scope_query is not None)
    _previous_suspended_cids.update(state.previous_suspended_cids)

    for stored_event in state.captured_events:
        event = UnsuspendEvent(
            cid=stored_event.cid,
            nid=stored_event.nid,
            detected_at=stored_event.detected_at,
            scope_query=stored_event.scope_query,
        )
        _captured_events_by_cid[event.cid] = event


def _clear_runtime_state() -> None:
    global _locked_scope_query, _retention_days

    set_tracking_enabled(False)
    _locked_scope_query = None
    _retention_days = DEFAULT_RETENTION_DAYS
    _previous_suspended_cids.clear()
    _captured_events_by_cid.clear()
