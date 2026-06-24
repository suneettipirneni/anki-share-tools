from datetime import datetime

import pytest

from share_tools.unsuspend_tracker import (
    FreshnessWindow,
    UnsuspendEvent,
    clear_all,
    clear_captured,
    count_for_window,
    get_captured_cids_for_window,
    get_captured_events,
    get_captured_events_for_window,
    get_captured_nids_for_window,
    get_locked_scope_query,
    is_tracking_enabled,
    load_state,
    lock_scope,
    record_snapshot,
    remove_captured_cids,
    save_state,
    apply_state,
    sync_baseline_without_capturing,
)


@pytest.fixture(autouse=True)
def reset_tracker() -> None:
    clear_all()


def cid_to_nid(cid: int) -> int:
    return cid // 10


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


def test_duplicate_snapshots_do_not_duplicate_events() -> None:
    lock_scope("tag:class::cardiology", [10, 20])

    record_snapshot([10], cid_to_nid, now=datetime(2026, 6, 15, 10))
    record_snapshot([10], cid_to_nid, now=datetime(2026, 6, 15, 11))

    assert get_captured_cids_for_window(
        FreshnessWindow.TODAY,
        now=datetime(2026, 6, 15, 12),
    ) == [20]


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


def test_count_for_window_returns_expected_value() -> None:
    lock_scope("deck:current", [10, 20])
    record_snapshot([], cid_to_nid, now=datetime(2026, 6, 15, 12))

    assert count_for_window(
        FreshnessWindow.TODAY,
        now=datetime(2026, 6, 15, 13),
    ) == 2


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
    state_path = tmp_path / "tracker.json"
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
