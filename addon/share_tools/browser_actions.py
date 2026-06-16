from datetime import date
from typing import Any, Optional

from aqt import gui_hooks, mw
from aqt.browser import Browser
from aqt.qt import QAction, QApplication, QInputDialog, QMenu
from aqt.utils import showInfo, tooltip

from .queries import (
    ShareTag,
    build_class_query,
    build_nid_query,
    build_unsuspended_tag_query,
    infer_common_class_tags,
)
from .browser_widget import (
    attach_unsuspend_tracker_widget,
    refresh_tracker_widgets_after_delay,
)


CLASS_TAG_PREFIX = "class::"
_browser_suspend_wrapped = False


def register_hooks() -> None:
    wrap_browser_suspend_action()
    gui_hooks.browser_will_show_context_menu.append(on_browser_context_menu)
    gui_hooks.browser_will_show.append(attach_unsuspend_tracker_widget)


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

    copy_nid_action = QAction("Copy exact selected notes query", browser)
    copy_nid_action.triggered.connect(lambda: copy_exact_selected_notes_query(browser))
    share_menu.addAction(copy_nid_action)

    copy_class_action = QAction("Copy class query excluding suspended", browser)
    copy_class_action.triggered.connect(
        lambda: copy_class_query_excluding_suspended(browser)
    )
    share_menu.addAction(copy_class_action)

    tag_unsuspended_action = QAction("Tag current unsuspended class subset…", browser)
    tag_unsuspended_action.triggered.connect(
        lambda: tag_current_unsuspended_class_subset(browser)
    )
    share_menu.addAction(tag_unsuspended_action)

    menu.addMenu(share_menu)


def copy_exact_selected_notes_query(browser: Browser) -> None:
    note_ids = get_selected_note_ids(browser)

    if not note_ids:
        showInfo("No cards selected.")
        return

    query = build_nid_query(note_ids)
    copy_to_clipboard(query)

    tooltip(f"Copied query for {len(note_ids)} unique note(s).")


def copy_class_query_excluding_suspended(browser: Browser) -> None:
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
        label="Multiple class tags found. Choose one, or cancel to use all:",
    )

    if selected_tags is None:
        return

    query = build_class_query(selected_tags, exclude_suspended=True)
    copy_to_clipboard(query)

    tooltip("Copied class query excluding suspended cards.")


def tag_current_unsuspended_class_subset(browser: Browser) -> None:
    """
    Main workflow:

    1. User selects representative cards from a class.
    2. Add-on infers the class tag, e.g. class::cardiology.
    3. It searches for cards matching: tag:class::cardiology -is:suspended
    4. It tags those notes with share_unsuspended::<class>_<date>
    5. It copies the query for that temporary share tag.

    Friend can then search that share tag and unsuspend matching cards.
    """
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
        label="Choose the class tag to snapshot:",
        force_single=True,
    )

    if not selected_tags:
        return

    class_tag = selected_tags[0]
    class_name = class_tag.removeprefix(CLASS_TAG_PREFIX)

    share_tag_default = ShareTag(
        class_name=class_name, created_on=date.today()
    ).to_anki_tag()

    share_tag, ok = QInputDialog.getText(
        browser,
        "Share tag",
        "Temporary share tag:",
        text=share_tag_default,
    )

    if not ok:
        return

    share_tag = share_tag.strip()

    if not share_tag:
        showInfo("Share tag cannot be empty.")
        return

    source_query = f"tag:{class_tag} -is:suspended"
    card_ids = mw.col.find_cards(source_query)

    if not card_ids:
        showInfo(f"No unsuspended cards found for query:\n\n{source_query}")
        return

    note_ids = get_note_ids_from_card_ids(card_ids)

    for nid in note_ids:
        note = mw.col.get_note(nid)
        note.add_tag(share_tag)
        mw.col.update_note(note)

    mw.col.save()

    share_query = build_unsuspended_tag_query(share_tag)
    copy_to_clipboard(share_query)

    tooltip(f"Tagged {len(note_ids)} note(s) and copied share query.")


def get_selected_note_ids(browser: Browser) -> list[int]:
    note_ids: set[int] = set()

    for cid in browser.selectedCards():
        card = mw.col.get_card(cid)
        note_ids.add(card.nid)

    return sorted(note_ids)


def get_note_ids_from_card_ids(card_ids: list[int]) -> list[int]:
    note_ids: set[int] = set()

    for cid in card_ids:
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

    if not force_single:
        # For "copy class query", using all matching class tags is acceptable.
        # But this keeps the UX explicit.
        options = ["<Use all>"] + class_tags
    else:
        options = class_tags

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


def copy_to_clipboard(value: str) -> None:
    QApplication.clipboard().setText(value)


def debug_show(value: Any) -> None:
    showInfo(str(value))
