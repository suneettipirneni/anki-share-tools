from types import SimpleNamespace
from typing import Optional

from anki.collection import OpChanges
from share_tools import browser_actions
from share_tools.ankipatch import AnkiPatch, CardPatchRow, PatchApplyResult
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


def test_apply_patch_results_include_preview_only_missing_row(monkeypatch) -> None:
    collection = object()
    pending = patch_result("guid-change", "pending", card_id=10)
    unchanged = patch_result("guid-same", "unchanged", card_id=20)
    missing = patch_result("guid-missing", "missing")
    updated = patch_result("guid-change", "updated", card_id=10)
    shown_results: list[PatchApplyResult] = []
    sync_calls = 0
    reset_calls = 0

    monkeypatch.setattr(browser_actions, "mw", SimpleNamespace(col=collection))
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
        lambda _col, _patch: [updated],
    )

    def sync_tracker() -> None:
        nonlocal sync_calls
        sync_calls += 1

    def reset_main_window() -> None:
        nonlocal reset_calls
        reset_calls += 1

    monkeypatch.setattr(
        browser_actions,
        "sync_tracker_baseline_to_current_scope",
        sync_tracker,
    )
    monkeypatch.setattr(browser_actions, "maybe_reset_main_window", reset_main_window)
    monkeypatch.setattr(
        browser_actions,
        "show_ankipatch_results_dialog",
        lambda _parent, results: shown_results.extend(results),
    )

    browser_actions.apply_ankipatch_from_file(object())

    assert shown_results == [updated, missing]
    assert unchanged not in shown_results
    assert sync_calls == 1
    assert reset_calls == 1
