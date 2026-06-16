from datetime import datetime

from share_tools.browser_widget import (
    build_suspended_scope_query,
    default_share_tag_for_window,
    normalize_scope_query,
)
from share_tools.unsuspend_tracker import FreshnessWindow


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
