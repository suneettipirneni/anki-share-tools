from dataclasses import FrozenInstanceError
from datetime import datetime
import os
from typing import cast

import pytest

from aqt.browser import Browser
from aqt.qt import QApplication, QMainWindow

from share_tools import browser_widget
from share_tools.browser_widget import (
    DOCK_ATTRIBUTE,
    EventMetadataCache,
    TrackerRefreshCoordinator,
    UnsuspendTrackerWidget,
    ensure_unsuspend_tracker_dock,
)
from share_tools.unsuspend_tracker import (
    TrackerStateSnapshot,
    UnsuspendEvent,
    clear_all,
    lock_scope,
    record_snapshot,
    shutdown_storage,
)


class FakeSignal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback

    def emit(self) -> None:
        assert self.callback is not None
        self.callback()


class FakeTimer:
    def __init__(self) -> None:
        self.timeout = FakeSignal()
        self.interval = 0
        self.active = False
        self.start_calls = 0
        self.stop_calls = 0

    def setInterval(self, interval: int) -> None:
        self.interval = interval

    def isActive(self) -> bool:
        return self.active

    def start(self) -> None:
        self.active = True
        self.start_calls += 1

    def stop(self) -> None:
        self.active = False
        self.stop_calls += 1


class ScheduledCallbacks:
    def __init__(self) -> None:
        self.callbacks = []

    def schedule(self, callback) -> None:
        self.callbacks.append(callback)

    def run_next(self) -> None:
        self.callbacks.pop(0)()


def make_coordinator(
    *,
    timer: FakeTimer,
    scheduled: ScheduledCallbacks,
    model_refresh,
    snapshot_provider,
    is_tracking=lambda: True,
    on_error=lambda _exc: None,
) -> TrackerRefreshCoordinator:
    return TrackerRefreshCoordinator(
        fallback_interval_ms=12345,
        timer_factory=lambda: timer,
        schedule_soon=scheduled.schedule,
        model_refresh=model_refresh,
        snapshot_provider=snapshot_provider,
        is_tracking=is_tracking,
        on_error=on_error,
    )


def test_refresh_snapshot_is_immutable() -> None:
    snapshot = TrackerStateSnapshot(False, None, (), (), 30)

    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "retention_days", 7)


def test_register_unregister_controls_single_fallback_timer() -> None:
    timer = FakeTimer()
    scheduled = ScheduledCallbacks()
    coordinator = make_coordinator(
        timer=timer,
        scheduled=scheduled,
        model_refresh=lambda: None,
        snapshot_provider=lambda: "same",
    )
    first = object()
    second = object()

    coordinator.register_consumer(first, lambda _revision: None, lambda: True)
    coordinator.register_consumer(second, lambda _revision: None, lambda: True)

    assert timer.interval == 12345
    assert timer.active
    assert timer.start_calls == 1
    coordinator.unregister_consumer(first)
    assert timer.active
    coordinator.unregister_consumer(second)
    assert not timer.active


def test_tracking_disabled_keeps_fallback_timer_stopped() -> None:
    timer = FakeTimer()
    scheduled = ScheduledCallbacks()
    tracking = False
    coordinator = make_coordinator(
        timer=timer,
        scheduled=scheduled,
        model_refresh=lambda: None,
        snapshot_provider=lambda: "same",
        is_tracking=lambda: tracking,
    )

    coordinator.register_consumer(object(), lambda _revision: None, lambda: True)

    assert not timer.active
    assert scheduled.callbacks == []


def test_same_loop_refresh_requests_are_coalesced() -> None:
    timer = FakeTimer()
    scheduled = ScheduledCallbacks()
    refresh_calls = 0

    def refresh() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    coordinator = make_coordinator(
        timer=timer,
        scheduled=scheduled,
        model_refresh=refresh,
        snapshot_provider=lambda: "same",
    )
    coordinator.register_consumer(object(), lambda _revision: None, lambda: True)

    coordinator.request_refresh(reason="operation")
    coordinator.request_refresh(reason="operation")

    assert len(scheduled.callbacks) == 1
    scheduled.run_next()
    assert refresh_calls == 1


def test_fallback_timer_requests_a_refresh() -> None:
    timer = FakeTimer()
    scheduled = ScheduledCallbacks()
    refresh_calls = 0

    def refresh() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    coordinator = make_coordinator(
        timer=timer,
        scheduled=scheduled,
        model_refresh=refresh,
        snapshot_provider=lambda: "same",
    )
    coordinator.register_consumer(object(), lambda _revision: None, lambda: True)

    timer.timeout.emit()

    assert len(scheduled.callbacks) == 1
    scheduled.run_next()
    assert refresh_calls == 1


def test_unchanged_refresh_does_not_publish_or_render() -> None:
    timer = FakeTimer()
    scheduled = ScheduledCallbacks()
    renders: list[int] = []
    coordinator = make_coordinator(
        timer=timer,
        scheduled=scheduled,
        model_refresh=lambda: None,
        snapshot_provider=lambda: "same",
    )
    coordinator.register_consumer(object(), renders.append, lambda: True)

    coordinator.request_refresh(reason="fallback")
    scheduled.run_next()

    assert coordinator.revision == 0
    assert renders == []


def test_changed_snapshot_renders_only_visible_consumers() -> None:
    timer = FakeTimer()
    scheduled = ScheduledCallbacks()
    snapshot = "before"
    visible_renders: list[int] = []
    hidden_renders: list[int] = []
    hidden = object()
    coordinator = make_coordinator(
        timer=timer,
        scheduled=scheduled,
        model_refresh=lambda: None,
        snapshot_provider=lambda: snapshot,
    )
    coordinator.register_consumer(object(), visible_renders.append, lambda: True)
    coordinator.register_consumer(hidden, hidden_renders.append, lambda: False)
    snapshot = "after"

    coordinator.publish_local_change()

    assert coordinator.revision == 1
    assert visible_renders == [1]
    assert hidden_renders == []
    coordinator.render_consumer(hidden)
    assert hidden_renders == []


def test_visible_consumer_can_render_latest_revision_after_being_hidden() -> None:
    timer = FakeTimer()
    scheduled = ScheduledCallbacks()
    snapshot = "before"
    visible = False
    renders: list[int] = []
    consumer = object()
    coordinator = make_coordinator(
        timer=timer,
        scheduled=scheduled,
        model_refresh=lambda: None,
        snapshot_provider=lambda: snapshot,
    )
    coordinator.register_consumer(consumer, renders.append, lambda: visible)
    snapshot = "after"
    coordinator.publish_local_change()
    visible = True

    coordinator.render_consumer(consumer)

    assert renders == [1]


def test_profile_close_stops_timer_and_clears_consumers() -> None:
    timer = FakeTimer()
    scheduled = ScheduledCallbacks()
    coordinator = make_coordinator(
        timer=timer,
        scheduled=scheduled,
        model_refresh=lambda: None,
        snapshot_provider=lambda: "same",
    )
    coordinator.register_consumer(object(), lambda _revision: None, lambda: True)

    coordinator.close()

    assert not timer.active
    assert coordinator.consumer_count == 0


def test_repeated_collection_error_is_reported_once_until_success() -> None:
    timer = FakeTimer()
    scheduled = ScheduledCallbacks()
    errors: list[str] = []
    should_fail = True

    def refresh() -> None:
        if should_fail:
            raise RuntimeError("collection unavailable")

    coordinator = make_coordinator(
        timer=timer,
        scheduled=scheduled,
        model_refresh=refresh,
        snapshot_provider=lambda: "same",
        on_error=lambda exc: errors.append(str(exc)),
    )
    coordinator.register_consumer(object(), lambda _revision: None, lambda: True)

    coordinator.request_refresh(reason="fallback")
    scheduled.run_next()
    coordinator.request_refresh(reason="fallback")
    scheduled.run_next()
    assert errors == ["collection unavailable"]

    should_fail = False
    coordinator.request_refresh(reason="fallback")
    scheduled.run_next()
    should_fail = True
    coordinator.request_refresh(reason="fallback")
    scheduled.run_next()
    assert errors == ["collection unavailable", "collection unavailable"]


def test_event_metadata_is_cached_and_pruned_with_events() -> None:
    cache = EventMetadataCache()
    event = UnsuspendEvent(10, 1, datetime(2026, 7, 26, 12), "deck:current")
    sort_calls: list[int] = []
    type_calls: list[int] = []

    def sort_field(nid: int) -> str:
        sort_calls.append(nid)
        return "Sort"

    def card_type(cid: int) -> str:
        type_calls.append(cid)
        return "Card"

    assert cache.resolve(event, sort_field, card_type) == ("Sort", "Card")
    assert cache.resolve(event, sort_field, card_type) == ("Sort", "Card")
    assert sort_calls == [1]
    assert type_calls == [10]

    cache.retain(set())
    assert cache.resolve(event, sort_field, card_type) == ("Sort", "Card")
    assert sort_calls == [1, 1]
    assert type_calls == [10, 10]


def test_attached_hidden_dock_stays_hidden_when_parent_is_shown(
    monkeypatch,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    shutdown_storage(clear_runtime=True)
    clear_all()
    now = datetime.now().replace(microsecond=0)
    lock_scope("deck:current", [10])
    record_snapshot([], lambda _cid: 1, now=now)
    metadata_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        browser_widget,
        "get_note_sort_field",
        lambda nid: metadata_calls.append(("sort", nid)) or "Sort",
    )
    monkeypatch.setattr(
        browser_widget,
        "get_card_type_name",
        lambda cid: metadata_calls.append(("type", cid)) or "Card",
    )
    parent = QMainWindow()

    ensure_unsuspend_tracker_dock(cast(Browser, parent), show=False)
    parent.show()
    app.processEvents()

    dock = getattr(parent, DOCK_ATTRIBUTE)
    widget = dock.widget()
    assert isinstance(widget, UnsuspendTrackerWidget)
    assert not dock.isVisible()
    assert not widget.isVisible()
    assert metadata_calls == []

    parent.close()
    browser_widget.deactivate_tracker_profile()
    clear_all()


def test_profile_teardown_invalidates_surviving_widget_metadata_cache() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    shutdown_storage(clear_runtime=True)
    clear_all()
    parent = QMainWindow()
    ensure_unsuspend_tracker_dock(cast(Browser, parent), show=False)
    dock = getattr(parent, DOCK_ATTRIBUTE)
    widget = dock.widget()
    assert isinstance(widget, UnsuspendTrackerWidget)
    event = UnsuspendEvent(10, 1, datetime(2026, 7, 26, 12), "deck:current")

    assert widget._event_metadata.resolve(
        event,
        lambda _nid: "Profile A sort",
        lambda _cid: "Profile A card",
    ) == ("Profile A sort", "Profile A card")

    browser_widget.deactivate_tracker_profile()

    assert widget._event_metadata.resolve(
        event,
        lambda _nid: "Profile B sort",
        lambda _cid: "Profile B card",
    ) == ("Profile B sort", "Profile B card")

    parent.close()
    app.processEvents()
    clear_all()
