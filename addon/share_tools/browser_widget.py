from datetime import datetime
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Optional, Union
from weakref import WeakSet

from aqt import mw
from aqt.browser import Browser
from aqt.qt import (
    QAbstractItemView,
    QAction,
    QComboBox,
    QDate,
    QDateEdit,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTimer,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    Qt,
    sip,
)
from aqt.utils import askUser, showInfo, tooltip

from .ankipatch import (
    AnkiPatch,
    card_rows_from_card_ids,
    ensure_ankipatch_suffix,
    write_patch,
)
from . import unsuspend_tracker
from .tracker_database import TrackerDatabase
from .unsuspend_tracker import DateRange, FreshnessWindow


ADDON_DIR = Path(__file__).resolve().parents[1]
USER_FILES_DIR = ADDON_DIR / "user_files"
LEGACY_DATABASE_FILE = USER_FILES_DIR / "fresh_card_state.sqlite3"
LEGACY_STATE_FILE = ADDON_DIR / "unsuspend_tracker_state.json"
PROFILE_DATABASE_NAME = "fresh_card_state.sqlite3"
LEGACY_CLAIM_MARKER = USER_FILES_DIR / "profiles" / ".legacy-state-claimed"
TIMER_INTERVAL_MS = 2000
DOCK_ATTRIBUTE = "_share_tools_unsuspend_tracker_dock"
CUSTOM_RANGE_VALUE = "custom"
RETENTION_OPTIONS = (
    ("1 month", 30),
    ("1 week", 7),
    ("1 day", 1),
    ("1 year", 365),
    ("Forever", 0),
)
_active_profile_key: Optional[str] = None
_active_database_path: Optional[Path] = None
_widgets: WeakSet["UnsuspendTrackerWidget"] = WeakSet()


class UnsuspendTrackerWidget(QWidget):
    def __init__(self, browser: Browser) -> None:
        super().__init__(browser)
        self.browser = browser
        self.timer = QTimer(self)
        self.timer.setInterval(TIMER_INTERVAL_MS)
        self.timer.timeout.connect(self.on_timer_tick)

        self.tracking_label = QLabel(self)
        self.scope_label = QLabel(self)
        self.scope_label.setWordWrap(True)
        self.count_label = QLabel(self)
        self.retention_combo = QComboBox(self)
        for label, retention_days in RETENTION_OPTIONS:
            self.retention_combo.addItem(label, retention_days)
        self.retention_combo.setToolTip(
            "Fresh unsuspends older than the selected duration are deleted."
        )
        self.sync_retention_combo()
        self.retention_combo.currentIndexChanged.connect(self.on_retention_changed)
        self.window_combo = QComboBox(self)
        self.window_combo.addItem("Today", FreshnessWindow.TODAY.value)
        self.window_combo.addItem("This week", FreshnessWindow.THIS_WEEK.value)
        self.window_combo.addItem("Custom range", CUSTOM_RANGE_VALUE)
        today = QDate.currentDate()
        self.from_date_label = QLabel("From:", self)
        self.from_date_edit = QDateEdit(today, self)
        self.from_date_edit.setCalendarPopup(True)
        self.from_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.from_date_edit.setMaximumDate(today)
        self.from_date_label.setVisible(False)
        self.from_date_edit.setVisible(False)
        self.to_date_label = QLabel("To:", self)
        self.to_date_edit = QDateEdit(today, self)
        self.to_date_edit.setCalendarPopup(True)
        self.to_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.to_date_edit.setMaximumDate(today)
        self.to_date_label.setVisible(False)
        self.to_date_edit.setVisible(False)
        self.window_combo.currentIndexChanged.connect(self.on_window_changed)
        self.from_date_edit.dateChanged.connect(self.on_from_date_changed)
        self.to_date_edit.dateChanged.connect(self.on_to_date_changed)
        self.events_table = QTableWidget(0, 6, self)
        self.events_table.setHorizontalHeaderLabels(
            ["Detected", "Sort field", "Card type", "Card ID", "Note ID", "Scope"]
        )
        self.events_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.events_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.events_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.events_table.setAlternatingRowColors(True)
        self.events_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.events_table.customContextMenuRequested.connect(
            self.show_events_table_context_menu
        )
        self.events_table.verticalHeader().setVisible(False)
        self.events_table.horizontalHeader().setStretchLastSection(True)
        self.events_table.setMinimumHeight(180)

        self.lock_button = QPushButton("Lock current search", self)
        self.lock_button.clicked.connect(self.lock_current_search)

        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(lambda: self.refresh(show_errors=True))

        self.export_patch_button = QPushButton("Export fresh card patch...", self)
        self.export_patch_button.clicked.connect(self.export_fresh_card_patch)

        self.apply_patch_button = QPushButton("Apply patch file...", self)
        self.apply_patch_button.clicked.connect(self.apply_patch_file)

        self.clear_button = QPushButton("Clear captured", self)
        self.clear_button.clicked.connect(self.clear_captured)
        self.destroyed.connect(lambda: unregister_tracker_widget(self))

        self.setup_layout()
        self.update_view()
        self.timer.start()

    def setup_layout(self) -> None:
        layout = QVBoxLayout()
        layout.addWidget(self.tracking_label)
        layout.addWidget(self.scope_label)

        retention_layout = QHBoxLayout()
        retention_layout.addWidget(QLabel("Keep fresh unsuspends for:", self))
        retention_layout.addWidget(self.retention_combo)
        layout.addLayout(retention_layout)

        window_layout = QHBoxLayout()
        window_layout.addWidget(QLabel("Window:", self))
        window_layout.addWidget(self.window_combo)
        layout.addLayout(window_layout)

        date_range_layout = QHBoxLayout()
        date_range_layout.addWidget(self.from_date_label)
        date_range_layout.addWidget(self.from_date_edit)
        date_range_layout.addWidget(self.to_date_label)
        date_range_layout.addWidget(self.to_date_edit)
        layout.addLayout(date_range_layout)

        layout.addWidget(self.count_label)
        layout.addWidget(self.events_table)
        layout.addWidget(self.lock_button)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.export_patch_button)
        layout.addWidget(self.apply_patch_button)
        layout.addWidget(self.clear_button)
        layout.addStretch(1)
        self.setLayout(layout)

    def selected_preset_window(self) -> Optional[FreshnessWindow]:
        if not is_widget_alive(self.window_combo):
            return FreshnessWindow.TODAY

        value = self.window_combo.currentData()
        if value == FreshnessWindow.THIS_WEEK.value:
            return FreshnessWindow.THIS_WEEK
        if value == FreshnessWindow.TODAY.value:
            return FreshnessWindow.TODAY
        return None

    def on_retention_changed(self, _index: int) -> None:
        retention_days = int(self.retention_combo.currentData())
        removed_count = unsuspend_tracker.set_retention_days(retention_days)
        refresh_tracker_widget_views()

        if removed_count:
            tooltip(f"Removed {removed_count} expired fresh unsuspend(s).")

    def sync_retention_combo(self) -> None:
        retention_days = unsuspend_tracker.get_retention_days()
        preset_values = {value for _label, value in RETENTION_OPTIONS}
        signals_were_blocked = self.retention_combo.blockSignals(True)

        for index in reversed(range(self.retention_combo.count())):
            value = int(self.retention_combo.itemData(index))
            if value not in preset_values:
                self.retention_combo.removeItem(index)

        selected_index = self.retention_combo.findData(retention_days)
        if selected_index == -1:
            self.retention_combo.addItem(
                f"{retention_days} days (current)",
                retention_days,
            )
            selected_index = self.retention_combo.count() - 1

        self.retention_combo.setCurrentIndex(selected_index)
        self.retention_combo.blockSignals(signals_were_blocked)

    def selected_date_range(self) -> DateRange:
        preset = self.selected_preset_window()

        if preset is not None:
            return unsuspend_tracker.date_range_for_window(preset)

        return DateRange(
            start=self.from_date_edit.date().toPyDate(),
            end=self.to_date_edit.date().toPyDate(),
        )

    def on_window_changed(self, _index: int) -> None:
        custom_range_selected = self.selected_preset_window() is None

        for widget in (
            self.from_date_label,
            self.from_date_edit,
            self.to_date_label,
            self.to_date_edit,
        ):
            widget.setVisible(custom_range_selected)

        self.update_view()

    def on_from_date_changed(self, selected_date: QDate) -> None:
        if selected_date > self.to_date_edit.date():
            self.to_date_edit.setDate(selected_date)
            return

        self.update_view()

    def on_to_date_changed(self, selected_date: QDate) -> None:
        if selected_date < self.from_date_edit.date():
            self.from_date_edit.setDate(selected_date)
            return

        self.update_view()

    def update_view(self) -> None:
        if not is_widget_alive(self):
            return

        self.sync_retention_combo()

        today = QDate.currentDate()
        self.from_date_edit.setMaximumDate(today)
        self.to_date_edit.setMaximumDate(today)
        tracking_status = "On" if unsuspend_tracker.is_tracking_enabled() else "Off"
        scope_query = unsuspend_tracker.get_locked_scope_query()
        if scope_query is None:
            scope_text = "No scope locked"
        elif scope_query:
            scope_text = scope_query
        else:
            scope_text = "Whole collection"
        count = unsuspend_tracker.count_for_date_range(self.selected_date_range())

        self.tracking_label.setText(f"Tracking: {tracking_status}")
        self.scope_label.setText(f"Scope: {scope_text}")
        self.count_label.setText(f"Fresh captured: {count} card(s)")
        self.update_events_table()

    def update_events_table(self) -> None:
        if not is_widget_alive(self.events_table):
            return

        events = unsuspend_tracker.get_captured_events_for_date_range(
            self.selected_date_range()
        )
        self.events_table.setRowCount(len(events))

        for row, event in enumerate(events):
            sort_field = get_note_sort_field(event.nid)
            card_type = get_card_type_name(event.cid)
            values = [
                event.detected_at.strftime("%Y-%m-%d %H:%M:%S"),
                sort_field,
                card_type,
                str(event.cid),
                str(event.nid),
                event.scope_query or "Whole collection",
            ]

            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 3:
                    item.setData(Qt.ItemDataRole.UserRole, event.cid)
                self.events_table.setItem(row, column, item)

        self.events_table.resizeColumnsToContents()

    def show_events_table_context_menu(self, position) -> None:
        selected_card_ids = self.selected_event_card_ids()

        if not selected_card_ids:
            return

        menu = QMenu(self.events_table)
        remove_action = QAction("Remove fresh unsuspend", menu)
        remove_action.triggered.connect(
            lambda: self.remove_selected_fresh_unsuspends(selected_card_ids)
        )
        menu.addAction(remove_action)
        menu.exec(self.events_table.viewport().mapToGlobal(position))

    def selected_event_card_ids(self) -> list[int]:
        card_ids: set[int] = set()

        for item in self.events_table.selectedItems():
            row = item.row()
            card_id_item = self.events_table.item(row, 3)

            if card_id_item is None:
                continue

            card_id = card_id_item.data(Qt.ItemDataRole.UserRole)

            if card_id is None:
                continue

            card_ids.add(int(card_id))

        return sorted(card_ids)

    def remove_selected_fresh_unsuspends(self, card_ids: list[int]) -> None:
        removed_count = unsuspend_tracker.remove_captured_cids(card_ids)

        self.update_view()
        tooltip(f"Removed {removed_count} fresh unsuspend(s).")

    def lock_current_search(self) -> None:
        scope_query = normalize_scope_query(get_browser_search_text(self.browser))

        if not scope_query and not askUser(
            "The current search is empty. Locking an empty scope means tracking "
            "unsuspensions across the whole collection. Continue?"
        ):
            return

        suspended_cids = find_suspended_cids_in_scope(scope_query)
        unsuspend_tracker.lock_scope(scope_query, suspended_cids)
        self.update_view()
        tooltip(f"Locked scope with {len(suspended_cids)} suspended card(s).")

    def on_timer_tick(self) -> None:
        self.refresh(show_errors=False)

    def refresh(self, show_errors: bool = False) -> None:
        if not is_widget_alive(self):
            return

        if (
            not unsuspend_tracker.is_tracking_enabled()
            or unsuspend_tracker.get_locked_scope_query() is None
        ):
            self.update_view()
            return

        try:
            scope_query = unsuspend_tracker.get_locked_scope_query() or ""
            current_suspended_cids = find_suspended_cids_in_scope(scope_query)
            unsuspend_tracker.record_snapshot(
                current_suspended_cids=current_suspended_cids,
                cid_to_nid=cid_to_nid,
            )
        except Exception as exc:
            if show_errors:
                showInfo(f"Could not refresh unsuspend tracker:\n\n{exc}")

        self.update_view()

    def export_fresh_card_patch(self) -> None:
        date_range = self.selected_date_range()
        card_ids = unsuspend_tracker.get_captured_cids_for_date_range(date_range)

        if not card_ids:
            showInfo("No fresh unsuspended cards found for the selected window.")
            return

        try:
            rows = card_rows_from_card_ids(mw.col, card_ids)
        except Exception as exc:
            showInfo(f"Could not build ankipatch:\n\n{exc}")
            return

        preset = self.selected_preset_window()
        default_filename = (
            default_ankipatch_filename(preset)
            if preset is not None
            else default_ankipatch_filename_for_date_range(date_range)
        )
        selected_path, _filter = QFileDialog.getSaveFileName(
            self,
            "Save ankipatch",
            default_filename,
            "Anki patch (*.ankipatch)",
        )

        if not selected_path:
            return

        path = ensure_ankipatch_suffix(Path(selected_path))

        try:
            write_patch(path, AnkiPatch(cards=rows))
        except Exception as exc:
            showInfo(f"Could not save ankipatch:\n\n{exc}")
            return

        tooltip(f"Saved ankipatch with {len(rows)} fresh card(s).")

    def apply_patch_file(self) -> None:
        from .browser_actions import apply_ankipatch_from_file

        apply_ankipatch_from_file(self)

    def clear_captured(self) -> None:
        unsuspend_tracker.clear_captured()
        self.update_view()


def attach_unsuspend_tracker_widget(browser: Browser) -> None:
    ensure_active_tracker_profile()
    ensure_default_scope_locked()

    ensure_unsuspend_tracker_dock(browser, show=False)


def show_unsuspend_tracker_widget(browser: Browser) -> None:
    ensure_active_tracker_profile()
    ensure_default_scope_locked()
    ensure_unsuspend_tracker_dock(browser, show=True)


def ensure_unsuspend_tracker_dock(browser: Browser, show: bool) -> None:
    dock = getattr(browser, DOCK_ATTRIBUTE, None)

    if dock is not None and is_widget_alive(dock):
        widget = dock.widget()
        if isinstance(widget, UnsuspendTrackerWidget):
            _widgets.add(widget)
            widget.timer.start()
            widget.update_view()
        if show:
            dock.show()
            dock.raise_()
        return

    widget = UnsuspendTrackerWidget(browser)
    _widgets.add(widget)
    dock = QDockWidget("Share Tools", browser)
    dock.setObjectName("share_tools_unsuspend_tracker")
    dock.setWidget(widget)
    browser.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
    setattr(browser, DOCK_ATTRIBUTE, dock)

    if show:
        dock.show()
        dock.raise_()


def ensure_default_scope_locked() -> None:
    if unsuspend_tracker.get_locked_scope_query() is not None:
        return

    suspended_cids = find_suspended_cids_in_scope("")
    unsuspend_tracker.lock_scope("", suspended_cids)


def sync_tracker_baseline_to_current_scope() -> None:
    if (
        not unsuspend_tracker.is_tracking_enabled()
        or unsuspend_tracker.get_locked_scope_query() is None
    ):
        refresh_tracker_widget_views()
        return

    scope_query = unsuspend_tracker.get_locked_scope_query() or ""
    current_suspended_cids = find_suspended_cids_in_scope(scope_query)
    unsuspend_tracker.sync_baseline_without_capturing(current_suspended_cids)
    refresh_tracker_widget_views()


def refresh_tracker_widgets() -> None:
    for widget in list(_widgets):
        if not is_widget_alive(widget):
            unregister_tracker_widget(widget)
            continue

        widget.refresh(show_errors=False)


def refresh_tracker_widget_views() -> None:
    for widget in list(_widgets):
        if not is_widget_alive(widget):
            unregister_tracker_widget(widget)
            continue

        widget.update_view()


def unregister_tracker_widget(widget: "UnsuspendTrackerWidget") -> None:
    _widgets.discard(widget)

    if is_widget_alive(widget.timer):
        widget.timer.stop()


def is_widget_alive(widget: object) -> bool:
    try:
        return not sip.isdeleted(widget)
    except RuntimeError:
        return False


def get_browser_search_text(browser: Browser) -> str:
    current_search = getattr(browser, "current_search", None)

    if callable(current_search):
        return str(current_search()).strip()

    search_edit = getattr(getattr(browser, "form", None), "searchEdit", None)
    line_edit = search_edit.lineEdit() if search_edit is not None else None

    if line_edit is not None:
        return str(line_edit.text()).strip()

    return ""


def normalize_scope_query(query: str) -> str:
    parts = [
        part
        for part in query.strip().split()
        if part not in {"is:suspended", "-is:suspended"}
    ]
    return " ".join(parts)


def build_suspended_scope_query(scope_query: str) -> str:
    scope_query = scope_query.strip()

    if not scope_query:
        return "is:suspended"

    return f"({scope_query}) is:suspended"


def find_suspended_cids_in_scope(scope_query: str) -> list[int]:
    query = build_suspended_scope_query(scope_query)
    return sorted(int(cid) for cid in mw.col.find_cards(query))


def cid_to_nid(cid: int) -> int:
    return int(mw.col.get_card(cid).nid)


def get_note_sort_field(nid: int) -> str:
    try:
        note = mw.col.get_note(nid)
        sort_field = getattr(note, "sfld", None)

        if sort_field:
            return str(sort_field)

        model = note.note_type()
        sort_index = int(model.get("sortf", 0))
        return str(note.fields[sort_index])
    except Exception:
        return ""


def get_card_type_name(cid: int) -> str:
    try:
        card = mw.col.get_card(cid)
        template = card.template()

        if isinstance(template, dict):
            return str(template.get("name", ""))

        return str(getattr(template, "name", ""))
    except Exception:
        return ""


def default_share_tag_for_window(window: FreshnessWindow) -> str:
    now = datetime.now()

    if window == FreshnessWindow.THIS_WEEK:
        week_start = unsuspend_tracker.start_of_week(now).date()
        return f"share_unsuspended::week_{week_start.isoformat().replace('-', '_')}"

    return f"share_unsuspended::{now.date().isoformat().replace('-', '_')}"


def default_ankipatch_filename(window: FreshnessWindow) -> str:
    now = datetime.now()

    if window == FreshnessWindow.THIS_WEEK:
        week_start = unsuspend_tracker.start_of_week(now).date()
        return f"fresh_unsuspends_week_{week_start.isoformat()}.ankipatch"

    return f"fresh_unsuspends_{now.date().isoformat()}.ankipatch"


def default_ankipatch_filename_for_date_range(date_range: DateRange) -> str:
    return (
        f"fresh_unsuspends_{date_range.start.isoformat()}"
        f"_to_{date_range.end.isoformat()}.ankipatch"
    )


def profile_key_for_collection_path(collection_path: Union[str, Path]) -> str:
    normalized_path = os.path.abspath(os.fspath(collection_path))
    return hashlib.sha256(os.fsencode(normalized_path)).hexdigest()


def profile_database_path(profile_key: str) -> Path:
    return USER_FILES_DIR / "profiles" / profile_key / PROFILE_DATABASE_NAME


def get_active_profile_key() -> Optional[str]:
    return _active_profile_key


def activate_tracker_profile(collection_path: Union[str, Path]) -> Path:
    global _active_database_path, _active_profile_key

    profile_key = profile_key_for_collection_path(collection_path)
    database_path = profile_database_path(profile_key)

    if (
        _active_profile_key == profile_key
        and _active_database_path == database_path
    ):
        return database_path

    deactivate_tracker_profile()
    legacy_json_path = _claim_legacy_state(database_path, profile_key)
    unsuspend_tracker.initialize_storage(database_path, legacy_json_path)
    _active_profile_key = profile_key
    _active_database_path = database_path

    if legacy_json_path is not None:
        _write_legacy_claim_marker(profile_key)

    return database_path


def ensure_active_tracker_profile() -> Path:
    collection = getattr(mw, "col", None)
    collection_path = getattr(collection, "path", None)

    if collection_path is None:
        raise RuntimeError("Open an Anki profile before using the fresh-card tracker.")

    return activate_tracker_profile(collection_path)


def deactivate_tracker_profile() -> None:
    global _active_database_path, _active_profile_key

    stop_tracker_widgets()
    unsuspend_tracker.shutdown_storage(clear_runtime=True)
    _active_profile_key = None
    _active_database_path = None


def stop_tracker_widgets() -> None:
    for widget in list(_widgets):
        unregister_tracker_widget(widget)
    _widgets.clear()


def _claim_legacy_state(
    database_path: Path,
    profile_key: str,
) -> Optional[Path]:
    if LEGACY_CLAIM_MARKER.exists():
        return None

    if database_path.exists():
        _write_legacy_claim_marker(profile_key)
        return None

    if LEGACY_DATABASE_FILE.exists():
        source_state = TrackerDatabase(LEGACY_DATABASE_FILE).load()

        if source_state is None:
            _write_legacy_claim_marker(profile_key)
            return None

        database_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Optional[Path] = None

        try:
            with tempfile.NamedTemporaryFile(
                dir=database_path.parent,
                prefix=f".{PROFILE_DATABASE_NAME}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)

            destination = TrackerDatabase(temporary_path)
            destination.save(source_state)

            if destination.load() != source_state:
                raise RuntimeError(
                    "Legacy fresh-card state failed its migration round trip."
                )

            temporary_path.replace(database_path)
            temporary_path = None
            _write_legacy_claim_marker(profile_key)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        return None

    if LEGACY_STATE_FILE.exists():
        return LEGACY_STATE_FILE

    return None


def _write_legacy_claim_marker(profile_key: str) -> None:
    LEGACY_CLAIM_MARKER.parent.mkdir(parents=True, exist_ok=True)
    temporary_marker = LEGACY_CLAIM_MARKER.with_suffix(".tmp")
    temporary_marker.write_text(f"{profile_key}\n", encoding="ascii")
    temporary_marker.replace(LEGACY_CLAIM_MARKER)
