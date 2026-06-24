from pathlib import Path
from typing import Any, Optional

from aqt import gui_hooks, mw
from aqt.browser import Browser
from aqt.qt import (
    QAction,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMenu,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    Qt,
)
from aqt.utils import showInfo, tooltip

from .ankipatch import (
    AnkiPatch,
    PatchApplyResult,
    apply_patch_to_collection,
    card_rows_from_card_ids,
    ensure_ankipatch_suffix,
    read_patch,
    write_patch,
)
from .queries import (
    infer_common_class_tags,
    normalize_tag_part,
)
from .browser_widget import (
    attach_unsuspend_tracker_widget,
    refresh_tracker_widgets_after_delay,
    show_unsuspend_tracker_widget,
    sync_tracker_baseline_to_current_scope,
)


CLASS_TAG_PREFIX = "class::"
_browser_suspend_wrapped = False
_main_window_actions_registered = False


def register_hooks() -> None:
    wrap_browser_suspend_action()
    gui_hooks.main_window_did_init.append(register_main_window_actions)
    gui_hooks.browser_will_show_context_menu.append(on_browser_context_menu)
    gui_hooks.browser_will_show.append(attach_unsuspend_tracker_widget)


def register_main_window_actions() -> None:
    global _main_window_actions_registered

    if _main_window_actions_registered:
        return

    tools_menu = getattr(getattr(mw, "form", None), "menuTools", None)

    if tools_menu is None:
        menubar = getattr(getattr(mw, "form", None), "menubar", None)
        if menubar is None:
            return
        tools_menu = QMenu("Share Tools", mw)
        menubar.addMenu(tools_menu)
    else:
        tools_menu.addSeparator()

    apply_patch_action = QAction("Apply ankipatch…", mw)
    apply_patch_action.triggered.connect(lambda: apply_ankipatch_from_file(mw))
    tools_menu.addAction(apply_patch_action)
    _main_window_actions_registered = True


def wrap_browser_suspend_action() -> None:
    global _browser_suspend_wrapped

    if _browser_suspend_wrapped:
        return

    original_suspend_selected_cards = getattr(Browser, "suspend_selected_cards", None)

    if original_suspend_selected_cards is None:
        return

    def wrapped_suspend_selected_cards(browser: Browser, checked: bool) -> Any:
        result = original_suspend_selected_cards(browser, checked)

        if not checked:
            refresh_tracker_widgets_after_delay()

        return result

    Browser.suspend_selected_cards = wrapped_suspend_selected_cards
    _browser_suspend_wrapped = True


def on_browser_context_menu(browser: Browser, menu: QMenu) -> None:
    share_menu = QMenu("Share Tools", browser)

    show_panel_action = QAction("Show fresh cards panel", browser)
    show_panel_action.triggered.connect(lambda: show_unsuspend_tracker_widget(browser))
    share_menu.addAction(show_panel_action)

    share_menu.addSeparator()

    export_patch_action = QAction("Export selected cards as ankipatch…", browser)
    export_patch_action.triggered.connect(
        lambda: export_selected_cards_as_ankipatch(browser)
    )
    share_menu.addAction(export_patch_action)

    export_class_patch_action = QAction(
        "Export current unsuspended class subset as ankipatch…",
        browser,
    )
    export_class_patch_action.triggered.connect(
        lambda: export_current_unsuspended_class_subset_as_ankipatch(browser)
    )
    share_menu.addAction(export_class_patch_action)

    apply_patch_action = QAction("Apply ankipatch…", browser)
    apply_patch_action.triggered.connect(lambda: apply_ankipatch_from_file(browser))
    share_menu.addAction(apply_patch_action)

    menu.addMenu(share_menu)


def export_current_unsuspended_class_subset_as_ankipatch(browser: Browser) -> None:
    class_tags = get_class_tags_from_selected_notes(browser)

    if not class_tags:
        showInfo(
            f"No class tags found on selected notes. Expected prefix: {CLASS_TAG_PREFIX}"
        )
        return

    selected_tags = choose_tags_if_needed(
        browser=browser,
        class_tags=class_tags,
        title="Choose class tag",
        label="Choose the class tag to export:",
        force_single=True,
    )

    if not selected_tags:
        return

    class_tag = selected_tags[0]
    class_name = class_tag.removeprefix(CLASS_TAG_PREFIX)
    source_query = f"tag:{class_tag} -is:suspended"
    card_ids = mw.col.find_cards(source_query)

    if not card_ids:
        showInfo(f"No unsuspended cards found for query:\n\n{source_query}")
        return

    filename = f"{normalize_tag_part(class_name)}-unsuspended.ankipatch"
    export_card_ids_as_ankipatch(browser, [int(cid) for cid in card_ids], filename)


def export_selected_cards_as_ankipatch(browser: Browser) -> None:
    card_ids = sorted({int(cid) for cid in browser.selectedCards()})

    if not card_ids:
        showInfo("No cards selected.")
        return

    export_card_ids_as_ankipatch(browser, card_ids, "selected-cards.ankipatch")


def export_card_ids_as_ankipatch(
    parent: Any,
    card_ids: list[int],
    default_filename: str,
) -> Optional[Path]:
    try:
        rows = card_rows_from_card_ids(mw.col, card_ids)
    except Exception as exc:
        showInfo(f"Could not build ankipatch:\n\n{exc}")
        return None

    if not rows:
        showInfo("No cards found to export.")
        return None

    selected_path, _filter = QFileDialog.getSaveFileName(
        parent,
        "Save ankipatch",
        default_filename,
        "Anki patch (*.ankipatch)",
    )

    if not selected_path:
        return None

    path = ensure_ankipatch_suffix(Path(selected_path))

    try:
        write_patch(path, AnkiPatch(cards=rows))
    except Exception as exc:
        showInfo(f"Could not save ankipatch:\n\n{exc}")
        return None

    tooltip(f"Saved ankipatch with {len(rows)} card(s).")
    return path


def apply_ankipatch_from_file(parent: Any) -> None:
    if getattr(mw, "col", None) is None:
        showInfo("Open a profile before applying an ankipatch.")
        return

    selected_path, _filter = QFileDialog.getOpenFileName(
        parent,
        "Apply ankipatch",
        "",
        "Anki patch (*.ankipatch)",
    )

    if not selected_path:
        return

    path = Path(selected_path)

    try:
        patch = read_patch(path)
    except Exception as exc:
        showInfo(f"Could not read ankipatch:\n\n{exc}")
        return

    if not patch.cards:
        showInfo("Ankipatch contains no cards.")
        return

    results = apply_patch_to_collection(mw.col, patch)
    sync_tracker_baseline_to_current_scope()
    maybe_reset_main_window()
    show_ankipatch_results_dialog(parent, results)


def show_ankipatch_results_dialog(
    parent: Any,
    results: list[PatchApplyResult],
) -> None:
    successful = [result for result in results if result.successful]
    unsuccessful = [result for result in results if not result.successful]
    resolved = [result for result in results if result.resolved]
    unresolved = [result for result in results if not result.resolved]
    unsuccessful_resolved = [result for result in unsuccessful if result.resolved]

    dialog = QDialog(parent)
    dialog.setWindowTitle("Ankipatch results")
    dialog.resize(980, 620)

    layout = QVBoxLayout(dialog)
    summary = QLabel(
        f"Applied ankipatch: {len(successful)} successful, "
        f"{len(unsuccessful)} unsuccessful.",
        dialog,
    )
    summary.setWordWrap(True)
    layout.addWidget(summary)

    tabs = QTabWidget(dialog)
    tabs.addTab(
        build_resolved_cards_table(resolved, dialog),
        f"Resolved cards ({len(resolved)})",
    )
    tabs.addTab(
        build_resolved_cards_table(successful, dialog),
        f"Successful cards ({len(successful)})",
    )
    tabs.addTab(
        build_resolved_cards_table(
            unsuccessful_resolved,
            dialog,
        ),
        f"Unsuccessful cards ({len(unsuccessful_resolved)})",
    )
    tabs.addTab(
        build_unresolved_rows_table(unresolved, dialog),
        f"Unresolved rows ({len(unresolved)})",
    )
    layout.addWidget(tabs)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
    close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
    if close_button is not None:
        close_button.clicked.connect(dialog.accept)
    layout.addWidget(buttons)

    dialog.exec()


def build_resolved_cards_table(
    results: list[PatchApplyResult],
    parent: Any,
) -> QTableWidget:
    headers = [
        "Sort Field",
        "Card Type",
        "Deck",
        "Due",
        "Previous State",
        "Target State",
        "Result",
        "Details",
    ]
    rows = sorted(
        (resolved_card_result_values(result) for result in results),
        key=lambda values: (values[0].lower(), values[1].lower(), values[2].lower(), values[3]),
    )
    table = QTableWidget(len(rows), len(headers), parent)
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)

    for row_index, values in enumerate(rows):
        for column_index, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_index, column_index, item)

    table.resizeColumnsToContents()
    return table


def resolved_card_result_values(result: PatchApplyResult) -> list[str]:
    card = get_result_card(result)
    note_id = result.note_id

    if card is not None:
        note_id = int(card.nid)

    return [
        get_note_sort_field(note_id),
        get_card_type_name(card),
        get_deck_name(card),
        get_due_text(card),
        suspended_state_label(result.previous_suspended),
        "Suspended" if result.row.suspended else "Unsuspended",
        result_status_label(result),
        result.message,
    ]


def build_unresolved_rows_table(
    results: list[PatchApplyResult],
    parent: Any,
) -> QTableWidget:
    headers = [
        "Patch Note GUID",
        "Patch Card Ord",
        "Target State",
        "Result",
        "Details",
    ]
    table = QTableWidget(len(results), len(headers), parent)
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)

    for row_index, result in enumerate(results):
        values = [
            result.row.note_guid,
            str(result.row.card_ord),
            "Suspended" if result.row.suspended else "Unsuspended",
            result_status_label(result),
            result.message,
        ]

        for column_index, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_index, column_index, item)

    table.resizeColumnsToContents()
    return table


def suspended_state_label(suspended: Optional[bool]) -> str:
    if suspended is None:
        return ""

    return "Suspended" if suspended else "Unsuspended"


def result_status_label(result: PatchApplyResult) -> str:
    if result.status == "updated":
        return "Updated"

    if result.status == "unchanged":
        return "Already matched"

    if result.status == "missing":
        return "Missing"

    if result.status == "error":
        return "Error"

    return result.status


def get_result_card(result: PatchApplyResult) -> Optional[Any]:
    if result.card_id is None:
        return None

    try:
        return mw.col.get_card(result.card_id)
    except Exception:
        return None


def get_note_sort_field(note_id: Optional[int]) -> str:
    if note_id is None:
        return ""

    try:
        note = mw.col.get_note(note_id)
        sort_field = getattr(note, "sfld", None)

        if sort_field:
            return str(sort_field)

        model = note.note_type()
        sort_index = int(model.get("sortf", 0))
        return str(note.fields[sort_index])
    except Exception:
        return ""


def get_card_type_name(card: Optional[Any]) -> str:
    if card is None:
        return ""

    try:
        template = card.template()

        if isinstance(template, dict):
            return str(template.get("name", ""))

        return str(getattr(template, "name", ""))
    except Exception:
        return ""


def get_deck_name(card: Optional[Any]) -> str:
    if card is None:
        return ""

    try:
        current_deck_id = getattr(card, "current_deck_id", None)
        deck_id = current_deck_id() if callable(current_deck_id) else card.did
        return str(mw.col.decks.name(deck_id, default=True))
    except Exception:
        return ""


def get_due_text(card: Optional[Any]) -> str:
    if card is None:
        return ""

    try:
        queue = int(card.queue)
        due = int(card.due)

        if queue == -1:
            return "Suspended"

        if queue < 0:
            return "Buried"

        if queue == 0:
            return f"New #{due}"

        if queue in {1, 3}:
            return f"Learning {due}"

        return str(due)
    except Exception:
        return ""


def get_selected_note_ids(browser: Browser) -> list[int]:
    note_ids: set[int] = set()

    for cid in browser.selectedCards():
        card = mw.col.get_card(cid)
        note_ids.add(card.nid)

    return sorted(note_ids)


def get_class_tags_from_selected_notes(browser: Browser) -> list[str]:
    note_ids = get_selected_note_ids(browser)

    if not note_ids:
        return []

    note_tags: list[list[str]] = []

    for nid in note_ids:
        note = mw.col.get_note(nid)
        note_tags.append(list(note.tags))

    return infer_common_class_tags(
        note_tags=note_tags,
        class_tag_prefix=CLASS_TAG_PREFIX,
    )


def choose_tags_if_needed(
    browser: Browser,
    class_tags: list[str],
    title: str,
    label: str,
    force_single: bool = False,
) -> Optional[list[str]]:
    if not class_tags:
        return []

    if len(class_tags) == 1:
        return class_tags

    options = class_tags if force_single else ["<Use all>"] + class_tags

    selected, ok = QInputDialog.getItem(
        browser,
        title,
        label,
        options,
        0,
        False,
    )

    if not ok:
        return None

    if selected == "<Use all>":
        return class_tags

    return [selected]


def debug_show(value: Any) -> None:
    showInfo(str(value))


def maybe_reset_main_window() -> None:
    reset = getattr(mw, "reset", None)

    if callable(reset):
        reset()
