from anki.collection import OpChanges
from share_tools.browser_actions import (
    on_operation_did_execute,
    on_profile_did_open,
    on_profile_will_close,
)


def test_card_operation_refreshes_tracker_after_completion(monkeypatch) -> None:
    refresh_reasons: list[str] = []

    def request_tracker_refresh(reason: str) -> None:
        refresh_reasons.append(reason)

    monkeypatch.setattr(
        "share_tools.browser_actions.request_tracker_refresh",
        request_tracker_refresh,
    )

    on_operation_did_execute(
        OpChanges(card=True),
        None,
    )

    assert refresh_reasons == ["operation"]


def test_unrelated_operation_does_not_refresh_tracker(monkeypatch) -> None:
    refresh_reasons: list[str] = []

    def request_tracker_refresh(reason: str) -> None:
        refresh_reasons.append(reason)

    monkeypatch.setattr(
        "share_tools.browser_actions.request_tracker_refresh",
        request_tracker_refresh,
    )

    on_operation_did_execute(
        OpChanges(),
        None,
    )

    assert refresh_reasons == []


def test_study_queue_operation_requests_tracker_refresh(monkeypatch) -> None:
    refresh_reasons: list[str] = []
    monkeypatch.setattr(
        "share_tools.browser_actions.request_tracker_refresh",
        lambda reason: refresh_reasons.append(reason),
    )

    on_operation_did_execute(OpChanges(study_queues=True), None)

    assert refresh_reasons == ["operation"]


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
