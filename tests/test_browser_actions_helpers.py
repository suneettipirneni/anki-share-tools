from types import SimpleNamespace
from typing import Any, Callable, Optional

from anki.collection import OpChanges
from share_tools import browser_actions
from share_tools.ankipatch import (
    AnkiPatch,
    CardPatchRow,
    PatchApplyResult,
    PatchOperationResult,
)
from share_tools.browser_actions import (
    PatchPreviewDecision,
    build_patch_preview_ledger,
    combine_patch_apply_results,
    on_operation_did_execute,
    preview_result_values,
)


def patch_result(
    guid: str,
    status: str,
    *,
    card_ord: int = 0,
    suspended: bool = False,
    card_id: Optional[int] = None,
) -> PatchApplyResult:
    return PatchApplyResult(
        row=CardPatchRow(guid, card_ord, suspended),
        card_id=card_id,
        status=status,
        message=f"{status} details",
    )


class DeferredCollectionOp:
    instances: list["DeferredCollectionOp"] = []

    def __init__(
        self,
        parent: Any,
        op: Callable[[Any], PatchOperationResult],
    ) -> None:
        self.parent = parent
        self.op = op
        self.success_callback: Optional[
            Callable[[PatchOperationResult], None]
        ] = None
        self.failure_callback: Optional[Callable[[Exception], None]] = None
        self.ran = False
        self.instances.append(self)

    def success(
        self,
        callback: Callable[[PatchOperationResult], None],
    ) -> "DeferredCollectionOp":
        self.success_callback = callback
        return self

    def failure(
        self,
        callback: Callable[[Exception], None],
    ) -> "DeferredCollectionOp":
        self.failure_callback = callback
        return self

    def run_in_background(self) -> None:
        self.ran = True

    def complete(self, collection: Any) -> None:
        result = self.op(collection)
        assert self.success_callback is not None
        self.success_callback(result)

    def fail(self, exc: Exception) -> None:
        assert self.failure_callback is not None
        self.failure_callback(exc)


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


def test_preview_ledger_retains_every_status_and_only_pending_is_selectable() -> None:
    results = [
        patch_result("guid-error", "error", card_id=30),
        patch_result("guid-same", "unchanged", card_id=20),
        patch_result("guid-missing", "missing", suspended=True),
        patch_result("guid-change", "pending", card_id=10),
    ]

    ledger = build_patch_preview_ledger(results)

    assert [row.result.status for row in ledger.rows] == [
        "pending",
        "unchanged",
        "error",
        "missing",
    ]
    assert [row.selectable for row in ledger.rows] == [True, False, False, False]
    assert [row.checked for row in ledger.rows] == [True, False, False, False]
    assert [row.status_label for row in ledger.rows] == [
        "Will change",
        "Same state",
        "Error",
        "Missing",
    ]
    assert ledger.change_count == 1
    assert ledger.unchanged_count == 1
    assert ledger.unavailable_count == 2


def test_all_unresolved_preview_rows_keep_patch_identity_and_details() -> None:
    ledger = build_patch_preview_ledger(
        [
            patch_result("guid-z", "missing", card_ord=2, suspended=True),
            patch_result("guid-a", "error", card_ord=1),
        ]
    )

    assert [row.result.row.note_guid for row in ledger.rows] == ["guid-a", "guid-z"]
    assert all(not row.selectable for row in ledger.rows)
    assert preview_result_values(ledger.rows[0]) == [
        "",
        "",
        "",
        "",
        "",
        "Unsuspended",
        "Error",
        "guid-a",
        "1",
        "error details",
    ]
    assert preview_result_values(ledger.rows[1]) == [
        "",
        "",
        "",
        "",
        "",
        "Suspended",
        "Missing",
        "guid-z",
        "2",
        "missing details",
    ]


def test_combined_results_keep_preview_only_unresolved_without_unchanged() -> None:
    updated = patch_result("guid-change", "updated", card_id=10)
    unchanged = patch_result("guid-same", "unchanged", card_id=20)
    missing = patch_result("guid-missing", "missing")
    decision = PatchPreviewDecision(
        selected_rows=(updated.row,),
        preview_only_results=(missing,),
    )

    combined = combine_patch_apply_results([updated], decision)

    assert combined == [updated, missing]
    assert unchanged not in combined


def test_apply_patch_cancel_does_not_mutate_collection(monkeypatch) -> None:
    collection = object()
    apply_calls = 0
    monkeypatch.setattr(browser_actions, "mw", SimpleNamespace(col=collection))
    monkeypatch.setattr(
        browser_actions.QFileDialog,
        "getOpenFileName",
        lambda *_args: ("cards.ankipatch", ""),
    )
    monkeypatch.setattr(
        browser_actions,
        "read_patch",
        lambda _path: AnkiPatch(cards=[CardPatchRow("guid-change", 0, False)]),
    )
    monkeypatch.setattr(
        browser_actions,
        "preview_patch_against_collection",
        lambda _col, _patch: [patch_result("guid-change", "pending", card_id=10)],
    )
    monkeypatch.setattr(
        browser_actions,
        "show_ankipatch_preview_dialog",
        lambda _parent, _results: None,
    )

    def apply_patch(_col, _patch):
        nonlocal apply_calls
        apply_calls += 1
        return []

    monkeypatch.setattr(browser_actions, "apply_patch_to_collection", apply_patch)

    browser_actions.apply_ankipatch_from_file(object())

    assert apply_calls == 0


def test_apply_patch_collection_op_syncs_before_hook_and_then_shows_results(
    monkeypatch,
) -> None:
    collection = object()
    pending = patch_result("guid-change", "pending", card_id=10)
    unchanged = patch_result("guid-same", "unchanged", card_id=20)
    missing = patch_result("guid-missing", "missing")
    updated = patch_result("guid-change", "updated", card_id=10)
    shown_results: list[PatchApplyResult] = []
    events: list[str] = []

    DeferredCollectionOp.instances.clear()
    monkeypatch.setattr(browser_actions, "mw", SimpleNamespace(col=collection))
    monkeypatch.setattr(browser_actions, "CollectionOp", DeferredCollectionOp)
    monkeypatch.setattr(
        browser_actions.QFileDialog,
        "getOpenFileName",
        lambda *_args: ("cards.ankipatch", ""),
    )
    monkeypatch.setattr(
        browser_actions,
        "read_patch",
        lambda _path: AnkiPatch(
            cards=[pending.row, unchanged.row, missing.row],
        ),
    )
    monkeypatch.setattr(
        browser_actions,
        "preview_patch_against_collection",
        lambda _col, _patch: [pending, unchanged, missing],
    )
    monkeypatch.setattr(
        browser_actions,
        "show_ankipatch_preview_dialog",
        lambda _parent, _results: PatchPreviewDecision(
            selected_rows=(pending.row,),
            preview_only_results=(missing,),
        ),
    )
    monkeypatch.setattr(
        browser_actions,
        "apply_patch_to_collection",
        lambda _col, _patch: PatchOperationResult(
            changes=OpChanges(card=True, study_queues=True),
            results=(updated,),
        ),
    )

    def sync_tracker() -> None:
        events.append("sync")

    monkeypatch.setattr(
        browser_actions,
        "sync_tracker_baseline_to_current_scope",
        sync_tracker,
    )
    def show_results(_parent, results) -> None:
        events.append("dialog")
        shown_results.extend(results)

    monkeypatch.setattr(
        browser_actions,
        "show_ankipatch_results_dialog",
        show_results,
    )

    browser_actions.apply_ankipatch_from_file(object())

    assert len(DeferredCollectionOp.instances) == 1
    operation = DeferredCollectionOp.instances[0]
    assert operation.ran
    assert events == []
    assert shown_results == []

    operation.complete(collection)
    events.append("operation_did_execute")

    assert shown_results == [updated, missing]
    assert unchanged not in shown_results
    assert events == ["sync", "dialog", "operation_did_execute"]


def test_apply_patch_collection_op_failure_shows_actionable_error(monkeypatch) -> None:
    collection = object()
    pending = patch_result("guid-change", "pending", card_id=10)
    messages: list[str] = []

    DeferredCollectionOp.instances.clear()
    monkeypatch.setattr(browser_actions, "mw", SimpleNamespace(col=collection))
    monkeypatch.setattr(browser_actions, "CollectionOp", DeferredCollectionOp)
    monkeypatch.setattr(
        browser_actions.QFileDialog,
        "getOpenFileName",
        lambda *_args: ("cards.ankipatch", ""),
    )
    monkeypatch.setattr(
        browser_actions,
        "read_patch",
        lambda _path: AnkiPatch(cards=[pending.row]),
    )
    monkeypatch.setattr(
        browser_actions,
        "preview_patch_against_collection",
        lambda _col, _patch: [pending],
    )
    monkeypatch.setattr(
        browser_actions,
        "show_ankipatch_preview_dialog",
        lambda _parent, _results: PatchPreviewDecision(
            selected_rows=(pending.row,),
            preview_only_results=(),
        ),
    )
    monkeypatch.setattr(browser_actions, "showInfo", messages.append)

    browser_actions.apply_ankipatch_from_file(object())
    DeferredCollectionOp.instances[0].fail(RuntimeError("database unavailable"))

    assert messages == [
        "Could not apply ankipatch:\n\ndatabase unavailable",
    ]
