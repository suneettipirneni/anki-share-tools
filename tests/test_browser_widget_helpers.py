from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

from share_tools import browser_widget
from share_tools.browser_widget import (
    RETENTION_OPTIONS,
    activate_tracker_profile,
    build_scope_membership_query,
    build_suspended_scope_query,
    deactivate_tracker_profile,
    default_ankipatch_filename,
    default_ankipatch_filename_for_date_range,
    default_share_tag_for_window,
    find_cids_in_scope,
    get_active_profile_key,
    get_refresh_coordinator,
    normalize_scope_query,
    profile_key_for_collection_path,
)
from share_tools.tracker_database import (
    StoredTrackerState,
    StoredUnsuspendEvent,
    TrackerDatabase,
)
from share_tools.unsuspend_tracker import (
    DateRange,
    FreshnessWindow,
    UnsuspendEvent,
    get_captured_events,
    get_locked_scope_query,
    get_retention_days,
    lock_scope,
    record_snapshot,
    set_retention_days,
)


@pytest.fixture(autouse=True)
def reset_active_tracker_profile() -> Iterator[None]:
    deactivate_tracker_profile()
    yield
    deactivate_tracker_profile()


def configure_profile_storage(monkeypatch, tmp_path: Path) -> None:
    user_files_dir = tmp_path / "user_files"
    monkeypatch.setattr(
        "share_tools.browser_widget.USER_FILES_DIR",
        user_files_dir,
    )
    monkeypatch.setattr(
        "share_tools.browser_widget.LEGACY_DATABASE_FILE",
        user_files_dir / "fresh_card_state.sqlite3",
    )
    monkeypatch.setattr(
        "share_tools.browser_widget.LEGACY_STATE_FILE",
        tmp_path / "unsuspend_tracker_state.json",
    )
    monkeypatch.setattr(
        "share_tools.browser_widget.LEGACY_CLAIM_MARKER",
        user_files_dir / "profiles" / ".legacy-state-claimed",
    )


def test_retention_dropdown_options() -> None:
    assert RETENTION_OPTIONS == (
        ("1 month", 30),
        ("1 week", 7),
        ("1 day", 1),
        ("1 year", 365),
        ("Forever", 0),
    )


def test_profile_key_is_deterministic_opaque_and_path_specific(tmp_path) -> None:
    first_path = tmp_path / "Profile A" / "collection.anki2"
    same_path = Path(str(first_path))
    second_path = tmp_path / "Profile B" / "collection.anki2"

    first_key = profile_key_for_collection_path(first_path)

    assert first_key == profile_key_for_collection_path(same_path)
    assert first_key != profile_key_for_collection_path(second_path)
    assert len(first_key) == 64
    assert first_key == first_key.lower()
    assert all(character in "0123456789abcdef" for character in first_key)
    assert "Profile A" not in first_key


def test_profile_activation_is_idempotent_and_isolates_all_state(
    monkeypatch,
    tmp_path,
) -> None:
    configure_profile_storage(monkeypatch, tmp_path)
    profile_a = tmp_path / "Profile A" / "collection.anki2"
    profile_b = tmp_path / "Profile B" / "collection.anki2"
    now = datetime.now().replace(microsecond=0)

    database_a = activate_tracker_profile(profile_a)
    lock_scope("tag:profile-a", [10, 20])
    record_snapshot([20], lambda cid: cid // 10, now=now)
    set_retention_days(0, now=now)
    assert activate_tracker_profile(profile_a) == database_a
    assert get_locked_scope_query() == "tag:profile-a"

    database_b = activate_tracker_profile(profile_b)
    assert database_b != database_a
    assert get_locked_scope_query() is None
    assert get_captured_events() == []
    assert get_retention_days() == 30
    lock_scope("tag:profile-b", [10, 30])
    record_snapshot([30], lambda cid: (cid // 10) + 100, now=now)
    set_retention_days(7, now=now)

    activate_tracker_profile(profile_a)
    assert get_locked_scope_query() == "tag:profile-a"
    assert get_retention_days() == 0
    assert get_captured_events() == [
        UnsuspendEvent(10, 1, now, "tag:profile-a")
    ]
    assert [event.cid for event in record_snapshot([], lambda cid: cid // 10, now)] == [
        20
    ]

    activate_tracker_profile(profile_b)
    assert get_locked_scope_query() == "tag:profile-b"
    assert get_retention_days() == 7
    assert get_captured_events() == [
        UnsuspendEvent(10, 101, now, "tag:profile-b")
    ]
    assert [
        event.cid for event in record_snapshot([], lambda cid: cid // 10, now)
    ] == [30]


def test_refresh_coordinator_is_singleton_per_active_profile(
    monkeypatch,
    tmp_path,
) -> None:
    configure_profile_storage(monkeypatch, tmp_path)
    profile_a = tmp_path / "Profile A" / "collection.anki2"
    profile_b = tmp_path / "Profile B" / "collection.anki2"

    activate_tracker_profile(profile_a)
    first = get_refresh_coordinator()
    assert get_refresh_coordinator() is first

    activate_tracker_profile(profile_b)
    second = get_refresh_coordinator()

    assert second is not first


def test_legacy_database_is_claimed_once_and_source_is_preserved(
    monkeypatch,
    tmp_path,
) -> None:
    configure_profile_storage(monkeypatch, tmp_path)
    legacy_path = tmp_path / "user_files" / "fresh_card_state.sqlite3"
    event = StoredUnsuspendEvent(10, 1, datetime.now(), "tag:legacy")
    legacy_state = StoredTrackerState("tag:legacy", (20,), (event,), 0)
    TrackerDatabase(legacy_path).save(legacy_state)
    profile_a = tmp_path / "Profile A" / "collection.anki2"
    profile_b = tmp_path / "Profile B" / "collection.anki2"

    database_a = activate_tracker_profile(profile_a)
    assert TrackerDatabase(database_a).load() == legacy_state
    assert TrackerDatabase(legacy_path).load() == legacy_state

    database_b = activate_tracker_profile(profile_b)
    assert TrackerDatabase(database_b).load() == StoredTrackerState(
        None,
        (),
        (),
        30,
    )

    deactivate_tracker_profile()
    assert activate_tracker_profile(profile_a) == database_a
    assert get_locked_scope_query() == "tag:legacy"


def test_failed_legacy_database_claim_preserves_recoverable_source(
    monkeypatch,
    tmp_path,
) -> None:
    configure_profile_storage(monkeypatch, tmp_path)
    legacy_path = tmp_path / "user_files" / "fresh_card_state.sqlite3"
    legacy_state = StoredTrackerState("tag:legacy", (20,), (), 30)
    TrackerDatabase(legacy_path).save(legacy_state)
    real_save = TrackerDatabase.save

    def fail_destination_save(
        database: TrackerDatabase,
        state: StoredTrackerState,
    ) -> None:
        if database.path != legacy_path:
            raise OSError("simulated destination failure")
        real_save(database, state)

    monkeypatch.setattr(TrackerDatabase, "save", fail_destination_save)
    profile_path = tmp_path / "Profile A" / "collection.anki2"

    with pytest.raises(OSError, match="simulated destination failure"):
        activate_tracker_profile(profile_path)

    assert TrackerDatabase(legacy_path).load() == legacy_state
    assert not (
        tmp_path
        / "user_files"
        / "profiles"
        / profile_key_for_collection_path(profile_path)
        / "fresh_card_state.sqlite3"
    ).exists()
    assert get_active_profile_key() is None


def test_normalize_scope_query_removes_suspended_literals() -> None:
    assert (
        normalize_scope_query("tag:class::cardiology -is:suspended is:suspended")
        == "tag:class::cardiology"
    )


def test_build_suspended_scope_query_wraps_non_empty_scope() -> None:
    assert (
        build_suspended_scope_query("tag:class::cardiology")
        == "(tag:class::cardiology) is:suspended"
    )


def test_build_suspended_scope_query_handles_empty_scope() -> None:
    assert build_suspended_scope_query("") == "is:suspended"


def test_build_scope_membership_query_preserves_nonempty_scope() -> None:
    assert (
        build_scope_membership_query("  tag:class::cardiology  ")
        == "tag:class::cardiology"
    )


def test_build_scope_membership_query_uses_empty_all_cards_search() -> None:
    assert build_scope_membership_query("") == ""


def test_find_cids_in_empty_scope_uses_supported_all_cards_query(
    monkeypatch,
) -> None:
    queries: list[str] = []

    def find_cards(query: str) -> list[int]:
        queries.append(query)
        return [30, 10]

    monkeypatch.setattr(
        browser_widget,
        "mw",
        SimpleNamespace(col=SimpleNamespace(find_cards=find_cards)),
    )

    assert find_cids_in_scope("") == [10, 30]
    assert queries == [""]


def test_default_share_tag_for_today(monkeypatch) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 6, 15, 10)

    monkeypatch.setattr("share_tools.browser_widget.datetime", FrozenDateTime)

    assert (
        default_share_tag_for_window(FreshnessWindow.TODAY)
        == "share_unsuspended::2026_06_15"
    )


def test_default_share_tag_for_week(monkeypatch) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 6, 17, 10)

    monkeypatch.setattr("share_tools.browser_widget.datetime", FrozenDateTime)

    assert (
        default_share_tag_for_window(FreshnessWindow.THIS_WEEK)
        == "share_unsuspended::week_2026_06_15"
    )


def test_default_ankipatch_filenames_for_presets_are_preserved(monkeypatch) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 6, 17, 10)

    monkeypatch.setattr("share_tools.browser_widget.datetime", FrozenDateTime)

    assert (
        default_ankipatch_filename(FreshnessWindow.TODAY)
        == "fresh_unsuspends_2026-06-17.ankipatch"
    )
    assert (
        default_ankipatch_filename(FreshnessWindow.THIS_WEEK)
        == "fresh_unsuspends_week_2026-06-15.ankipatch"
    )


def test_default_ankipatch_filename_for_custom_date_range() -> None:
    assert (
        default_ankipatch_filename_for_date_range(
            DateRange(start=date(2026, 6, 13), end=date(2026, 6, 15))
        )
        == "fresh_unsuspends_2026-06-13_to_2026-06-15.ankipatch"
    )


def test_default_ankipatch_filename_for_single_day_custom_range() -> None:
    assert (
        default_ankipatch_filename_for_date_range(
            DateRange(start=date(2026, 6, 13), end=date(2026, 6, 13))
        )
        == "fresh_unsuspends_2026-06-13_to_2026-06-13.ankipatch"
    )
