from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Optional

import pytest
from anki.errors import NotFoundError

from share_tools import browser_widget
from share_tools.tracker_database import (
    StoredTrackerState,
    TrackerDatabase,
    TrackerMigrationError,
)
from share_tools.unsuspend_tracker import (
    DateRange,
    FreshnessWindow,
    UnsuspendEvent,
    clear_all,
    clear_captured,
    count_for_window,
    count_for_date_range,
    date_range_for_window,
    decode_legacy_tracker_state,
    get_captured_cids_for_window,
    get_captured_cids_for_date_range,
    get_captured_events,
    get_captured_events_for_date_range,
    get_captured_events_for_window,
    get_captured_nids_for_date_range,
    get_captured_nids_for_window,
    get_locked_scope_query,
    get_retention_days,
    initialize_storage,
    is_tracking_enabled,
    load_state,
    lock_scope,
    record_snapshot,
    remove_captured_cids,
    save_state,
    set_retention_days,
    apply_state,
    shutdown_storage,
    sync_baseline_without_capturing,
)


@pytest.fixture(autouse=True)
def reset_tracker() -> None:
    shutdown_storage()
    clear_all()


def cid_to_nid(cid: int) -> int:
    return cid // 10


def legacy_tracker_payload() -> dict[str, object]:
    return {
        "version": 1,
        "tracking_enabled": True,
        "locked_scope_query": "tag:class::cardiology",
        "previous_suspended_cids": [20],
        "captured_events": [
            {
                "cid": 10,
                "nid": 1,
                "detected_at": "2026-06-15T12:00:00",
                "scope_query": "tag:class::cardiology",
            }
        ],
    }


def test_browser_card_resolver_returns_none_only_for_missing_cards(
    monkeypatch,
) -> None:
    def missing_card(_cid: int) -> None:
        raise NotFoundError("missing", None, None, None)

    monkeypatch.setattr(
        browser_widget,
        "mw",
        SimpleNamespace(col=SimpleNamespace(get_card=missing_card)),
    )

    assert browser_widget.cid_to_nid(10) is None


def test_browser_card_resolver_propagates_unexpected_errors(monkeypatch) -> None:
    def broken_card_lookup(_cid: int) -> None:
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(
        browser_widget,
        "mw",
        SimpleNamespace(col=SimpleNamespace(get_card=broken_card_lookup)),
    )

    with pytest.raises(RuntimeError, match="backend unavailable"):
        browser_widget.cid_to_nid(10)


def test_lock_scope_stores_baseline_enables_tracking_and_clears_events() -> None:
    lock_scope("tag:old", [10])
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 15, 9))

    lock_scope("tag:new", [20, 30])

    assert is_tracking_enabled()
    assert get_locked_scope_query() == "tag:new"
    assert get_captured_events() == []


def test_record_snapshot_captures_cards_removed_from_suspended_set() -> None:
    now = datetime(2026, 6, 15, 10)

    lock_scope("tag:class::cardiology", [10, 20, 30])
    events = record_snapshot([10, 30], cid_to_nid, now=now)

    assert events == [
        UnsuspendEvent(
            cid=20,
            nid=2,
            detected_at=now,
            scope_query="tag:class::cardiology",
        )
    ]


def test_deleted_baseline_card_is_pruned_without_creating_event() -> None:
    resolver_calls: list[int] = []

    def resolve_missing_card(cid: int) -> None:
        resolver_calls.append(cid)
        return None

    lock_scope("tag:class::cardiology", [10])

    assert record_snapshot([], resolve_missing_card) == []
    assert record_snapshot([], resolve_missing_card) == []
    assert resolver_calls == [10]
    assert get_captured_events() == []


def test_deleted_and_valid_departures_are_partitioned_in_one_snapshot() -> None:
    def resolve_card(cid: int) -> Optional[int]:
        return None if cid == 10 else cid // 10

    now = datetime(2026, 6, 15, 10)
    lock_scope("tag:class::cardiology", [10, 20])

    assert record_snapshot([], resolve_card, now=now) == [
        UnsuspendEvent(
            cid=20,
            nid=2,
            detected_at=now,
            scope_query="tag:class::cardiology",
        )
    ]
    assert get_captured_events() == [
        UnsuspendEvent(
            cid=20,
            nid=2,
            detected_at=now,
            scope_query="tag:class::cardiology",
        )
    ]


def test_deleted_baseline_card_is_not_retried_after_restart(tmp_path) -> None:
    database_path = tmp_path / "tracker.sqlite3"
    initialize_storage(database_path)
    lock_scope("tag:class::cardiology", [10])

    assert record_snapshot([], lambda _cid: None) == []
    shutdown_storage()
    clear_all()
    initialize_storage(database_path)

    def unexpected_resolver_call(_cid: int) -> None:
        raise AssertionError("deleted card was retried")

    assert record_snapshot([], unexpected_resolver_call) == []


def test_resolver_failure_does_not_partially_advance_snapshot(tmp_path) -> None:
    database_path = tmp_path / "tracker.sqlite3"
    now = datetime(2026, 6, 15, 10)
    initialize_storage(database_path)
    lock_scope("tag:class::cardiology", [10, 20])

    def fail_after_first_resolution(cid: int) -> int:
        if cid == 20:
            raise RuntimeError("backend unavailable")
        return cid // 10

    with pytest.raises(RuntimeError, match="backend unavailable"):
        record_snapshot([], fail_after_first_resolution, now=now)

    assert get_captured_events() == []
    shutdown_storage()
    clear_all()
    initialize_storage(database_path)
    assert record_snapshot([], cid_to_nid, now=now) == [
        UnsuspendEvent(10, 1, now, "tag:class::cardiology"),
        UnsuspendEvent(20, 2, now, "tag:class::cardiology"),
    ]


def test_snapshot_classifies_suspension_scope_and_entry_independently() -> None:
    now = datetime(2026, 6, 15, 10)
    resolved_cids: list[int] = []

    def resolve_card(cid: int) -> int:
        resolved_cids.append(cid)
        return cid // 10

    lock_scope("tag:class::cardiology", [10, 20, 30])

    assert record_snapshot(
        current_in_scope_cids=[10, 20, 40],
        current_suspended_cids=[10, 40],
        cid_to_nid=resolve_card,
        now=now,
    ) == [
        UnsuspendEvent(
            cid=20,
            nid=2,
            detected_at=now,
            scope_query="tag:class::cardiology",
        )
    ]
    assert resolved_cids == [20]

    assert record_snapshot(
        current_in_scope_cids=[10, 40],
        current_suspended_cids=[10, 40],
        cid_to_nid=resolve_card,
        now=now,
    ) == []
    assert resolved_cids == [20]

    later = datetime(2026, 6, 15, 11)
    assert record_snapshot(
        current_in_scope_cids=[10, 40],
        current_suspended_cids=[40],
        cid_to_nid=resolve_card,
        now=later,
    ) == [
        UnsuspendEvent(
            cid=10,
            nid=1,
            detected_at=later,
            scope_query="tag:class::cardiology",
        )
    ]
    assert [event.cid for event in get_captured_events()] == [20, 10]


def test_scope_departure_with_missing_card_is_pruned_without_resolution() -> None:
    lock_scope("tag:class::cardiology", [10])

    def unexpected_resolver_call(_cid: int) -> None:
        raise AssertionError("out-of-scope card should not be resolved")

    assert record_snapshot(
        current_in_scope_cids=[],
        current_suspended_cids=[],
        cid_to_nid=unexpected_resolver_call,
    ) == []


def test_snapshot_database_failure_leaves_runtime_baseline_unchanged(
    monkeypatch,
    tmp_path,
) -> None:
    database_path = tmp_path / "tracker.sqlite3"
    initialize_storage(database_path)
    lock_scope("tag:class::cardiology", [10, 20])

    def fail_snapshot(*_args, **_kwargs) -> None:
        raise OSError("database unavailable")

    monkeypatch.setattr(
        "share_tools.unsuspend_tracker._database.apply_snapshot",
        fail_snapshot,
    )

    with pytest.raises(OSError, match="database unavailable"):
        record_snapshot(
            current_in_scope_cids=[10, 20],
            current_suspended_cids=[20],
            cid_to_nid=cid_to_nid,
        )

    shutdown_storage()
    clear_all()
    initialize_storage(database_path)
    now = datetime(2026, 6, 15, 10)
    assert record_snapshot(
        current_in_scope_cids=[10, 20],
        current_suspended_cids=[20],
        cid_to_nid=cid_to_nid,
        now=now,
    ) == [
        UnsuspendEvent(10, 1, now, "tag:class::cardiology")
    ]


def test_duplicate_snapshots_do_not_duplicate_events() -> None:
    lock_scope("tag:class::cardiology", [10, 20])

    record_snapshot([10], cid_to_nid, now=datetime(2026, 6, 15, 10))
    record_snapshot([10], cid_to_nid, now=datetime(2026, 6, 15, 11))

    assert get_captured_cids_for_window(
        FreshnessWindow.TODAY,
        now=datetime(2026, 6, 15, 12),
    ) == [20]


def test_consecutive_unsuspend_snapshots_accumulate_events() -> None:
    lock_scope("tag:class::cardiology", [10, 20, 30])

    first_events = record_snapshot(
        [20, 30],
        cid_to_nid,
        now=datetime(2026, 6, 15, 10),
    )
    second_events = record_snapshot(
        [30],
        cid_to_nid,
        now=datetime(2026, 6, 15, 11),
    )

    assert [event.cid for event in first_events] == [10]
    assert [event.cid for event in second_events] == [20]
    assert get_captured_cids_for_window(
        FreshnessWindow.TODAY,
        now=datetime(2026, 6, 15, 12),
    ) == [10, 20]


def test_resuspended_cards_are_removed_from_captured_events() -> None:
    lock_scope("tag:class::cardiology", [10, 20])
    record_snapshot([10], cid_to_nid, now=datetime(2026, 6, 15, 10))

    record_snapshot([10, 20], cid_to_nid, now=datetime(2026, 6, 15, 11))

    assert get_captured_events() == []


def test_sync_baseline_without_capturing_ignores_patch_unsuspends() -> None:
    lock_scope("tag:class::cardiology", [10, 20, 30])

    sync_baseline_without_capturing([10])

    assert get_captured_events() == []
    assert record_snapshot([10], cid_to_nid, now=datetime(2026, 6, 15, 10)) == []


def test_sync_baseline_without_capturing_removes_resuspended_captured_cards() -> None:
    lock_scope("tag:class::cardiology", [10, 20])
    record_snapshot([10], cid_to_nid, now=datetime(2026, 6, 15, 10))

    sync_baseline_without_capturing([10, 20])

    assert get_captured_events() == []


def test_remove_captured_cids_removes_matching_events_only() -> None:
    lock_scope("tag:class::cardiology", [10, 20, 30])
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 15, 10))

    assert remove_captured_cids([20, 40]) == 1
    assert get_captured_cids_for_window(
        FreshnessWindow.TODAY,
        now=datetime(2026, 6, 15, 12),
    ) == [10, 30]


def test_captured_cids_are_sorted() -> None:
    lock_scope("deck:current", [30, 10, 20])
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 15, 9))

    assert get_captured_cids_for_window(
        FreshnessWindow.TODAY,
        now=datetime(2026, 6, 15, 12),
    ) == [10, 20, 30]


def test_captured_nids_are_sorted_and_deduped() -> None:
    lock_scope("deck:current", [30, 31, 10])
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 15, 9))

    assert get_captured_nids_for_window(
        FreshnessWindow.TODAY,
        now=datetime(2026, 6, 15, 12),
    ) == [1, 3]


def test_today_window_includes_only_same_date() -> None:
    lock_scope("deck:current", [10, 20])
    record_snapshot([20], cid_to_nid, now=datetime(2026, 6, 15, 23, 59))
    lock_scope("deck:current", [30])
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 16, 0, 1))

    assert get_captured_cids_for_window(
        FreshnessWindow.TODAY,
        now=datetime(2026, 6, 16, 12),
    ) == [30]


def test_this_week_window_includes_monday_start_through_now() -> None:
    lock_scope("deck:current", [10, 20, 30])
    record_snapshot([20, 30], cid_to_nid, now=datetime(2026, 6, 15, 0, 0))
    record_snapshot([30], cid_to_nid, now=datetime(2026, 6, 17, 12, 0))

    assert get_captured_cids_for_window(
        FreshnessWindow.THIS_WEEK,
        now=datetime(2026, 6, 17, 13),
    ) == [10, 20]


def test_this_week_window_excludes_events_before_current_week() -> None:
    lock_scope("deck:current", [10, 20])
    record_snapshot([20], cid_to_nid, now=datetime(2026, 6, 14, 23, 59))
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 15, 0, 0))

    assert get_captured_cids_for_window(
        FreshnessWindow.THIS_WEEK,
        now=datetime(2026, 6, 15, 12),
    ) == [20]


def test_date_range_includes_entire_boundary_dates_and_excludes_neighbors() -> None:
    lock_scope("deck:current", [10, 20, 30, 40])
    record_snapshot(
        [20, 30, 40],
        cid_to_nid,
        now=datetime(2026, 6, 14, 23, 59, 59),
    )
    record_snapshot(
        [30, 40],
        cid_to_nid,
        now=datetime(2026, 6, 15, 0, 0),
    )
    record_snapshot(
        [40],
        cid_to_nid,
        now=datetime(2026, 6, 17, 23, 59, 59),
    )
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 18, 0, 0))

    selected_range = DateRange(start=date(2026, 6, 15), end=date(2026, 6, 17))

    assert [
        event.cid for event in get_captured_events_for_date_range(selected_range)
    ] == [20, 30]
    assert get_captured_cids_for_date_range(selected_range) == [20, 30]
    assert get_captured_nids_for_date_range(selected_range) == [2, 3]
    assert count_for_date_range(selected_range) == 2


def test_single_day_date_range_includes_only_that_date() -> None:
    lock_scope("deck:current", [10, 20])
    record_snapshot([20], cid_to_nid, now=datetime(2026, 6, 15, 23, 59))
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 16, 0, 0))

    assert get_captured_cids_for_date_range(
        DateRange(start=date(2026, 6, 15), end=date(2026, 6, 15))
    ) == [10]


def test_date_range_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="start must be on or before"):
        DateRange(start=date(2026, 6, 16), end=date(2026, 6, 15))


def test_freshness_windows_resolve_to_date_ranges() -> None:
    now = datetime(2026, 6, 17, 13)

    assert date_range_for_window(FreshnessWindow.TODAY, now) == DateRange(
        start=date(2026, 6, 17),
        end=date(2026, 6, 17),
    )
    assert date_range_for_window(FreshnessWindow.THIS_WEEK, now) == DateRange(
        start=date(2026, 6, 15),
        end=date(2026, 6, 17),
    )


def test_count_for_window_returns_expected_value() -> None:
    lock_scope("deck:current", [10, 20])
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 15, 12))

    assert (
        count_for_window(
            FreshnessWindow.TODAY,
            now=datetime(2026, 6, 15, 13),
        )
        == 2
    )


def test_default_retention_is_thirty_days() -> None:
    assert get_retention_days() == 30


def test_setting_retention_sweeps_dates_before_inclusive_cutoff() -> None:
    lock_scope("deck:current", [10, 20, 30])
    record_snapshot([20, 30], cid_to_nid, now=datetime(2026, 6, 1, 23, 59))
    record_snapshot([30], cid_to_nid, now=datetime(2026, 6, 2, 0, 0))
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 30, 12))

    removed_count = set_retention_days(29, now=datetime(2026, 6, 30, 12))

    assert removed_count == 1
    assert get_retention_days() == 29
    assert [event.cid for event in get_captured_events()] == [20, 30]


def test_forever_retention_does_not_sweep_old_events() -> None:
    lock_scope("deck:current", [10])
    record_snapshot([], cid_to_nid, now=datetime(2020, 1, 1, 12))

    assert set_retention_days(0, now=datetime(2026, 6, 30, 12)) == 0
    assert get_captured_cids_for_date_range(
        DateRange(start=date(2020, 1, 1), end=date(2020, 1, 1))
    ) == [10]


def test_negative_retention_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        set_retention_days(-1)


def test_clear_captured_preserves_scope_and_tracking() -> None:
    lock_scope("deck:current", [10])
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 15, 12))

    clear_captured()

    assert is_tracking_enabled()
    assert get_locked_scope_query() == "deck:current"
    assert get_captured_events() == []


def test_clear_all_clears_tracker_state() -> None:
    lock_scope("deck:current", [10])
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 15, 12))

    clear_all()

    assert not is_tracking_enabled()
    assert get_locked_scope_query() is None
    assert get_captured_events() == []


def test_save_and_load_state_round_trips_tracker_state(tmp_path) -> None:
    state_path = tmp_path / "tracker.sqlite3"
    detected_at = datetime(2026, 6, 15, 12)

    lock_scope("deck:current", [10, 20])
    record_snapshot([20], cid_to_nid, now=detected_at)
    save_state(state_path)
    clear_all()

    load_state(state_path)

    assert is_tracking_enabled()
    assert get_locked_scope_query() == "deck:current"
    assert get_captured_events() == [
        UnsuspendEvent(
            cid=10,
            nid=1,
            detected_at=detected_at,
            scope_query="deck:current",
        )
    ]
    assert record_snapshot([], cid_to_nid, now=datetime(2026, 6, 15, 13)) == [
        UnsuspendEvent(
            cid=20,
            nid=2,
            detected_at=datetime(2026, 6, 15, 13),
            scope_query="deck:current",
        )
    ]


def test_initialized_storage_persists_mutations_automatically(tmp_path) -> None:
    database_path = tmp_path / "tracker.sqlite3"
    detected_at = datetime(2026, 6, 15, 12)

    initialize_storage(database_path, now=datetime(2026, 6, 15, 12))
    lock_scope("deck:current", [10, 20])
    record_snapshot([20], cid_to_nid, now=detected_at)
    shutdown_storage()
    clear_all()
    initialize_storage(database_path, now=datetime(2026, 6, 15, 12))

    assert get_locked_scope_query() == "deck:current"
    assert get_captured_events() == [
        UnsuspendEvent(
            cid=10,
            nid=1,
            detected_at=detected_at,
            scope_query="deck:current",
        )
    ]

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert connection.execute("SELECT retention_days FROM tracker").fetchone() == (
            30,
        )
        assert connection.execute(
            "SELECT cid FROM suspended_baseline ORDER BY cid"
        ).fetchall() == [(20,)]
        assert connection.execute(
            "SELECT cid, nid FROM fresh_unsuspends"
        ).fetchall() == [(10, 1)]


def test_initialize_storage_migrates_legacy_json_state(tmp_path) -> None:
    database_path = tmp_path / "tracker.sqlite3"
    legacy_path = tmp_path / "tracker.json"
    legacy_path.write_text(json.dumps(legacy_tracker_payload()), encoding="utf-8")
    original_bytes = legacy_path.read_bytes()

    initialize_storage(
        database_path,
        legacy_path,
        now=datetime(2026, 6, 15, 12),
    )
    shutdown_storage()
    clear_all()
    initialize_storage(
        database_path,
        legacy_path,
        now=datetime(2026, 6, 15, 12),
    )

    assert get_locked_scope_query() == "tag:class::cardiology"
    assert get_captured_events() == [
        UnsuspendEvent(
            cid=10,
            nid=1,
            detected_at=datetime(2026, 6, 15, 12),
            scope_query="tag:class::cardiology",
        )
    ]
    assert get_retention_days() == 30
    assert legacy_path.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"previous_suspended_cids": [], "captured_events": "invalid"},
        {
            "previous_suspended_cids": ["10"],
            "captured_events": [],
        },
        {
            "previous_suspended_cids": [10, 10],
            "captured_events": [],
        },
        {
            "previous_suspended_cids": [10],
            "captured_events": [
                {
                    "cid": 10,
                    "nid": 1,
                    "detected_at": "2026-06-15T12:00:00",
                    "scope_query": "deck:current",
                }
            ],
        },
        {
            "previous_suspended_cids": [],
            "captured_events": [
                {
                    "cid": 10,
                    "nid": 1,
                    "detected_at": "not-a-timestamp",
                    "scope_query": "deck:current",
                }
            ],
        },
        {
            "previous_suspended_cids": [],
            "captured_events": [],
            "retention_days": -1,
        },
        {
            "previous_suspended_cids": [],
            "captured_events": [],
            "retention_days": True,
        },
    ],
)
def test_legacy_decoder_rejects_invalid_payloads(payload) -> None:
    with pytest.raises(ValueError):
        decode_legacy_tracker_state(payload)


def test_legacy_decoder_defaults_missing_retention_to_thirty_days() -> None:
    state = decode_legacy_tracker_state(legacy_tracker_payload())

    assert state.retention_days == 30


def test_malformed_legacy_json_raises_typed_error_without_source_contents(
    tmp_path,
) -> None:
    database_path = tmp_path / "tracker.sqlite3"
    legacy_path = tmp_path / "tracker.json"
    secret_contents = '{"private-token":"do-not-echo"'
    legacy_path.write_text(secret_contents, encoding="utf-8")
    original_bytes = legacy_path.read_bytes()

    with pytest.raises(TrackerMigrationError) as error:
        initialize_storage(database_path, legacy_path)

    assert error.value.category == "invalid-payload"
    assert error.value.source_path == legacy_path
    assert error.value.destination_path == database_path
    assert secret_contents not in str(error.value)
    assert legacy_path.read_bytes() == original_bytes
    assert not database_path.exists()


def test_legacy_source_read_failure_is_typed_and_retryable(
    monkeypatch,
    tmp_path,
) -> None:
    database_path = tmp_path / "tracker.sqlite3"
    legacy_path = tmp_path / "tracker.json"
    legacy_path.write_text(json.dumps(legacy_tracker_payload()), encoding="utf-8")
    original_bytes = legacy_path.read_bytes()
    real_read_text = Path.read_text

    def fail_legacy_read(path: Path, *args, **kwargs) -> str:
        if path == legacy_path:
            raise OSError("simulated source failure")
        return real_read_text(path, *args, **kwargs)

    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(Path, "read_text", fail_legacy_read)
        with pytest.raises(TrackerMigrationError) as error:
            initialize_storage(database_path, legacy_path)

    assert error.value.category == "source-read"
    assert not database_path.exists()
    assert legacy_path.read_bytes() == original_bytes

    initialize_storage(database_path, legacy_path)
    assert get_locked_scope_query() == "tag:class::cardiology"


def test_legacy_destination_write_failure_is_retryable(
    monkeypatch,
    tmp_path,
) -> None:
    database_path = tmp_path / "tracker.sqlite3"
    legacy_path = tmp_path / "tracker.json"
    legacy_path.write_text(json.dumps(legacy_tracker_payload()), encoding="utf-8")
    original_bytes = legacy_path.read_bytes()

    def fail_save(
        _database: TrackerDatabase,
        _state: StoredTrackerState,
    ) -> None:
        raise OSError("simulated write failure")

    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(TrackerDatabase, "save", fail_save)
        with pytest.raises(TrackerMigrationError) as error:
            initialize_storage(database_path, legacy_path)

    assert error.value.category == "destination-write"
    assert not database_path.exists()
    assert legacy_path.read_bytes() == original_bytes

    initialize_storage(database_path, legacy_path)
    assert get_locked_scope_query() == "tag:class::cardiology"


def test_legacy_destination_readback_mismatch_is_retryable(
    monkeypatch,
    tmp_path,
) -> None:
    database_path = tmp_path / "tracker.sqlite3"
    legacy_path = tmp_path / "tracker.json"
    legacy_path.write_text(json.dumps(legacy_tracker_payload()), encoding="utf-8")
    original_bytes = legacy_path.read_bytes()
    real_load = TrackerDatabase.load
    load_count = 0

    def mismatch_on_readback(database: TrackerDatabase):
        nonlocal load_count
        load_count += 1
        state = real_load(database)
        if load_count == 2:
            return StoredTrackerState(None, (), (), 30)
        return state

    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(TrackerDatabase, "load", mismatch_on_readback)
        with pytest.raises(TrackerMigrationError) as error:
            initialize_storage(database_path, legacy_path)

    assert error.value.category == "verification"
    assert not database_path.exists()
    assert legacy_path.read_bytes() == original_bytes

    initialize_storage(database_path, legacy_path)
    assert get_locked_scope_query() == "tag:class::cardiology"


def test_failed_migration_quarantines_preexisting_empty_destination(
    tmp_path,
) -> None:
    database_path = tmp_path / "tracker.sqlite3"
    legacy_path = tmp_path / "tracker.json"
    TrackerDatabase(database_path).load()
    legacy_path.write_text("invalid json", encoding="utf-8")
    original_bytes = legacy_path.read_bytes()

    with pytest.raises(TrackerMigrationError) as error:
        initialize_storage(database_path, legacy_path)

    assert error.value.quarantine_path is not None
    assert error.value.quarantine_path.exists()
    assert not database_path.exists()
    assert legacy_path.read_bytes() == original_bytes


def test_retention_setting_and_sweep_persist_across_restarts(tmp_path) -> None:
    database_path = tmp_path / "tracker.sqlite3"

    initialize_storage(database_path, now=datetime(2026, 6, 30, 12))
    set_retention_days(0, now=datetime(2026, 6, 30, 12))
    lock_scope("deck:current", [10, 20])
    record_snapshot([20], cid_to_nid, now=datetime(2026, 5, 1, 12))
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 30, 12))

    assert set_retention_days(7, now=datetime(2026, 6, 30, 12)) == 1
    shutdown_storage()
    clear_all()
    initialize_storage(database_path, now=datetime(2026, 6, 30, 12))

    assert get_retention_days() == 7
    assert [event.cid for event in get_captured_events()] == [20]


def test_version_one_database_migrates_to_thirty_day_retention(tmp_path) -> None:
    database_path = tmp_path / "tracker.sqlite3"

    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE tracker (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                locked_scope_query TEXT
            );
            CREATE TABLE suspended_baseline (cid INTEGER PRIMARY KEY);
            CREATE TABLE fresh_unsuspends (
                cid INTEGER PRIMARY KEY,
                nid INTEGER NOT NULL,
                detected_at TEXT NOT NULL,
                scope_query TEXT NOT NULL
            );
            INSERT INTO tracker(singleton, locked_scope_query)
            VALUES (1, 'deck:current');
            INSERT INTO fresh_unsuspends(cid, nid, detected_at, scope_query)
            VALUES (10, 1, '2026-06-01T12:00:00', 'deck:current');
            PRAGMA user_version = 1;
            """
        )

    initialize_storage(database_path, now=datetime(2026, 6, 15, 12))

    assert get_retention_days() == 30
    assert [event.cid for event in get_captured_events()] == [10]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert connection.execute("SELECT retention_days FROM tracker").fetchone() == (
            30,
        )


def test_loaded_locked_scope_is_tracking_even_if_old_state_was_disabled() -> None:
    apply_state(
        {
            "version": 1,
            "tracking_enabled": False,
            "locked_scope_query": "tag:class::cardiology",
            "previous_suspended_cids": [10],
            "captured_events": [],
        }
    )

    assert is_tracking_enabled()
    assert record_snapshot([], cid_to_nid, now=datetime(2026, 6, 15, 13)) == [
        UnsuspendEvent(
            cid=10,
            nid=1,
            detected_at=datetime(2026, 6, 15, 13),
            scope_query="tag:class::cardiology",
        )
    ]


def test_today_window_excludes_yesterday() -> None:
    lock_scope("deck:current", [10])
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 14, 12))

    assert (
        get_captured_events_for_window(
            FreshnessWindow.TODAY,
            now=datetime(2026, 6, 15, 12),
        )
        == []
    )
