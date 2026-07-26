from anki.collection import OpChanges
from share_tools.browser_actions import (
    on_operation_did_execute,
    on_profile_did_open,
    on_profile_will_close,
)


def test_card_operation_refreshes_tracker_after_completion(monkeypatch) -> None:
    refresh_calls = 0

    def refresh_tracker_widgets() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    monkeypatch.setattr(
        "share_tools.browser_actions.refresh_tracker_widgets",
        refresh_tracker_widgets,
    )

    on_operation_did_execute(
        OpChanges(card=True),
        None,
    )

    assert refresh_calls == 1


def test_unrelated_operation_does_not_refresh_tracker(monkeypatch) -> None:
    refresh_calls = 0

    def refresh_tracker_widgets() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    monkeypatch.setattr(
        "share_tools.browser_actions.refresh_tracker_widgets",
        refresh_tracker_widgets,
    )

    on_operation_did_execute(
        OpChanges(),
        None,
    )

    assert refresh_calls == 0


def test_profile_hooks_activate_then_deactivate_tracker(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "share_tools.browser_actions.ensure_active_tracker_profile",
        lambda: calls.append("open"),
    )
    monkeypatch.setattr(
        "share_tools.browser_actions.deactivate_tracker_profile",
        lambda: calls.append("close"),
    )

    on_profile_did_open()
    on_profile_will_close()

    assert calls == ["open", "close"]
