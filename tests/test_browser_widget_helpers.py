from datetime import date, datetime

from share_tools.browser_widget import (
    RETENTION_OPTIONS,
    build_suspended_scope_query,
    default_ankipatch_filename,
    default_ankipatch_filename_for_date_range,
    default_share_tag_for_window,
    normalize_scope_query,
)
from share_tools.unsuspend_tracker import DateRange, FreshnessWindow


def test_retention_dropdown_options() -> None:
    assert RETENTION_OPTIONS == (
        ("1 month", 30),
        ("1 week", 7),
        ("1 day", 1),
        ("1 year", 365),
        ("Forever", 0),
    )


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
